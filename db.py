# db.py — persistence layer for the SMS chatbot.
# All SQLite interactions live here. No other file writes SQL directly.
# Every function that reads or writes data accepts a business_id so records
# are always scoped to the correct business.

import sqlite3
import json
from pathlib import Path
from datetime import datetime

import logging
log = logging.getLogger("db")
log_config = logging.getLogger("config")

# Anchored to this file's directory, not the working directory. A relative
# path meant that running a script from anywhere else silently CREATED a
# second, empty chatbot.db there and seeded into it — no error, no warning,
# and the real database untouched. A database you can create by accident is
# a database you can lose work in.
DB_PATH = Path(__file__).resolve().parent / "chatbot.db"


def get_connection():
    """Open a SQLite connection with dict-style row access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables if they don't exist. Safe to call on every startup."""
    conn = get_connection()

    # businesses — one row per client business. The lookup table that lets a
    # single server serve many businesses. twilio_number is nullable so a
    # business can exist in the DB before it has a Twilio number assigned
    # (e.g. a web-chat-only client, or during setup).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS businesses (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT    NOT NULL,
            slug           TEXT    UNIQUE NOT NULL,
            twilio_number  TEXT    UNIQUE,
            config_path    TEXT    NOT NULL,
            active         INTEGER DEFAULT 1,
            calendar_sync_token TEXT,
            documents_dirty INTEGER DEFAULT 0
        )
    """)

    # users — one row per person who can log into the admin.
    # business_id is NULL for operators (us), who see every business.
    # Owners have a business_id and are scoped to that one business.
    # Passwords are stored as bcrypt hashes, never plaintext.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            email          TEXT    UNIQUE NOT NULL,
            password_hash  TEXT    NOT NULL,
            business_id    INTEGER,
            is_operator    INTEGER DEFAULT 0,
            active         INTEGER DEFAULT 1,
            created_at     TEXT    NOT NULL,
            FOREIGN KEY (business_id) REFERENCES businesses(id)
        )
    """)
    # messages — append-only log of every turn per (phone, business).
    # business_id scopes history so Bob's customers never appear in the
    # bakery's conversation window and vice versa.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id  INTEGER NOT NULL,
            phone        TEXT    NOT NULL,
            role         TEXT    NOT NULL,
            content      TEXT    NOT NULL,
            source       TEXT    DEFAULT 'unknown',
            timestamp    TEXT    NOT NULL,
            FOREIGN KEY (business_id) REFERENCES businesses(id)
        )
    """)

    # appointments — one row per booked appointment/order.
    # The external_* fields record where this booking was mirrored (e.g. a
    # Google Calendar event), so it can be updated or removed there later.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id        INTEGER NOT NULL,
            phone              TEXT    NOT NULL,
            service            TEXT    NOT NULL,
            datetime           TEXT    NOT NULL,
            status             TEXT    DEFAULT 'booked',
            details            TEXT    DEFAULT '{}',
            external_event_id  TEXT,
            external_calendar  TEXT,
            sync_status        TEXT    DEFAULT 'none',
            calendar_changed   TEXT,
            FOREIGN KEY (business_id) REFERENCES businesses(id)
        )
    """)

    # conversation_state — exactly one row per (phone, business_id) pair.
    # Composite primary key means the same phone can have independent booking
    # flows with two different businesses simultaneously. INSERT OR REPLACE
    # updates in place when state changes.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversation_state (
            phone           TEXT NOT NULL,
            business_id     INTEGER NOT NULL,
            state           TEXT NOT NULL DEFAULT 'idle',
            pending_booking TEXT NOT NULL DEFAULT '{}',
            last_updated    TEXT NOT NULL,
            PRIMARY KEY (phone, business_id),
            FOREIGN KEY (business_id) REFERENCES businesses(id)
        )
    """)

    # config_overrides — owner-editable settings that override the YAML.
    # YAML is the initial state (onboarding); this table is the current state.
    # Storing overrides rather than a full config copy means a business
    # inherits any YAML improvements it hasn't explicitly overridden, and
    # keeps the difference between "as onboarded" and "as edited" visible.
    # `field` is a dotted path into the config: "business.phone",
    # "bot.persona_preset". `value` is JSON so lists (services, faq, etc) work
    # alongside plain strings.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS config_overrides (
            business_id  INTEGER NOT NULL,
            field        TEXT    NOT NULL,
            value        TEXT    NOT NULL,
            updated_at   TEXT    NOT NULL,
            updated_by   TEXT,
            PRIMARY KEY (business_id, field),
            FOREIGN KEY (business_id) REFERENCES businesses(id)
        )
    """)

    # documents — the RAG knowledge base, one row per section.
    # Markdown files under documents/<slug>/ are the seed; this table is
    # authoritative once imported. Same relationship YAML has to
    # config_overrides, and for the same reason: the deployed filesystem is
    # ephemeral, so anything an owner edits must live in the database.
    # Storing sections separately rather than one blob means chunk boundaries
    # are exactly section boundaries — more predictable than inferring them
    # from a regex over free-form Markdown.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id  INTEGER NOT NULL,
            position     INTEGER NOT NULL DEFAULT 0,
            title        TEXT    NOT NULL,
            body         TEXT    NOT NULL,
            updated_at   TEXT    NOT NULL,
            updated_by   TEXT,
            FOREIGN KEY (business_id) REFERENCES businesses(id)
        )
    """)

    # document_versions — previous content, kept on every save.
    # A bad document edit degrades every answer and, unlike a wrong phone
    # number, gives no obvious sign of what broke. Being able to see and
    # revert previous versions is cheap insurance.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS document_versions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id  INTEGER NOT NULL,
            title        TEXT    NOT NULL,
            body         TEXT    NOT NULL,
            saved_at     TEXT    NOT NULL,
            saved_by     TEXT,
            FOREIGN KEY (document_id) REFERENCES documents(id)
        )
    """)

    # geocode_cache — addresses already resolved, so a repeat booking to the
    # same street doesn't pay for a second lookup. Keyed by the query plus
    # its viewport bias, because the same string biased differently is a
    # different question. A row with result NULL is a remembered miss.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS geocode_cache (
            query      TEXT PRIMARY KEY,
            result     TEXT,
            fetched_at TEXT NOT NULL
        )
    """)

    # geocode_usage — how many geocoding calls each business has spent today.
    # The limit lives here rather than in Google's console because a vendor
    # quota is per-project, often not adjustable, and tells you about it by
    # failing inside a customer's booking. This one is per-business, always
    # adjustable, and resets at midnight by virtue of the date being part of
    # the key — no cleanup job, and yesterday's rows are free history.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS geocode_usage (
            usage_key TEXT    NOT NULL,
            day       TEXT    NOT NULL,
            calls     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (usage_key, day)
        )
    """)

    # rate_limits — fixed-window counters for public endpoints. In SQLite
    # rather than memory because gunicorn workers don't share memory and the
    # container restarts; a counter that resets with the process is not a
    # limit.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_limits (
            bucket       TEXT    NOT NULL,
            window_start INTEGER NOT NULL,
            count        INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (bucket, window_start)
        )
    """)

    # --- migrations -------------------------------------------------------
    # CREATE TABLE IF NOT EXISTS is a no-op once a table exists, so a new
    # column on an existing table needs an explicit ALTER. Guarded by an
    # inspection rather than a try/except so a genuine error still surfaces.
    _add_column_if_missing(conn, "appointments", "address_check", "TEXT")

    # Demo tenancy. A visitor to the public demo gets their own throwaway
    # business, cloned from a template, so they can edit settings and watch
    # the bot change without touching a real client's data. These columns are
    # what make such a row tellable from a real one:
    #   is_demo        the flag every listing and destructive path checks
    #   demo_of        which business it was cloned from
    #   created_at     when the clone was made
    #   last_seen_at   last visitor activity — what the idle sweep reads
    #   rag_collection the Chroma collection this business retrieves from.
    #     A fresh clone points at its template's collection, so cloning costs
    #     no embedding calls; it moves to one of its own only if the visitor
    #     publishes a knowledge-base change. Reading the collection name from
    #     a column rather than deriving it means two businesses can share a
    #     collection deliberately, which deriving it could never express.
    _add_column_if_missing(conn, "businesses", "is_demo", "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "businesses", "demo_of", "INTEGER")
    _add_column_if_missing(conn, "businesses", "created_at", "TEXT")
    _add_column_if_missing(conn, "businesses", "last_seen_at", "TEXT")
    _add_column_if_missing(conn, "businesses", "rag_collection", "TEXT")

    # A revision counter per conversation, so a slow turn can't overwrite a
    # fast one. Every write bumps it; a writer that read revision N refuses
    # to write once someone else has made it N+1. Without this, one request
    # that hung for two minutes on a calendar call came back and restored a
    # two-minute-old booking, silently undoing three of the customer's
    # answers. See notes/debugging_lessons.md.
    _add_column_if_missing(conn, "conversation_state", "revision",
                           "INTEGER NOT NULL DEFAULT 0")

    conn.commit()
    conn.close()
    log.info("Database ready.")


def _add_column_if_missing(conn, table, column, declaration):
    """Add a column to an existing table, once. Safe to run on every boot."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column in existing:
        return
    log.info("Adding column %s.%s", table, column)
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


# ---------------------------------------------------------------------------
# Business lookup
# ---------------------------------------------------------------------------

def get_business_by_number(twilio_number):
    """Return the active business row matching a Twilio 'To' number, or None.

    Used by the /sms (and future /voice) endpoint to identify which business
    a webhook is for.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM businesses WHERE twilio_number = ? AND active = 1",
        (twilio_number,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_business_by_slug(slug):
    """Return the active business row matching a URL slug, or None.

    Used by the /webchat/<slug> endpoint to identify the business from the URL.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM businesses WHERE slug = ? AND active = 1",
        (slug,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def add_business(name, slug, config_path, twilio_number=None, *,
                 is_demo=False, demo_of=None, rag_collection=None):
    """Register a new business and return its id.

    twilio_number is optional — a business can be added before its Twilio
    number is assigned (e.g. during setup or for web-chat-only clients).

    The keyword-only arguments are for demo clones (see demo.py) and default
    to the values a real client wants, so no existing caller changes.
    rag_collection defaults to the slug — the behaviour before the column
    existed — rather than to NULL, so nothing has to cope with a missing one.
    """
    from datetime import datetime as _dt
    conn = get_connection()
    cursor = conn.execute(
        """
        INSERT INTO businesses (name, slug, twilio_number, config_path,
                                is_demo, demo_of, rag_collection,
                                created_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (name, slug, twilio_number, config_path,
         1 if is_demo else 0, demo_of, rag_collection or slug,
         _dt.now().isoformat(), _dt.now().isoformat())
    )
    business_id = cursor.lastrowid
    conn.commit()
    conn.close()
    log.info(f"Registered business: '{name}' (id={business_id}, slug={slug})")
    return business_id

def clear_config_override(business_id, field):
    """Remove an override so the field falls back to the YAML value."""
    conn = get_connection()
    conn.execute(
        "DELETE FROM config_overrides WHERE business_id = ? AND field = ?",
        (business_id, field)
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def save_message(phone, role, content, business_id, source="unknown"):
    """Append one message turn to the log.

    `source` records which layer produced an assistant reply — 'rule',
    'llm', or 'scheduler' — so we can later measure how much traffic each
    layer handles. Customer messages use 'customer'.
    """
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO messages (business_id, phone, role, content, source, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (business_id, phone, role, content, source, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_recent_messages(phone, business_id, limit=10):
    """Return the last `limit` turns for this (phone, business) as a list of
    {role, content} dicts — in chronological order, ready for the Claude API.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT role, content FROM messages
        WHERE phone = ? AND business_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (phone, business_id, limit)
    ).fetchall()
    conn.close()
    # Fetched newest-first; reverse so Claude sees chronological order.
    rows = list(reversed(rows))
    return [{"role": r["role"], "content": r["content"]} for r in rows]


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------

def save_appointment(phone, service, when, business_id, details=None,
                     external_event_id=None, external_calendar=None,
                     sync_status="none", address_check=None):
    """Write a completed booking.

    The external_* fields record where this booking was mirrored, so it can
    be updated or removed there later (e.g. on cancellation).
    """
    import json as _json
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO appointments
            (business_id, phone, service, datetime, status, details,
             external_event_id, external_calendar, sync_status, address_check)
        VALUES (?, ?, ?, ?, 'booked', ?, ?, ?, ?, ?)
        """,
        (business_id, phone, service, when, _json.dumps(details or {}),
         external_event_id, external_calendar, sync_status,
         _json.dumps(address_check) if address_check else None)
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Conversation state
# ---------------------------------------------------------------------------

def get_state(phone, business_id):
    """Return the current booking state for this (phone, business) pair.

    Returns {'state': 'idle', 'pending': {}} if no row exists yet — the
    default starting state for any new customer.
    """
    conn = get_connection()
    row = conn.execute(
        """
        SELECT state, pending_booking, revision FROM conversation_state
        WHERE phone = ? AND business_id = ?
        """,
        (phone, business_id)
    ).fetchone()
    conn.close()
    if row:
        return {
            "state": row["state"],
            "pending": json.loads(row["pending_booking"]),
            "revision": row["revision"] or 0,
        }
    # revision 0 for a conversation that doesn't exist yet: a writer holding
    # 0 will still lose to anyone who has since created the row, because
    # creating it writes revision 1.
    return {"state": "idle", "pending": {}, "revision": 0}

# ---------------------------------------------------------------------------
# Admin queries
# ---------------------------------------------------------------------------

def get_business_by_id(business_id):
    """Return the business row for an id, or None.

    Exists so the config loader can stamp a business's immutable slug onto
    the config it hands out.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM businesses WHERE id = ?", (business_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_businesses(include_demo=False):
    """Return businesses ordered by name. Used by the admin dashboard.

    Demo clones are excluded by default. An operator's dashboard lists every
    business there is, and a public demo can mint one per visitor — left in,
    the list of real clients would be buried under throwaways within a day.
    Callers that genuinely want them (the idle sweep, operator tooling) ask.
    """
    conn = get_connection()
    sql = "SELECT * FROM businesses"
    if not include_demo:
        sql += " WHERE COALESCE(is_demo, 0) = 0"
    rows = conn.execute(sql + " ORDER BY name").fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Demo tenancy
# ---------------------------------------------------------------------------
# Rows created for a visitor to the public demo, and the machinery for taking
# them away again. Everything here refuses to touch a business that isn't
# flagged as a demo.

def touch_business(business_id):
    """Record that someone just interacted with this business.

    The idle sweep reads this column, so anything a visitor does that should
    keep their demo alive has to come through here. Kept cheap — one UPDATE,
    no read — because it runs on every inbound message.
    """
    from datetime import datetime as _dt
    conn = get_connection()
    conn.execute("UPDATE businesses SET last_seen_at = ? WHERE id = ?",
                 (_dt.now().isoformat(), business_id))
    conn.commit()
    conn.close()


def set_rag_collection(business_id, collection):
    """Point a business at a vector collection by name.

    Used when a demo clone stops sharing its template's collection — see
    demo.fork_collection. Not owner-editable and not exposed in settings: a
    business that retrieves from the wrong collection answers confidently out
    of someone else's knowledge base.
    """
    conn = get_connection()
    conn.execute("UPDATE businesses SET rag_collection = ? WHERE id = ?",
                 (collection, business_id))
    conn.commit()
    conn.close()
    log.info("Business %s now retrieves from collection %r",
             business_id, collection)


def get_demo_businesses(idle_before=None):
    """Return demo businesses, optionally only those idle since a timestamp.

    idle_before is an ISO timestamp string; rows whose last_seen_at is older
    (or missing, which means a clone that was never used) come back. String
    comparison is correct here because ISO timestamps sort lexically — the
    one property of that format worth relying on.
    """
    conn = get_connection()
    sql = "SELECT * FROM businesses WHERE COALESCE(is_demo, 0) = 1"
    params = []
    if idle_before is not None:
        sql += " AND COALESCE(last_seen_at, created_at, '') < ?"
        params.append(idle_before)
    rows = conn.execute(sql + " ORDER BY id", params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# Every table that stores rows scoped to one business, and the column that
# scopes them. Listed explicitly rather than discovered, so that adding a
# tenant-scoped table without thinking about teardown shows up as a review
# question here instead of as a demo that leaves rows behind forever.
_TENANT_TABLES = (
    ("messages",           "business_id"),
    ("appointments",       "business_id"),
    ("conversation_state", "business_id"),
    ("config_overrides",   "business_id"),
    ("users",              "business_id"),
)


def delete_business(business_id, *, force=False):
    """Delete a demo business and every row scoped to it. Returns the counts.

    Refuses anything not flagged is_demo unless force is passed, and nothing
    in the sweep passes force. This fails closed on purpose, for the same
    reason the calendar's tenancy filter does: refusing wrongly leaves a stale
    demo row lying about, deleting wrongly destroys a client's whole history.
    Those are not the same size of mistake, so the code should not treat them
    as one.

    SQLite does not enforce foreign keys unless asked per-connection, so the
    cascade here is written out rather than assumed. document_versions hangs
    off documents by id, not business_id, so it goes first — after the
    documents rows are gone there is no way left to find it.
    """
    row = get_business_by_id(business_id)
    if not row:
        return None
    if not row.get("is_demo") and not force:
        log.warning("Refused to delete business %s (%s) — not a demo",
                    business_id, row["name"])
        return None

    conn = get_connection()
    deleted = {}

    doc_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM documents WHERE business_id = ?", (business_id,)
    )]
    if doc_ids:
        marks = ",".join("?" * len(doc_ids))
        cur = conn.execute(
            f"DELETE FROM document_versions WHERE document_id IN ({marks})",
            doc_ids)
        deleted["document_versions"] = cur.rowcount
    cur = conn.execute("DELETE FROM documents WHERE business_id = ?",
                       (business_id,))
    deleted["documents"] = cur.rowcount

    for table, column in _TENANT_TABLES:
        cur = conn.execute(f"DELETE FROM {table} WHERE {column} = ?",
                           (business_id,))
        deleted[table] = cur.rowcount

    # geocode_usage is keyed by the business id as a string, not an integer
    # column — it also accepts a slug, so the key is text.
    cur = conn.execute("DELETE FROM geocode_usage WHERE usage_key = ?",
                       (str(business_id),))
    deleted["geocode_usage"] = cur.rowcount

    # geocode_cache is deliberately left alone: it is keyed by the address
    # asked about, not by who asked, and a resolved address stays resolved.
    # rate_limits is left to prune_rate_limits — its rows expire by window.

    cur = conn.execute("DELETE FROM businesses WHERE id = ?", (business_id,))
    deleted["businesses"] = cur.rowcount

    conn.commit()
    conn.close()
    log.info("Deleted business %s (%s): %s", business_id, row["slug"],
             ", ".join(f"{k}={v}" for k, v in deleted.items() if v))
    return deleted

def get_config_overrides(business_id):
    """Return {dotted_field: value} of owner edits for this business."""
    import json as _json
    conn = get_connection()
    rows = conn.execute(
        "SELECT field, value FROM config_overrides WHERE business_id = ?",
        (business_id,)
    ).fetchall()
    conn.close()
    return {r["field"]: _json.loads(r["value"]) for r in rows}


def set_config_override(business_id, field, value, updated_by=None):
    """Store an owner edit. Upsert — one row per (business, field)."""
    import json as _json
    from datetime import datetime as _dt

    conn = get_connection()
    conn.execute(
        """
        INSERT OR REPLACE INTO config_overrides
            (business_id, field, value, updated_at, updated_by)
        VALUES (?, ?, ?, ?, ?)
        """,
        (business_id, field, _json.dumps(value), _dt.now().isoformat(), updated_by)
    )
    conn.commit()
    conn.close()
    log_config.info(f"{updated_by or 'unknown'} set {field} for business {business_id}")


def get_conversation_list(business_id):
    """Return one summary row per unique phone number for a business.

    Each row includes the phone number, total message count, timestamp of
    the last message, and a preview of the last message content.
    Used by the admin conversation list view.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            phone,
            COUNT(*) as message_count,
            MAX(timestamp) as last_message_time,
            (
                SELECT content FROM messages m2
                WHERE m2.phone = m1.phone AND m2.business_id = m1.business_id
                ORDER BY id DESC LIMIT 1
            ) as last_content
        FROM messages m1
        WHERE business_id = ?
        GROUP BY phone
        ORDER BY last_message_time DESC
        """,
        (business_id,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_conversation(business_id, phone):
    """Return all messages for a specific (business, phone) pair in order.

    Used by the admin conversation detail view.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT role, content, timestamp FROM messages
        WHERE business_id = ? AND phone = ?
        ORDER BY id ASC
        """,
        (business_id, phone)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_appointments(business_id, status=None):
    """Return all appointments for a business, newest first.

    Optional `status` filter ('booked', 'cancelled', etc.). None returns all.
    Used by the admin appointments view.
    """
    conn = get_connection()
    if status:
        rows = conn.execute(
            """
            SELECT * FROM appointments
            WHERE business_id = ? AND status = ?
            ORDER BY datetime DESC
            """,
            (business_id, status)
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM appointments
            WHERE business_id = ?
            ORDER BY datetime DESC
            """,
            (business_id,)
        ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def set_state(phone, business_id, state, pending=None, expect_revision=None):
    """Upsert the booking state for this (phone, business) pair.

    Returns the new revision number, or None if the write was refused.

    expect_revision is optimistic concurrency. Pass the revision you read at
    the start of the turn and the write only lands if nobody has written
    since; pass None and it writes unconditionally, which is what callers
    outside a conversation turn want.

    Why it exists: one web-chat request hung for 116 seconds inside a
    Google Calendar call. While it hung the customer sent three more
    messages, each of which was handled correctly. Then the slow one
    finished and wrote its two-minute-old pending dict over the top,
    deleting two answers the customer had given in the meantime and asking
    a question they'd already answered. A last-writer-wins state machine
    isn't a state machine, it's a race.
    """
    if pending is None:
        pending = {}
    conn = get_connection()
    try:
        if expect_revision is not None:
            row = conn.execute(
                "SELECT revision FROM conversation_state "
                "WHERE phone = ? AND business_id = ?",
                (phone, business_id),
            ).fetchone()
            current = (row["revision"] or 0) if row else 0
            if current != expect_revision:
                log.warning(
                    "Refusing a stale state write for %s/%s "
                    "(held revision %s, current is %s)",
                    business_id, phone, expect_revision, current)
                return None
            new_revision = current + 1
        else:
            row = conn.execute(
                "SELECT revision FROM conversation_state "
                "WHERE phone = ? AND business_id = ?",
                (phone, business_id),
            ).fetchone()
            new_revision = ((row["revision"] or 0) if row else 0) + 1

        conn.execute(
            """
            INSERT OR REPLACE INTO conversation_state
                (phone, business_id, state, pending_booking, last_updated,
                 revision)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (phone, business_id, state, json.dumps(pending),
             datetime.now().isoformat(), new_revision)
        )
        conn.commit()
        return new_revision
    finally:
        conn.close()

def get_appointment(appointment_id):
    """Return a single appointment as a dict, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM appointments WHERE id = ?", (appointment_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def cancel_appointment(appointment_id):
    """Mark an appointment cancelled. Soft delete — the row stays for the record."""
    conn = get_connection()
    conn.execute(
        "UPDATE appointments SET status = 'cancelled', sync_status = 'deleted' "
        "WHERE id = ?",
        (appointment_id,)
    )
    conn.commit()
    conn.close()

def get_appointment_by_event_id(event_id):
    """Find the appointment mirroring a given calendar event, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM appointments WHERE external_event_id = ?", (event_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def mark_calendar_change(appointment_id, description):
    """Record that this appointment was changed in the calendar, not the bot."""
    conn = get_connection()
    conn.execute(
        "UPDATE appointments SET calendar_changed = ? WHERE id = ?",
        (description, appointment_id)
    )
    conn.commit()
    conn.close()


def reschedule_appointment(appointment_id, new_datetime):
    """Change an appointment's time."""
    conn = get_connection()
    conn.execute(
        "UPDATE appointments SET datetime = ? WHERE id = ?",
        (new_datetime, appointment_id)
    )
    conn.commit()
    conn.close()

def get_sync_token(business_id):
    """Return the stored calendar sync token, or None for a full resync."""
    conn = get_connection()
    row = conn.execute(
        "SELECT calendar_sync_token FROM businesses WHERE id = ?", (business_id,)
    ).fetchone()
    conn.close()
    return row["calendar_sync_token"] if row else None


def set_sync_token(business_id, token):
    """Store the sync token returned by the last calendar poll."""
    conn = get_connection()
    conn.execute(
        "UPDATE businesses SET calendar_sync_token = ? WHERE id = ?",
        (token, business_id)
    )
    conn.commit()
    conn.close()

def create_user(email, password, business_id=None, is_operator=False):
    """Create a user with a bcrypt-hashed password.
    business_id=None + is_operator=True → operator, sees all businesses.
    business_id=N   + is_operator=False → owner, scoped to that business.
    """
    import bcrypt
    from datetime import datetime as _dt

    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())

    conn = get_connection()
    cursor = conn.execute(
        """
        INSERT INTO users (email, password_hash, business_id, is_operator, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (email.lower().strip(), pw_hash.decode("utf-8"), business_id,
            1 if is_operator else 0, _dt.now().isoformat())
    )
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()
    log.info(f"Created user {email} (id={user_id}, operator={is_operator})")
    return user_id


def get_user_by_email(email):
    """Return an active user by email, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE email = ? AND active = 1",
        (email.lower().strip(),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def verify_password(email, password):
    """Return the user dict if credentials are valid, else None.

    Always runs a bcrypt check even when the user doesn't exist, so the
    response time doesn't reveal which emails are registered — a timing
    attack that's cheap to prevent and awkward to retrofit.
    """
    import bcrypt

    user = get_user_by_email(email)
    if not user:
        # Dummy hash to keep timing consistent.
        bcrypt.checkpw(b"dummy", bcrypt.hashpw(b"dummy", bcrypt.gensalt()))
        return None

    if bcrypt.checkpw(password.encode("utf-8"),
                      user["password_hash"].encode("utf-8")):
        return user
    return None

def get_documents(business_id):
    """Return this business's document sections in display order."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM documents WHERE business_id = ? ORDER BY position, id",
        (business_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_document_section(business_id, title, body, position=None,
                         updated_by=None):
    """Add a section. Appends to the end unless a position is given."""
    from datetime import datetime as _dt
    conn = get_connection()
    if position is None:
        row = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM documents "
            "WHERE business_id = ?", (business_id,)
        ).fetchone()
        position = row["p"]
    cursor = conn.execute(
        """
        INSERT INTO documents (business_id, position, title, body,
                               updated_at, updated_by)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (business_id, position, title, body, _dt.now().isoformat(), updated_by)
    )
    doc_id = cursor.lastrowid
    conn.execute("UPDATE businesses SET documents_dirty = 1 WHERE id = ?",
                 (business_id,))
    conn.commit()
    conn.close()
    return doc_id


def update_document_section(document_id, title, body, updated_by=None):
    """Update a section, keeping the previous content as a version."""
    from datetime import datetime as _dt
    conn = get_connection()

    old = conn.execute("SELECT * FROM documents WHERE id = ?",
                       (document_id,)).fetchone()
    if not old:
        conn.close()
        return

    conn.execute(
        """
        INSERT INTO document_versions (document_id, title, body, saved_at, saved_by)
        VALUES (?, ?, ?, ?, ?)
        """,
        (document_id, old["title"], old["body"], old["updated_at"],
         old["updated_by"])
    )
    conn.execute(
        "UPDATE documents SET title = ?, body = ?, updated_at = ?, updated_by = ? "
        "WHERE id = ?",
        (title, body, _dt.now().isoformat(), updated_by, document_id)
    )
    conn.execute("UPDATE businesses SET documents_dirty = 1 WHERE id = ?",
                 (old["business_id"],))
    conn.commit()
    conn.close()


def delete_document_section(document_id):
    """Remove a section. Versions are kept — they reference the id only."""
    conn = get_connection()
    row = conn.execute("SELECT business_id FROM documents WHERE id = ?",
                       (document_id,)).fetchone()
    if not row:
        conn.close()
        return
    conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    conn.execute("UPDATE businesses SET documents_dirty = 1 WHERE id = ?",
                 (row["business_id"],))
    conn.commit()
    conn.close()


def get_document_versions(document_id, limit=10):
    """Return recent previous versions of a section, newest first."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM document_versions WHERE document_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (document_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_documents_clean(business_id):
    """Clear the dirty flag after a successful re-ingest."""
    conn = get_connection()
    conn.execute("UPDATE businesses SET documents_dirty = 0 WHERE id = ?",
                 (business_id,))
    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------
# Geocode cache
# ---------------------------------------------------------------------------

def get_cached_geocode(query):
    """Return {'result': <dict or None>} if this query was looked up before.

    The wrapper dict distinguishes "never asked" (None) from "asked, and the
    answer was no match" ({'result': None}) — without it, every remembered
    miss would be looked up again forever.
    """
    import json as _json
    conn = get_connection()
    row = conn.execute(
        "SELECT result FROM geocode_cache WHERE query = ?", (query,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    if not row["result"]:
        return {"result": None, "detail": None}
    stored = _json.loads(row["result"])
    # Rows written before the detail was cached hold the bare result dict.
    if isinstance(stored, dict) and "result" in stored:
        return stored
    return {"result": stored, "detail": None}


def save_cached_geocode(query, result, detail=None):
    """Remember a lookup, hit or miss, along with why it missed."""
    import json as _json
    from datetime import datetime
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO geocode_cache (query, result, fetched_at)
        VALUES (?, ?, ?)
        ON CONFLICT(query) DO UPDATE SET
            result = excluded.result, fetched_at = excluded.fetched_at
        """,
        (query, _json.dumps({"result": result, "detail": detail}),
         datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Geocode usage
# ---------------------------------------------------------------------------

def reserve_geocode_call(usage_key, day):
    """Count one geocoding call against a business's day, returning the new total.

    Increment-then-check rather than check-then-increment: the UPSERT is a
    single atomic statement, so two gunicorn workers can't both read "99"
    and both decide they're clear. The cost of that ordering is that
    attempts made after the limit is reached still increment, so the number
    can exceed the limit — it becomes a count of attempts rather than of
    calls. That's the more useful number anyway when someone is hammering
    the endpoint.
    """
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO geocode_usage (usage_key, day, calls)
        VALUES (?, ?, 1)
        ON CONFLICT(usage_key, day) DO UPDATE SET calls = calls + 1
        """,
        (usage_key, day)
    )
    # Read back on the SAME connection before committing. The INSERT already
    # took SQLite's write lock, so no other worker can slip between these two
    # statements — the same guarantee RETURNING would give, without depending
    # on the SQLite version that happens to be bundled wherever this runs.
    row = conn.execute(
        "SELECT calls FROM geocode_usage WHERE usage_key = ? AND day = ?",
        (usage_key, day)
    ).fetchone()
    conn.commit()
    conn.close()
    return row["calls"]


def get_geocode_usage(usage_key, day):
    """Calls already counted for this business today."""
    conn = get_connection()
    row = conn.execute(
        "SELECT calls FROM geocode_usage WHERE usage_key = ? AND day = ?",
        (usage_key, day)
    ).fetchone()
    conn.close()
    return row["calls"] if row else 0


def prune_rate_limits(older_than_seconds=172800):
    """Drop counters from closed windows. Returns how many rows went.

    Returning the count rather than nothing so the caller can say whether it
    did anything — housekeeping that runs silently is housekeeping nobody
    can tell has stopped running.
    """
    import time
    conn = get_connection()
    cursor = conn.execute("DELETE FROM rate_limits WHERE window_start < ?",
                          (int(time.time()) - older_than_seconds,))
    removed = cursor.rowcount
    conn.commit()
    conn.close()
    return removed

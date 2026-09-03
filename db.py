# db.py — persistence layer for the SMS chatbot.
# All SQLite interactions live here. No other file writes SQL directly.
# Every function that reads or writes data accepts a business_id so records
# are always scoped to the correct business.

import sqlite3
import json
from pathlib import Path
from datetime import datetime

DB_PATH = Path("chatbot.db")


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

    conn.commit()
    conn.close()
    print("[db] Database ready.")


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


def add_business(name, slug, config_path, twilio_number=None):
    """Register a new business and return its id.

    twilio_number is optional — a business can be added before its Twilio
    number is assigned (e.g. during setup or for web-chat-only clients).
    """
    conn = get_connection()
    cursor = conn.execute(
        """
        INSERT INTO businesses (name, slug, twilio_number, config_path)
        VALUES (?, ?, ?, ?)
        """,
        (name, slug, twilio_number, config_path)
    )
    business_id = cursor.lastrowid
    conn.commit()
    conn.close()
    print(f"[db] Registered business: '{name}' (id={business_id}, slug={slug})")
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
                     sync_status="none"):
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
             external_event_id, external_calendar, sync_status)
        VALUES (?, ?, ?, ?, 'booked', ?, ?, ?, ?)
        """,
        (business_id, phone, service, when, _json.dumps(details or {}),
         external_event_id, external_calendar, sync_status)
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
        SELECT state, pending_booking FROM conversation_state
        WHERE phone = ? AND business_id = ?
        """,
        (phone, business_id)
    ).fetchone()
    conn.close()
    if row:
        return {
            "state": row["state"],
            "pending": json.loads(row["pending_booking"])
        }
    return {"state": "idle", "pending": {}}

# ---------------------------------------------------------------------------
# Admin queries
# ---------------------------------------------------------------------------

def get_all_businesses():
    """Return all businesses ordered by name. Used by the admin dashboard."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM businesses ORDER BY name"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

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
    print(f"[config] {updated_by or 'unknown'} set {field} for business {business_id}")


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


def set_state(phone, business_id, state, pending=None):
    """Upsert the booking state for this (phone, business) pair.

    INSERT OR REPLACE overwrites the existing row if one exists, or creates
    a new one. Always call this after advancing the state machine.
    """
    if pending is None:
        pending = {}
    conn = get_connection()
    conn.execute(
        """
        INSERT OR REPLACE INTO conversation_state
            (phone, business_id, state, pending_booking, last_updated)
        VALUES (?, ?, ?, ?, ?)
        """,
        (phone, business_id, state, json.dumps(pending), datetime.now().isoformat())
    )
    conn.commit()
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
    print(f"[db] Created user {email} (id={user_id}, operator={is_operator})")
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
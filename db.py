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
            active         INTEGER DEFAULT 1
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


def reschedule_appointment(appointment_id, new_datetime):
    """Change an appointment's time."""
    conn = get_connection()
    conn.execute(
        "UPDATE appointments SET datetime = ? WHERE id = ?",
        (new_datetime, appointment_id)
    )
    conn.commit()
    conn.close()
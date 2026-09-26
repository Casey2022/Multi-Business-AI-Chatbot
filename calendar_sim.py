# calendar_sim.py — a calendar that lives in the database.
#
# The sandbox demo can't use Google. A stranger's bookings must not land in a
# real calendar, the service account must not be reachable from a public
# page, and a demo that depends on someone else's API is a demo that breaks
# on their bad day.
#
# So this backend answers the same questions from the appointments table.
# Everything that decides WHETHER a slot works — business hours, blackouts,
# buffers, capacity, the search for alternatives — comes from scheduling.py,
# the same module the Google backend uses. Only two things differ here:
# busy time is read with SQL instead of an API call, and "writing an event"
# means handing back an id nobody will look up.
#
# That split is the point. A demo whose availability logic is a second
# implementation would drift from production and quietly start lying about
# what the product does.

import logging
from datetime import datetime, timedelta

import scheduling

log = logging.getLogger("calendar")

PROVIDER = "simulated"


def is_enabled(config):
    """Enabled whenever the business config asks for this provider.

    No calendar_id needed — there's no external calendar to point at.
    """
    return bool((config.get("calendar") or {}).get("enabled", True))


def _busy_periods(config, window_start, window_end):
    """Booked appointments in a window, as (start, end) datetimes.

    The database is the diary. Cancelled appointments don't hold time, and a
    business only collides with its own bookings — hence the business_id
    filter, which is the multi-tenant guarantee restated in SQL.
    """
    business_id = (config.get("business") or {}).get("id")
    if business_id is None:
        # Better to see no bookings than someone else's. A config loaded
        # without a business_id has no tenant to scope to, and guessing here
        # would mean one demo visitor blocking another's calendar.
        log.warning("Simulated calendar asked for busy time with no business "
                    "id — returning none rather than every business's")
        return []
    from db import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT datetime, service FROM appointments
            WHERE status = 'booked'
              AND (? IS NULL OR business_id = ?)
              AND datetime >= ? AND datetime <= ?
            """,
            (business_id, business_id,
             window_start.strftime("%Y-%m-%d %H:%M"),
             window_end.strftime("%Y-%m-%d %H:%M")),
        ).fetchall()
    finally:
        conn.close()

    busy = []
    for row in rows:
        try:
            start = datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            continue          # a malformed row shouldn't block every booking
        # Each booking is as long as ITS OWN service, not the business
        # default. A salon where a two-hour colour blocked forty-five
        # minutes would cheerfully double-book the other seventy-five.
        minutes = scheduling.duration_for(config, row["service"])
        busy.append((start, start + timedelta(minutes=minutes)))
    return busy


# ---------------------------------------------------------------------------
# The same questions, answered from the same rules
# ---------------------------------------------------------------------------

def is_slot_available(config, start_iso, busy=None, service=None):
    if busy is None:
        start = datetime.strptime(start_iso, "%Y-%m-%d %H:%M")
        # The window has to start far enough back to catch a booking that
        # began before this slot and is still running. A day of look-back
        # covers any plausible appointment length with room to spare; the
        # day it doesn't, the business has bigger problems than this query.
        busy = _busy_periods(config, start - timedelta(days=1),
                             start + timedelta(days=1))
    return scheduling.is_slot_available(config, start_iso, busy, service)


def find_alternatives(config, desired_iso, service=None):
    desired = datetime.strptime(desired_iso, "%Y-%m-%d %H:%M")
    busy = _busy_periods(config, desired - timedelta(days=8),
                         desired + timedelta(days=8))
    return scheduling.find_alternatives(config, desired_iso, busy, service)


def slot_rejection_reason(config, start_iso, busy=None, service=None):
    if busy is None:
        start = datetime.strptime(start_iso, "%Y-%m-%d %H:%M")
        busy = _busy_periods(config, start - timedelta(days=1),
                             start + timedelta(days=1))
    return scheduling.slot_rejection_reason(config, start_iso, busy, service)


# ---------------------------------------------------------------------------
# Writes — the appointment row IS the event
# ---------------------------------------------------------------------------

def create_event(config, service_name, start_iso, customer_id, details=None,
                 customer_name=None):
    """Return an event id without writing anywhere.

    The scheduler saves the appointment row immediately after this returns,
    and that row is what _busy_periods reads back — so the booking really
    does hold the slot against the next customer. The id exists only because
    the interface promises one.
    """
    event_id = f"sim-{start_iso.replace(' ', 'T')}-{customer_id}"
    log.info("Simulated calendar: booked %s at %s", service_name, start_iso)
    return event_id


def delete_event(config, event_id):
    """Nothing to delete — cancelling sets the row's status, which is enough."""
    log.info("Simulated calendar: released %s", event_id)
    return True


def update_event_time(config, event_id, new_start_iso, service=None):
    """Nothing to move — rescheduling rewrites the row's datetime."""
    log.info("Simulated calendar: moved %s to %s", event_id, new_start_iso)
    return True


def fetch_changes(config, sync_token=None):
    """No owner edits to reconcile: nobody can change this calendar but us."""
    return [], None

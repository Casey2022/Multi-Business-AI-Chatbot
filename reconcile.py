# reconcile.py — pull owner-made calendar changes into the database.
#
# One-directional by design: the CALENDAR is authoritative for scheduling,
# the database is authoritative for conversations and metadata. When they
# disagree about a time, the calendar wins.
#
# Customers are told (2026-09-28): a move or cancellation made in Google
# Calendar goes through the same notify step as the portal's buttons. This
# used to say it deliberately didn't notify, because there was no way to
# send anything; notify.py is that way.

from datetime import datetime
from zoneinfo import ZoneInfo

import calendar_sync
from config import load_config
from db import (get_sync_token, set_sync_token, get_appointment_by_event_id,
                cancel_appointment, reschedule_appointment, mark_calendar_change)
from notify import notify_change

import logging
log = logging.getLogger("reconcile")


def reconcile_business(business):
    """Pull calendar changes for one business into the database.

    Returns a list of human-readable descriptions of what changed.
    """
    # With the business id, so its overrides apply and, above all, a demo
    # copy gets its demo config: without it a demo of Bob's read Bob's REAL
    # Google Calendar (the template file's settings) and could apply those
    # changes to the real business's appointments.
    config = load_config(business["config_path"], business["id"])
    if not calendar_sync.is_enabled(config):
        return []

    token = get_sync_token(business["id"])
    events, next_token = calendar_sync.fetch_changes(config, token)

    if events is None:                      # token expired
        events, next_token = calendar_sync.fetch_changes(config, None)
        if events is None:
            return []

    tz      = ZoneInfo(config["calendar"].get("timezone", "America/New_York"))
    changes = []

    for event in events:
        appt = get_appointment_by_event_id(event.get("id"),
                                           business_id=business["id"])
        if not appt:
            # An event with no matching appointment — the owner created it
            # by hand, or it predates sync. Availability checking already
            # respects it; there's nothing to reconcile.
            continue

        if event.get("status") == "cancelled":
            if appt["status"] != "cancelled":
                cancel_appointment(appt["id"])
                note = f"Cancelled in calendar on {datetime.now(tz):%b %d at %-I:%M %p}"
                mark_calendar_change(appt["id"], note)
                notify_change(appt, config, "cancelled")
                changes.append(f"#{appt['id']} {appt['service']} — cancelled")
            continue

        start_raw = event.get("start", {}).get("dateTime")
        if not start_raw:
            continue                        # all-day event

        new_start = (datetime.fromisoformat(start_raw)
                     .astimezone(tz).replace(tzinfo=None)
                     .strftime("%Y-%m-%d %H:%M"))

        if new_start != appt["datetime"]:
            reschedule_appointment(appt["id"], new_start)
            note = (f"Moved in calendar from {appt['datetime']} "
                    f"on {datetime.now(tz):%b %d at %-I:%M %p}")
            mark_calendar_change(appt["id"], note)
            notify_change(appt, config, "moved", new_datetime=new_start)
            changes.append(
                f"#{appt['id']} {appt['service']} — moved to {new_start}"
            )

    if next_token:
        set_sync_token(business["id"], next_token)

    if changes:
        log.info(f"{business['name']}: {len(changes)} change(s)")
        for c in changes:
            log.debug(f"  {c}")

    return changes


# When each business was last reconciled, in this process. Reconciling is
# one Google call (usually returning nothing), but the customer's chat checks
# for notices every 10 seconds, so those checks are throttled.
_last_run = {}


def maybe_reconcile(business, min_interval=60):
    """Reconcile unless this business was reconciled in the last min_interval
    seconds. Never raises. Returns the changes, or [] when skipped.

    Called from every portal page that shows appointments (the list, the
    calendar, an appointment) and from the customer's chat window. Until
    2026-09-28 only the appointments list ran it: an owner who moved a
    booking in Google Calendar and then looked at the portal's calendar saw
    the old time, and the customer was never told.
    """
    import time
    now = time.monotonic()
    last = _last_run.get(business["id"])
    if last is not None and now - last < min_interval:
        return []
    _last_run[business["id"]] = now
    try:
        return reconcile_business(business)
    except Exception as e:
        log.warning("Reconcile failed for %s: %s", business.get("name"), e)
        return []

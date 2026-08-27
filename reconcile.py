# reconcile.py — pull owner-made calendar changes into the database.
#
# One-directional by design: the CALENDAR is authoritative for scheduling,
# the database is authoritative for conversations and metadata. When they
# disagree about a time, the calendar wins.
#
# Deliberately does NOT notify customers. Telling someone their appointment
# moved requires outbound messaging this system doesn't have, and a silent
# reschedule is a product decision, not an oversight — see future_directions.

from datetime import datetime
from zoneinfo import ZoneInfo

import calendar_sync
from config import load_config
from db import (get_sync_token, set_sync_token, get_appointment_by_event_id,
                cancel_appointment, reschedule_appointment, mark_calendar_change)


def reconcile_business(business):
    """Pull calendar changes for one business into the database.

    Returns a list of human-readable descriptions of what changed.
    """
    config = load_config(business["config_path"])
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
        appt = get_appointment_by_event_id(event.get("id"))
        if not appt:
            # An event with no matching appointment — the owner created it
            # by hand, or it predates sync. Availability checking already
            # respects it; there's nothing to reconcile.
            continue

        if event.get("status") == "cancelled":
            if appt["status"] != "cancelled":
                cancel_appointment(appt["id"])
                note = f"Cancelled in calendar on {datetime.now():%b %d at %-I:%M %p}"
                mark_calendar_change(appt["id"], note)
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
                    f"on {datetime.now():%b %d at %-I:%M %p}")
            mark_calendar_change(appt["id"], note)
            changes.append(
                f"#{appt['id']} {appt['service']} — moved to {new_start}"
            )

    if next_token:
        set_sync_token(business["id"], next_token)

    if changes:
        print(f"[reconcile] {business['name']}: {len(changes)} change(s)")
        for c in changes:
            print(f"  {c}")

    return changes
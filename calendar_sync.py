# calendar_sync.py — Google Calendar integration.
#
# Creates events on a business's calendar when a booking is finalized.
# Authentication uses a single service account; each business shares its
# calendar with that account's email and puts the calendar ID in its config.
#
# Design rule: this module raises on failure rather than swallowing errors.
# The caller decides what to tell the customer — we never want a silent
# failure that lets the bot confirm a booking that isn't on the calendar.

import os
import json
from datetime import datetime, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

_service = None


def _get_service():
    """Return an authenticated Calendar API client, building it lazily.

    Lazy for the same reason the ChromaDB client is: this must be created
    inside the worker process, not inherited across a gunicorn fork.
    """
    global _service
    if _service is not None:
        return _service

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not set")

    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=SCOPES
    )
    # cache_discovery=False avoids a noisy warning and a filesystem cache
    # we don't want on an ephemeral container.
    _service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return _service


def is_enabled(config):
    """True if this business has calendar sync configured."""
    cal = config.get("calendar", {})
    return bool(cal.get("enabled") and cal.get("calendar_id"))


def create_event(config, service_name, start_iso, customer_id, details=None):
    """Create a calendar event for a booking. Returns the Google event ID.

    Raises on any failure — the caller must not confirm the booking unless
    this returns successfully.

    start_iso: "YYYY-MM-DD HH:MM" (the format the scheduler stores)
    details:   dict of extra-question answers, written into the description
    """
    cal      = config["calendar"]
    business = config["business"]
    tz       = cal.get("timezone", "America/New_York")
    duration = int(cal.get("default_duration_minutes", 60))

    start = datetime.strptime(start_iso, "%Y-%m-%d %H:%M")
    end   = start + timedelta(minutes=duration)

    # Build a readable description from the booking details.
    lines = [f"Booked via {business['name']} assistant.", f"Customer: {customer_id}"]
    for key, value in (details or {}).items():
        lines.append(f"{key.replace('_', ' ').title()}: {value}")

    event = {
        "summary": f"{service_name} — {customer_id}",
        "description": "\n".join(lines),
        "start": {"dateTime": start.isoformat(), "timeZone": tz},
        "end":   {"dateTime": end.isoformat(),   "timeZone": tz},
    }

    created = _get_service().events().insert(
        calendarId=cal["calendar_id"],
        body=event,
    ).execute()

    event_id = created.get("id")
    print(f"[calendar] Created event {event_id} for {service_name} at {start_iso}")
    return event_id
# calendar_google.py — the Google Calendar backend.
#
# One of two backends behind calendar_sync. Everything here talks to a real
# Google calendar; the simulated backend mirrors this module's public
# functions exactly, so callers never learn which one they got.
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
from zoneinfo import ZoneInfo

import scheduling

from config import question_labels, humanize

import logging
log = logging.getLogger("calendar")

# calendar.events covers creating/updating events; freebusy queries need
# the broader calendar scope. Both are required for availability checking.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]

_service = None

# Seconds before a Calendar API call gives up. Without a timeout, httplib2
# waits on the socket indefinitely: one request sat inside an availability
# check for 116 seconds, and everything the customer typed while it hung was
# later overwritten by its stale view of the conversation. A booking that
# fails fast is recoverable; one that hangs is not. Tunable because a slow
# network shouldn't mean no calendar at all.
API_TIMEOUT_SECONDS = int(os.environ.get("CALENDAR_TIMEOUT_SECONDS", "10"))


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
    #
    # The transport is built by hand only to put a timeout on it. If the
    # httplib2 pieces aren't importable we fall back to the default
    # transport rather than failing to build a client at all — a calendar
    # that might hang still beats no calendar — but say so, because that
    # fallback is the configuration the 116-second stall happened in.
    try:
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp
        http = AuthorizedHttp(creds,
                              http=httplib2.Http(timeout=API_TIMEOUT_SECONDS))
        _service = build("calendar", "v3", http=http, cache_discovery=False)
        log.info("Calendar client ready (timeout %ds)", API_TIMEOUT_SECONDS)
    except ImportError as e:
        log.warning("Building the calendar client without a timeout (%s) — "
                    "a hung request will block a customer's turn", e)
        _service = build("calendar", "v3", credentials=creds,
                         cache_discovery=False)
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
    duration = scheduling.duration_for(config, service_name)

    start = datetime.strptime(start_iso, "%Y-%m-%d %H:%M")
    end   = start + timedelta(minutes=duration)

    # Build a readable description from the booking details.
    lines = [f"Booked via {business['name']} assistant.", f"Customer: {customer_id}"]
    labels = question_labels(config)
    for key, value in (details or {}).items():
        lines.append(f"{labels.get(key) or humanize(key)}: {value}")

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
    log.info(f"Created event {event_id} for {service_name} at {start_iso}")
    return event_id

def _busy_periods(config, window_start, window_end):
    """Return [(start, end)] for each event in the window, as naive local datetimes.

    Uses events().list rather than freebusy().query because freeBusy merges
    overlapping intervals — it reports WHETHER the calendar is busy, not how
    many events overlap. The capacity model needs the count, so we need the
    individual events.

    All-day events are skipped: they have a 'date' rather than a 'dateTime'
    and represent notes rather than bookings.
    """
    cal = config["calendar"]
    tz  = ZoneInfo(cal.get("timezone", "America/New_York"))

    result = _get_service().events().list(
        calendarId   = cal["calendar_id"],
        timeMin      = window_start.replace(tzinfo=tz).isoformat(),
        timeMax      = window_end.replace(tzinfo=tz).isoformat(),
        singleEvents = True,        # expand recurring events into instances
        orderBy      = "startTime",
        maxResults   = 250,).execute()

    periods = []
    for event in result.get("items", []):
        # Cancelled events remain in the list with status 'cancelled'.
        if event.get("status") == "cancelled":
            continue

        start_raw = event["start"].get("dateTime")
        end_raw   = event["end"].get("dateTime")
        if not start_raw or not end_raw:
            continue    # all-day event

        # Convert INTO the business timezone, then drop tzinfo so these are
        # comparable to the naive local datetimes the scheduler uses.
        start = datetime.fromisoformat(start_raw).astimezone(tz).replace(tzinfo=None)
        end   = datetime.fromisoformat(end_raw).astimezone(tz).replace(tzinfo=None)
        periods.append((start, end))

    return periods



# The scheduling rules live in scheduling.py, shared with the simulated
# backend. What stays here is the one thing that is genuinely Google's: where
# the busy periods come from. Signatures are unchanged, so every caller and
# the calendar_sync facade carry on as before.

def is_slot_available(config, start_iso, busy=None, service=None):
    """True if a booking can be made at start_iso. Fetches busy if not given."""
    if busy is None:
        start = datetime.strptime(start_iso, "%Y-%m-%d %H:%M")
        busy = _busy_periods(config, start - timedelta(days=1),
                             start + timedelta(days=1))
    return scheduling.is_slot_available(config, start_iso, busy, service)


def find_alternatives(config, desired_iso, service=None):
    """Up to max_alternatives nearby openings, mixing earlier and later."""
    desired = datetime.strptime(desired_iso, "%Y-%m-%d %H:%M")
    # One events call covers the whole search window.
    busy = _busy_periods(config, desired - timedelta(days=8),
                         desired + timedelta(days=8))
    return scheduling.find_alternatives(config, desired_iso, busy, service)


def slot_rejection_reason(config, start_iso, busy=None, service=None):
    """Why a slot is unavailable: 'past', 'closed', 'blackout', 'conflict', or None."""
    if busy is None:
        start = datetime.strptime(start_iso, "%Y-%m-%d %H:%M")
        busy = _busy_periods(config, start - timedelta(days=1),
                             start + timedelta(days=1))
    return scheduling.slot_rejection_reason(config, start_iso, busy, service)


def delete_event(config, event_id):
    """Remove an event from the business's calendar.

    A 410 (Gone) means the event is already deleted — the desired end state
    is already true, so that counts as success. The business owner may well
    have deleted it on their phone before touching the admin.
    """
    from googleapiclient.errors import HttpError

    cal = config["calendar"]
    try:
        _get_service().events().delete(
            calendarId=cal["calendar_id"],
            eventId=event_id,
        ).execute()
        log.info(f"Deleted event {event_id}")
    except HttpError as e:
        # Only 410 Gone means "this event existed and is already deleted" —
        # the desired end state is true, so treat it as success.
        #
        # 404 is NOT safe to forgive: it also fires when the CALENDAR itself
        # can't be found (bad calendar_id), and swallowing that would report
        # a successful cancellation while the real event stays on the owner's
        # calendar.
        if e.resp.status == 410:
            log.warning(f"Event {event_id} already deleted — treating as success")
            return
        raise


def update_event_time(config, event_id, new_start_iso, service=None):
    """Move an existing event to a new time. Returns the updated event id.

    service keeps the moved event its original length — without it, a
    rescheduled two-hour job would silently shrink to the default.
    """
    cal      = config["calendar"]
    tz       = cal.get("timezone", "America/New_York")
    duration = scheduling.duration_for(config, service)

    start = datetime.strptime(new_start_iso, "%Y-%m-%d %H:%M")
    end   = start + timedelta(minutes=duration)

    updated = _get_service().events().patch(
        calendarId = cal["calendar_id"],
        eventId    = event_id,
        body = {
            "start": {"dateTime": start.isoformat(), "timeZone": tz},
            "end":   {"dateTime": end.isoformat(),   "timeZone": tz},
        },
    ).execute()

    log.info(f"Moved event {event_id} to {new_start_iso}")
    return updated.get("id")

def fetch_changes(config, sync_token=None):
    """Fetch calendar changes since the last poll.

    Returns (events, next_sync_token). With no token, returns all upcoming
    events and a fresh token — that's the initial sync. With a token, returns
    only what changed since it was issued, which is usually nothing.

    A 410 means the token has expired (Google keeps them for about a week);
    the caller should retry with sync_token=None for a full resync.
    """
    from googleapiclient.errors import HttpError

    cal = config["calendar"]
    tz  = ZoneInfo(cal.get("timezone", "America/New_York"))

    params = {
        "calendarId":   cal["calendar_id"],
        "singleEvents": True,
        "maxResults":   250,
    }
    if sync_token:
        params["syncToken"] = sync_token
    else:
        # Initial sync: only care about events from now forward.
        params["timeMin"] = datetime.now().replace(tzinfo=tz).isoformat()

    try:
        result = _get_service().events().list(**params).execute()
    except HttpError as e:
        if e.resp.status == 410:
            log.warning("Sync token expired — full resync needed")
            return None, None
        raise

    return result.get("items", []), result.get("nextSyncToken")
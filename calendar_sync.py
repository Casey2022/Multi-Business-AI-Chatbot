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
from zoneinfo import ZoneInfo

# calendar.events covers creating/updating events; freebusy queries need
# the broader calendar scope. Both are required for availability checking.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]

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

def _sched(config):
    """Scheduling settings with sensible defaults."""
    s = config.get("calendar", {}).get("scheduling", {})
    return {
        "model":            s.get("model", "exclusive"),
        "slots_per_time":   int(s.get("slots_per_time", 1)),
        "buffer_minutes":   int(s.get("buffer_minutes", 0)),
        "granularity":      int(s.get("slot_granularity", 30)),
        "max_alternatives": int(s.get("max_alternatives", 3)),
        "business_hours":   s.get("business_hours", {}),
        "blackout":         s.get("blackout", []),
    }


def _within_business_hours(dt, duration_min, sched):
    """True if [dt, dt+duration] falls inside open hours and outside blackouts."""
    hours = sched["business_hours"]
    # YAML keys may parse as ints or strings depending on quoting.
    day = hours.get(dt.weekday(), hours.get(str(dt.weekday())))
    if not day:
        return False

    open_t  = datetime.strptime(day[0], "%H:%M").time()
    close_t = datetime.strptime(day[1], "%H:%M").time()
    end     = dt + timedelta(minutes=duration_min)

    if dt.time() < open_t or end.time() > close_t or end.date() != dt.date():
        return False

    for window in sched["blackout"]:
        if dt.weekday() not in window.get("days", []):
            continue
        b_start = datetime.strptime(window["start"], "%H:%M").time()
        b_end   = datetime.strptime(window["end"],   "%H:%M").time()
        # Overlap if the appointment starts before the blackout ends
        # and ends after it starts.
        if dt.time() < b_end and end.time() > b_start:
            return False

    return True


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
        maxResults   = 250,
    ).execute()

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


def is_slot_available(config, start_iso, busy=None):
    """True if a booking can be made at start_iso.

    Checks business hours, blackout windows, and existing bookings according
    to the business's scheduling model:
      - exclusive: no overlapping event, plus buffer_minutes clearance
      - capacity:  fewer than slots_per_time overlapping events

    `busy` can be passed in to avoid repeated API calls when checking many
    candidate slots.
    """
    cal      = config["calendar"]
    sched    = _sched(config)
    duration = int(cal.get("default_duration_minutes", 60))

    start = datetime.strptime(start_iso, "%Y-%m-%d %H:%M")
    end   = start + timedelta(minutes=duration)

    if not _within_business_hours(start, duration, sched):
        return False

    if busy is None:
        busy = _busy_periods(config, start - timedelta(days=1),
                             start + timedelta(days=1))

    if sched["model"] == "capacity":
        overlapping = sum(1 for b_start, b_end in busy
                          if start < b_end and end > b_start)
        return overlapping < sched["slots_per_time"]

    # exclusive: extend the window by the buffer on both sides
    buf     = timedelta(minutes=sched["buffer_minutes"])
    padded_s = start - buf
    padded_e = end + buf
    for b_start, b_end in busy:
        if padded_s < b_end and padded_e > b_start:
            return False
    return True


def find_alternatives(config, desired_iso):
    """Return up to max_alternatives available slots, mixing earlier and later.

    Searches forward and backward independently, then interleaves the results
    so the customer sees options on both sides of what they asked for. A pure
    outward walk can exhaust the quota in one direction — e.g. when the rest
    of the day is blocked, every suggestion ends up earlier — which reads as
    unhelpful even though each slot is genuinely the nearest available.
    """
    sched   = _sched(config)
    step    = timedelta(minutes=sched["granularity"])
    wanted  = sched["max_alternatives"]
    desired = datetime.strptime(desired_iso, "%Y-%m-%d %H:%M")

    # One events call covers the whole search window.
    busy = _busy_periods(config, desired - timedelta(days=8),
                         desired + timedelta(days=8))

    def search(direction, limit):
        """Walk one direction, collecting available slots."""
        out = []
        for i in range(1, 337):          # 336 half-hour steps ≈ 7 days
            candidate = desired + (step * i * direction)
            if candidate < datetime.now():
                continue
            iso = candidate.strftime("%Y-%m-%d %H:%M")
            if is_slot_available(config, iso, busy=busy):
                out.append(iso)
                if len(out) >= limit:
                    break
        return out

    # Ask each direction for the full quota, then interleave. If one side
    # comes up short, the other fills the gap.
    later   = search(+1, wanted)
    earlier = search(-1, wanted)

    mixed = []
    for i in range(wanted):
        if i < len(later):
            mixed.append(later[i])
        if i < len(earlier):
            mixed.append(earlier[i])

    # Truncate BEFORE sorting. Sorting first would order all candidates
    # chronologically and then keep the earliest few, which throws away
    # the later-side options the interleave was built to preserve.
    seen = []
    for iso in mixed:
        if iso not in seen:
            seen.append(iso)
        if len(seen) >= wanted:
            break

    return sorted(seen)
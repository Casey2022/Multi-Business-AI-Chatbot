# scheduling.py — when a business can take a booking.
#
# Pure arithmetic over a config and a list of busy periods. No network, no
# database, no knowledge of where the busy list came from — Google, SQLite,
# or a test fixture all look the same from here.
#
# This module exists because the two calendar backends disagree about almost
# nothing. Business hours, blackout windows, buffers, capacity, and the
# search for alternatives are the business's rules, identical whoever is
# holding the diary. Only two things are backend-specific: where busy time
# comes from, and where a new event is written. Keeping the rules here means
# the simulated backend can't quietly drift from the real one — a demo that
# schedules differently from production is a demo that lies.

import logging
from datetime import datetime, timedelta

import clock

log = logging.getLogger("scheduling")


def _normalise(name):
    """Lowercase, single-spaced — for comparing two spellings of a service."""
    return " ".join(str(name or "").lower().split())


def duration_for(config, service=None):
    """How many minutes this service occupies. The single place that decides.

    A business has one default length and, optionally, exceptions:

        calendar:
          default_duration_minutes: 45
          service_durations:
            "color": 120
            "cut and color": 150

    Matching is exact once case and spacing are normalised, deliberately not
    the containment matching the booking flow uses on a customer's words.
    The service reaching this function has already been snapped to the
    catalogue, and containment here would let "cut" claim the 150 minutes
    belonging to "cut and color" — an hour and a half of a stylist's day
    given away by a substring.

    An unknown service falls back to the default rather than raising: an
    unbookable service is a worse outcome than a slightly wrong length, and
    unknown_duration_services() below is what makes the mismatch visible.
    """
    cal     = config.get("calendar") or {}
    default = int(cal.get("default_duration_minutes", 60))
    table   = cal.get("service_durations") or {}
    if not service or not table:
        return default

    wanted = _normalise(service)
    for name, minutes in table.items():
        if _normalise(name) == wanted:
            try:
                return int(minutes)
            except (TypeError, ValueError):
                log.warning("service_durations[%r] is %r, not a number — "
                            "using the default %d", name, minutes, default)
                return default
    return default


def same_service(a, b):
    """True when two service names mean the same service.

    The same exact-once-normalised rule duration_for uses, exposed so that
    anything deciding "is this booking's service the one the owner named"
    gets the same answer. Two notions of service identity in one codebase is
    how a job ends up asked one set of questions and scheduled for the
    length of another.
    """
    return _normalise(a) == _normalise(b)


def unknown_condition_services(config):
    """Services named by a booking question that this business doesn't offer.

    Same failure as unknown_duration_services, one layer over: a question
    conditioned on "water heater repair" when the catalogue says "water
    heater installation" is never asked, for anyone, forever. Nothing errors
    and nothing looks wrong -- the owner just never sees the answer they
    thought they were collecting. Startup calls this so it's a log line.
    """
    offered  = {_normalise(s) for s in (config.get("services") or [])}
    stale    = []
    for q in ((config.get("booking") or {}).get("extra_questions") or []):
        for name in (q.get("services") or []):
            if _normalise(name) not in offered:
                stale.append((q.get("key") or "?", name))
    return stale


def unknown_notice_services(config):
    """Notice entries (booking.notice) for services the business doesn't
    offer. policy.notice_for treats a service it can't find as needing no
    notice, so a typo or a renamed service fails open, silently: startup
    logs these, the way it logs durations nobody matches."""
    table = (config.get("booking") or {}).get("notice") or {}
    offered = {_normalise(s) for s in (config.get("services") or [])}
    return [name for name in table if _normalise(name) not in offered]


def unknown_duration_services(config):
    """service_durations keys that name no service this business offers.

    A duration whose key is a typo, or whose service was renamed in settings,
    does nothing at all — the booking silently gets the default length and
    nothing anywhere says why. Startup calls this so the mismatch is a log
    line rather than a mystery.
    """
    table = (config.get("calendar") or {}).get("service_durations") or {}
    offered = {_normalise(s) for s in (config.get("services") or [])}
    return [name for name in table if _normalise(name) not in offered]


def settings(config):
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
        # Dated blocks the owner set on the Calendar page (booking_control),
        # put here by config.load_config: [(start, end)] datetimes.
        "blocks":           _parse_blocks(s.get("blocks", [])),
    }


def _parse_blocks(raw):
    out = []
    for b in raw or []:
        try:
            out.append((datetime.strptime(b["start"], "%Y-%m-%d %H:%M"),
                        datetime.strptime(b["end"], "%Y-%m-%d %H:%M")))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _in_block(start, end, sched):
    """True if [start, end) touches time the owner blocked off."""
    return any(start < b_end and end > b_start for b_start, b_end in sched.get("blocks", []))


_DAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_DAY_PLURAL = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays",
               "Saturdays", "Sundays")


def weekly_hours(table):
    """The weekly hours as {weekday 0-6: ("HH:MM", "HH:MM")}, whatever the
    keys are (YAML ints, or strings from a saved override)."""
    out = {}
    for day, span in (table or {}).items():
        try:
            day = int(day)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6 and span and len(span) == 2:
            out[day] = (str(span[0]), str(span[1]))
    return out


def _clock(hhmm):
    """ "07:00" -> "7am", "09:30" -> "9:30am", "22:00" -> "10pm"."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    suffix = "am" if hour < 12 else "pm"
    hour = hour % 12 or 12
    return f"{hour}:{minute:02d}{suffix}" if minute else f"{hour}{suffix}"


def hours_text(table):
    """The hours as customers are told them, made from the weekly table:
    "Tue-Sun, 7am-3pm (closed Mondays)". The table is the only copy
    (YAML review, 2026-10-23): the text used to be typed separately, owners
    could edit it and not the table, and an owner who changed one told
    customers one closing time while bookings followed the other.

    Days in a row with the same hours are one group; groups are separated
    by "; ". Closed days are named after, starting from the day after the
    last open one ("closed Sun & Mon"), except a Monday-Friday business's
    weekend, which "Mon-Fri" already says."""
    week = weekly_hours(table)
    if not week:
        return ""
    groups = []
    for day in range(7):
        if day not in week:
            continue
        if groups and groups[-1]["last"] == day - 1 and groups[-1]["span"] == week[day]:
            groups[-1]["last"] = day
        else:
            groups.append({"first": day, "last": day, "span": week[day]})
    parts = []
    for g in groups:
        days = (_DAY_SHORT[g["first"]] if g["first"] == g["last"]
                else f"{_DAY_SHORT[g['first']]}-{_DAY_SHORT[g['last']]}")
        parts.append(f"{days}, {_clock(g['span'][0])}-{_clock(g['span'][1])}")
    text = "; ".join(parts)
    last_open = max(week)
    closed = [(last_open + i) % 7 for i in range(1, 8) if (last_open + i) % 7 not in week]
    if not closed or (set(closed) == {5, 6} and set(week) == {0, 1, 2, 3, 4}):
        return text
    if len(closed) == 1:
        return f"{text} (closed {_DAY_PLURAL[closed[0]]})"
    names = [_DAY_SHORT[d] for d in closed]
    return f"{text} (closed {', '.join(names[:-1])} & {names[-1]})"


def within_business_hours(dt, duration_min, sched):
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

    if _in_block(dt, end, sched):
        return False

    return True


def is_slot_available(config, start_iso, busy, service=None, details=None):
    """True if a booking can be made at start_iso, given the busy periods.

    Honours the business's scheduling model:
      - exclusive: no overlapping event, plus buffer_minutes clearance
      - capacity:  fewer than slots_per_time overlapping events

    service decides how long the booking runs; omitting it means the
    business's default length. details are the order's own words, for a
    notice variant they name (policy.notice_for).
    """
    sched    = settings(config)
    duration = duration_for(config, service)

    start = datetime.strptime(start_iso, "%Y-%m-%d %H:%M")
    end   = start + timedelta(minutes=duration)

    # Past times are the parser's most likely mistake ("Thursday" said on a
    # Thursday). find_alternatives already skips past candidates; this makes
    # the same guarantee for a directly requested time.
    if start < clock.business_now(config):
        return False

    if not within_business_hours(start, duration, sched):
        return False

    # Inside the service's notice, or after its order-by cutoff
    # (notes/booking_notice.md). Here, so find_alternatives never offers one.
    import policy
    if policy.too_soon(service, start, config, details=details):
        return False

    if sched["model"] == "capacity":
        overlapping = sum(1 for b_start, b_end in busy
                          if start < b_end and end > b_start)
        return overlapping < sched["slots_per_time"]

    # exclusive: extend the window by the buffer on both sides
    buf      = timedelta(minutes=sched["buffer_minutes"])
    padded_s = start - buf
    padded_e = end + buf
    for b_start, b_end in busy:
        if padded_s < b_end and padded_e > b_start:
            return False
    return True


def find_alternatives(config, desired_iso, busy, service=None, details=None):
    """Return up to max_alternatives available slots, mixing earlier and later.

    Searches forward and backward independently, then interleaves the results
    so the customer sees options on both sides of what they asked for. A pure
    outward walk can exhaust the quota in one direction — e.g. when the rest
    of the day is blocked, every suggestion ends up earlier — which reads as
    unhelpful even though each slot is genuinely the nearest available.
    """
    sched   = settings(config)
    step    = timedelta(minutes=sched["granularity"])
    wanted  = sched["max_alternatives"]
    desired = datetime.strptime(desired_iso, "%Y-%m-%d %H:%M")
    now     = clock.business_now(config)   # once, not 672 times

    # A notice longer than the week searched (a wedding cake's two weeks)
    # would find nothing near what was asked: search from the earliest time
    # the notice allows instead, on the step grid.
    import policy
    late = policy.too_soon(service, desired, config, now, details)
    if late and late["why"] == "notice":
        earliest = late["earliest"].replace(second=0, microsecond=0)
        minutes = (earliest.hour * 60 + earliest.minute) % sched["granularity"]
        if minutes:
            earliest += timedelta(minutes=sched["granularity"] - minutes)
        desired = max(desired, earliest - step)

    def search(direction, limit):
        """Walk one direction, collecting available slots."""
        out = []
        for i in range(1, 337):          # 336 half-hour steps ≈ 7 days
            candidate = desired + (step * i * direction)
            if candidate < now:
                continue
            iso = candidate.strftime("%Y-%m-%d %H:%M")
            if is_slot_available(config, iso, busy, service, details):
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


def slot_rejection_reason(config, start_iso, busy, service=None, details=None):
    """Why a slot is unavailable: 'past', 'closed', 'blackout', 'blocked'
    (the owner blocked it off), 'notice' or 'cutoff' (the service's notice,
    notes/booking_notice.md), 'conflict', or None."""
    sched    = settings(config)
    duration = duration_for(config, service)

    start = datetime.strptime(start_iso, "%Y-%m-%d %H:%M")
    end   = start + timedelta(minutes=duration)

    if start < clock.business_now(config):
        return "past"

    hours = sched["business_hours"]
    day   = hours.get(start.weekday(), hours.get(str(start.weekday())))
    if not day:
        return "closed"

    open_t  = datetime.strptime(day[0], "%H:%M").time()
    close_t = datetime.strptime(day[1], "%H:%M").time()
    if start.time() < open_t or end.time() > close_t or end.date() != start.date():
        return "closed"

    for window in sched["blackout"]:
        if start.weekday() not in window.get("days", []):
            continue
        b_start = datetime.strptime(window["start"], "%H:%M").time()
        b_end   = datetime.strptime(window["end"],   "%H:%M").time()
        if start.time() < b_end and end.time() > b_start:
            return "blackout"

    if _in_block(start, end, sched):
        return "blocked"

    import policy
    late = policy.too_soon(service, start, config, details=details)
    if late:
        return late["why"]                  # "notice" or "cutoff"

    if not is_slot_available(config, start_iso, busy, service, details):
        return "conflict"

    return None

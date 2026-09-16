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

from datetime import datetime, timedelta


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
    }


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

    return True


def is_slot_available(config, start_iso, busy):
    """True if a booking can be made at start_iso, given the busy periods.

    Honours the business's scheduling model:
      - exclusive: no overlapping event, plus buffer_minutes clearance
      - capacity:  fewer than slots_per_time overlapping events
    """
    cal      = config["calendar"]
    sched    = settings(config)
    duration = int(cal.get("default_duration_minutes", 60))

    start = datetime.strptime(start_iso, "%Y-%m-%d %H:%M")
    end   = start + timedelta(minutes=duration)

    # Past times are the parser's most likely mistake ("Thursday" said on a
    # Thursday). find_alternatives already skips past candidates; this makes
    # the same guarantee for a directly requested time.
    if start < datetime.now():
        return False

    if not within_business_hours(start, duration, sched):
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


def find_alternatives(config, desired_iso, busy):
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

    def search(direction, limit):
        """Walk one direction, collecting available slots."""
        out = []
        for i in range(1, 337):          # 336 half-hour steps ≈ 7 days
            candidate = desired + (step * i * direction)
            if candidate < datetime.now():
                continue
            iso = candidate.strftime("%Y-%m-%d %H:%M")
            if is_slot_available(config, iso, busy):
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


def slot_rejection_reason(config, start_iso, busy):
    """Why a slot is unavailable: 'past', 'closed', 'blackout', 'conflict', or None."""
    cal      = config["calendar"]
    sched    = settings(config)
    duration = int(cal.get("default_duration_minutes", 60))

    start = datetime.strptime(start_iso, "%Y-%m-%d %H:%M")
    end   = start + timedelta(minutes=duration)

    if start < datetime.now():
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

    if not is_slot_available(config, start_iso, busy):
        return "conflict"

    return None

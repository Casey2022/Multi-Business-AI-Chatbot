"""booking_control.py — an owner saying when the assistant may book.

Added 2026-10-12. Two controls, both the owner's:

1. Pausing new bookings (Settings: "Taking new bookings?"). While paused,
   any booking request gets the owner's message instead of the booking
   flow: checked in code at the door of the flow (scheduler), not left to
   the prompt, so the assistant can't book someone anyway. Questions still
   get answered. An optional date turns bookings back on by itself; nothing
   has to run at midnight, because "paused" is worked out from the date
   each time it's asked.

2. Blocked time (Calendar: "Block off time"): a whole day, several days, or
   specific hours, with a private note. Loaded into the scheduling settings
   by config.load_config, so scheduling.py treats a block like the weekly
   blackout it already had, for the simulated calendar and Google alike:
   never offered, and a customer asking for it hears it isn't available.
"""

from datetime import date, datetime, timedelta

DEFAULT_MESSAGE = ("Sorry, we're not taking new {noun}s right now. Please give us "
                   "a call at {phone} for more information.")
MAX_MESSAGE = 400
FORMAT = "%Y-%m-%d %H:%M"


# --- pausing ------------------------------------------------------------------

def _resume_on(config):
    raw = ((config or {}).get("booking") or {}).get("resume_on")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def pause_state(config, today=None):
    """{"paused", "set_off", "resume_on", "resumed"}.

    set_off is what the owner chose ("No"); paused is whether it's in force
    today: a resume date that has arrived means bookings are on again
    (resumed=True), with no job needed to flip anything."""
    import clock
    today = today or clock.business_today(config)
    accepting = ((config or {}).get("booking") or {}).get("accepting")
    set_off = accepting is False
    resume_on = _resume_on(config) if set_off else None
    resumed = bool(set_off and resume_on and today >= resume_on)
    return {"paused": set_off and not resumed, "set_off": set_off,
            "resume_on": resume_on, "resumed": resumed}


def paused(config, today=None):
    return pause_state(config, today)["paused"]


def _noun(config):
    return ((config or {}).get("booking") or {}).get("noun") or "appointment"


def default_message(config):
    """The standard wording for this business, as the owner sees it in the
    box: their word for a booking filled in, {phone} left for them to see."""
    return DEFAULT_MESSAGE.replace("{noun}", _noun(config))


def message_template(config):
    """The owner's wording, or the standard one."""
    custom = (((config or {}).get("booking") or {}).get("paused_message") or "").strip()
    return custom or default_message(config)


def paused_reply(config):
    """What a customer asking to book is told while bookings are paused."""
    from config import substitute
    return substitute(message_template(config).replace("{noun}", _noun(config)), config)


# --- blocked time -----------------------------------------------------------------

def parse_block_form(form, today):
    """((start, end, all_day, note), errors) from the Calendar page's form.

    Whole day: from the start of the first day to the end of the last.
    Specific hours: from the first date and time to the last date and time
    (the last date defaults to the first), so "Tuesday 9 to 12" and "Monday
    2pm through Wednesday noon" are both one block."""
    errors = []
    all_day = form.get("all_day") == "yes"
    note = " ".join((form.get("note") or "").split())[:120]

    def day(name, label):
        raw = (form.get(name) or "").strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            errors.append(f"{label} isn't a date.")
            return None

    def time_of(name, label):
        raw = (form.get(name) or "").strip()
        if not raw:
            errors.append(f"Choose a {label}, or tick “Whole day”.")
            return None
        try:
            return datetime.strptime(raw, "%H:%M").time()
        except ValueError:
            errors.append(f"The {label} isn't a time.")
            return None

    first = day("from_date", "The start date")
    last = day("until_date", "The end date") or first
    if first is None:
        if not errors:
            errors.append("Choose the day to block.")
        return None, errors
    if all_day:
        start = datetime.combine(first, datetime.min.time())
        end = datetime.combine(last + timedelta(days=1), datetime.min.time())
    else:
        t1, t2 = time_of("from_time", "start time"), time_of("until_time", "end time")
        if errors:
            return None, errors
        start, end = datetime.combine(first, t1), datetime.combine(last, t2)
    if end <= start:
        errors.append("The block ends before it starts.")
    elif end <= datetime.combine(today, datetime.min.time()):
        errors.append("That's already in the past.")
    if (end - start).days > 366:
        errors.append("Block a year at most at a time.")
    if errors:
        return None, errors
    return (start.strftime(FORMAT), end.strftime(FORMAT), all_day, note), []


def describe(block):
    """'Tue, Oct 20, all day' / 'Oct 19 – Oct 23, all day' / 'Tue, Oct 20, 9:00am – 12:00pm'."""
    start = datetime.strptime(block["starts_at"], FORMAT)
    end = datetime.strptime(block["ends_at"], FORMAT)

    def t(d):
        return d.strftime("%-I:%M%p").lower()

    if block.get("all_day"):
        last = end - timedelta(days=1)
        if last.date() == start.date():
            return f"{start:%a, %b %-d}, all day"
        return f"{start:%a, %b %-d} – {last:%a, %b %-d}, all day"
    if start.date() == end.date():
        return f"{start:%a, %b %-d}, {t(start)} – {t(end)}"
    return f"{start:%a, %b %-d} {t(start)} – {end:%a, %b %-d} {t(end)}"


def overlaps(start, end, b_start, b_end):
    return start < b_end and end > b_start


def appointments_in(block, appointments, config):
    """Booked appointments that fall (partly) inside a block: the owner is
    warned about them; they're not cancelled."""
    import scheduling
    b_start = datetime.strptime(block["starts_at"], FORMAT)
    b_end = datetime.strptime(block["ends_at"], FORMAT)
    out = []
    for a in appointments:
        if a.get("status") == "cancelled":
            continue
        try:
            start = datetime.strptime(a["datetime"], FORMAT)
        except (KeyError, TypeError, ValueError):
            continue
        end = start + timedelta(minutes=scheduling.duration_for(config, a.get("service")))
        if overlaps(start, end, b_start, b_end):
            out.append(a)
    return out


def cells(blocks, days, hours):
    """{(weekday, hour): note} for the week grid's shading."""
    out = {}
    for b in blocks:
        b_start = datetime.strptime(b["starts_at"], FORMAT)
        b_end = datetime.strptime(b["ends_at"], FORMAT)
        for d in days:
            for h in hours:
                cell = datetime.combine(d, datetime.min.time()) + timedelta(hours=h)
                if overlaps(cell, cell + timedelta(hours=1), b_start, b_end):
                    out.setdefault((d.weekday(), h), b.get("note") or "Blocked")
    return out

# holidays.py — the holidays an owner says they close for, as closed days.
#
# Until 2026-10-20 the assistant knew only the days an owner had blocked on
# the Calendar page. With Thanksgiving unblocked, Ridgeline's "three business
# days" from the Wednesday before came out as Monday, counting Thanksgiving
# and the Friday after as working days, and Sunrise took a Thanksgiving-
# morning order without a second thought.
#
# Nothing is assumed: a bakery may open Thanksgiving morning. The owner ticks
# the ones they close for (Calendar page, "Holidays we close"); each ticked
# holiday becomes an all-day block every year, through the same list as the
# owner's own blocks (config.load_config), so notice, business days and
# bookings all treat it as closed with no code of their own. A holiday's
# name, unlike an owner's block note, isn't private: the reply can say
# "closed for Thanksgiving".
#
# The day itself, not the "observed" weekday a federal office would take:
# a shop that closes on the Fourth closes on the Fourth.

from datetime import date, timedelta


def _nth_weekday(year, month, weekday, n):
    """The nth `weekday` (0 = Monday) of a month; n = -1 is the last."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


# key: (what the owner and the customer read, the day in a given year).
# In the order the Calendar page lists them: through the year.
HOLIDAYS = {
    "new_years_day":          ("New Year's Day", lambda y: date(y, 1, 1)),
    "memorial_day":           ("Memorial Day", lambda y: _nth_weekday(y, 5, 0, -1)),
    "independence_day":       ("the Fourth of July", lambda y: date(y, 7, 4)),
    "labor_day":              ("Labor Day", lambda y: _nth_weekday(y, 9, 0, 1)),
    "thanksgiving":           ("Thanksgiving", lambda y: _nth_weekday(y, 11, 3, 4)),
    "day_after_thanksgiving": ("the day after Thanksgiving",
                               lambda y: _nth_weekday(y, 11, 3, 4) + timedelta(days=1)),
    "christmas_eve":          ("Christmas Eve", lambda y: date(y, 12, 24)),
    "christmas_day":          ("Christmas Day", lambda y: date(y, 12, 25)),
    "new_years_eve":          ("New Year's Eve", lambda y: date(y, 12, 31)),
}


def ticked(config):
    """The holiday keys this business closes for (unknown keys dropped)."""
    keys = ((config or {}).get("calendar") or {}).get("holidays") or []
    return [k for k in keys if k in HOLIDAYS]


def blocks(keys, year):
    """All-day blocks, in the Calendar page's format, for the ticked holidays
    in the year before `year` through the year after it."""
    out = []
    for y in (year - 1, year, year + 1):
        for key in keys:
            if key in HOLIDAYS:
                day = HOLIDAYS[key][1](y)
                out.append({"start": f"{day:%Y-%m-%d} 00:00",
                            "end": f"{day + timedelta(days=1):%Y-%m-%d} 00:00"})
    return out


def name_on(day, config):
    """The ticked holiday on `day` (a date or datetime), or None."""
    day = day.date() if hasattr(day, "date") else day
    for key in ticked(config):
        label, on = HOLIDAYS[key]
        if on(day.year) == day:
            return label
    return None


def next_dates(today):
    """{key: the holiday's next date on or after today}, for the Calendar page."""
    out = {}
    for key, (_, on) in HOLIDAYS.items():
        day = on(today.year)
        out[key] = day if day >= today else on(today.year + 1)
    return out

"""What time it is for the business, wherever the server happens to be.

Appointment times are stored and compared as naive, business-local strings
("2026-09-22 16:00" means 4pm in Rochester). So "now" has to be naive and
business-local too. datetime.now() is the SERVER's local time, and Render's
servers run on UTC, four or five hours ahead of the Eastern businesses.
Before this module, on Render:

- a same-day booking within the next four hours was refused as "past"
  (at 2pm Eastern the server said 6pm, so a 4pm slot was already gone)
- after 8pm Eastern, "tomorrow" was resolved against the day after tomorrow
- the owner portal's "today" (and so the calendar week) jumped a day early
  every evening

Stdlib only, so scheduling.py stays pure and the tests can import it
without the app's dependencies.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "America/New_York"


def utc_now():
    """The one read of the real clock. Tests replace this to fix the moment."""
    return datetime.now(timezone.utc)


def timezone_for(config=None):
    """The business's timezone from its calendar config, else the default."""
    return ((config or {}).get("calendar") or {}).get("timezone") or DEFAULT_TIMEZONE


def business_now(config=None, now=None):
    """Naive current time in the business's timezone.

    `now` short-circuits the clock entirely, for the eval's pinned prompt
    clock.
    """
    if now is not None:
        return now
    return utc_now().astimezone(ZoneInfo(timezone_for(config))).replace(tzinfo=None)


def business_today(config=None):
    return business_now(config).date()

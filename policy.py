"""Rule arithmetic in code, not in the model's head.

For a week the Sunrise deposit rows failed whatever the wording. Each
rewrite fixed one row and broke the other: "3 days before" got a wedding
refund, then "it differs between the two", and "a month out" got "25% or
less, you get it all back", then "75% of what you paid above". The 6pm
muffins row called 14 hours "just under" a 24-hour window. Sonnet 5 failed
the same family on different rows. It isn't a wording problem; it's the
model doing arithmetic on rules, which it gets right most of the time and
that is not good enough for money.

So the split is a cashier and a till: the model reads what the customer
wants and phrases the answer; this module does the sums. Two tools:

- check_notice: is there enough notice between now and when they need it?
  The notice period still comes from the knowledge document (the model
  reads "24 hours' notice" and passes 24), so there is no second copy of
  it. Only the subtraction moved.
- cancellation_outcome: which cancellation window a cancellation falls in
  and what happens to each kind of order. The rules live in the business's
  YAML under policies.cancellation as data, and the text the bot sees
  (policy_text) is rendered from that data. It is the ONLY copy: the
  document has no deposits section (one_source_test.py checks).

Stdlib only, so the tests import it without the app's dependencies.
"""

import json
import logging
import re
from datetime import datetime, timedelta

import clock

log = logging.getLogger("policy")


# ---------------------------------------------------------------------------
# Saying amounts of time and money the same way every time
# ---------------------------------------------------------------------------

def label_span(hours):
    """A window boundary: "7 days", "48 hours"."""
    if hours >= 72 and hours % 24 == 0:
        return f"{int(hours // 24)} days"
    return f"{hours:g} hours" if hours != 1 else "1 hour"


def span(hours):
    """A measured stretch of time: "14 hours", "4 days (96 hours)", "no time"."""
    minutes = round(hours * 60)
    if minutes <= 0:
        return "no time"
    if minutes < 60:
        return f"{minutes} minutes"
    whole = minutes / 60
    text = f"{round(whole, 1):g} hours" if whole != 1 else "1 hour"
    if minutes % 1440 == 0 and whole >= 48:
        return f"{minutes // 1440} days ({text})"
    return text


def when(moment):
    """ "Wednesday, September 23 at 8:00 AM" """
    return (f"{moment.strftime('%A, %B')} {moment.day} at "
            f"{moment.strftime('%I:%M %p').lstrip('0')}")


def money(amount):
    text = f"${amount:,.2f}"
    return text[:-3] if text.endswith(".00") else text


# ---------------------------------------------------------------------------
# Notice periods
# ---------------------------------------------------------------------------

def _parse_local(text):
    """ISO date-time from the model, as a naive business-local datetime."""
    moment = datetime.fromisoformat(str(text).strip().replace("Z", ""))
    return moment.replace(tzinfo=None)


_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday"]


def clock_time(moment):
    """ "4pm", "9:45pm": the way a cutoff is said."""
    text = moment.strftime("%I:%M%p").lstrip("0").lower()
    return text.replace(":00", "")


def _cutoff_on(needed, order_by, order_by_days):
    """The order-by moment that applies to an order needed at `needed`, or None.

    "On Friday and Saturday nights we ask for the order by 4pm": the cutoff
    is on the day the order is FOR, and only on the days listed (none listed:
    every day). A cutoff later than the time it's needed doesn't bind.
    """
    if not order_by:
        return None
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(order_by))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"order_by should be HH:MM (24-hour), not {order_by!r}")
    days = [str(d).strip().lower() for d in (order_by_days or [])]
    unknown = [d for d in days if d not in _WEEKDAYS and d.rstrip("s") not in _WEEKDAYS]
    if unknown:
        raise ValueError(f"order_by_days should be weekday names, not {unknown}")
    days = [d if d in _WEEKDAYS else d.rstrip("s") for d in days]
    if days and _WEEKDAYS[needed.weekday()] not in days:
        return None
    cutoff = needed.replace(hour=int(m.group(1)), minute=int(m.group(2)),
                            second=0, microsecond=0)
    return cutoff if cutoff <= needed else None


# ---------------------------------------------------------------------------
# Opening hours
# ---------------------------------------------------------------------------
#
# 2026-10-18 (journey_eval baseline, 3/3): "a dozen muffins tomorrow at 5pm?"
# at Sunrise, which closes at 3pm, got "Yes! … tomorrow at 5pm": check_notice
# said 28 hours beats 24 and nothing mentioned closing time. Opening hours
# decide WHEN something can be collected or delivered. They don't change how
# notice is counted: "24 hours' notice" stays 24 clock hours, as the
# documents say (whether closed hours count is the business's call, and a
# guess would be wrong in a new way). Read the way the booking flow reads
# them (scheduling.settings: weekday keys, Monday 0). No hours set: unknown,
# and nothing here says anything. Time the owner blocked off on the
# Calendar page counts too (2026-10-18), as it does for booking: the booking
# flow never offers it, so the answer doesn't either.

def _opening(config):
    """{weekday: (open, close)} as times; {} when the business has none."""
    import scheduling
    hours = scheduling.settings(config or {})["business_hours"] or {}
    out = {}
    for day in range(7):
        pair = hours.get(day, hours.get(str(day)))
        try:
            if pair:
                out[day] = (datetime.strptime(pair[0], "%H:%M").time(),
                            datetime.strptime(pair[1], "%H:%M").time())
        except (TypeError, ValueError, IndexError):
            continue
    return out


def _blocked(moment, config):
    """The owner's block (Calendar: "Block off time") covering `moment`, as
    (start, end), or None. Read the way the booking flow reads them
    (scheduling.settings "blocks", [start, end)). Only the times reach the
    config: the owner's note is private and never loaded."""
    import scheduling
    plain = moment.replace(tzinfo=None)
    for start, end in scheduling.settings(config or {})["blocks"]:
        if start <= plain < end:
            return start, end
    return None


def availability(moment, config):
    """"open", "closed" (outside opening hours), "blocked" (time the owner
    blocked off), or None when nothing is known (no hours, no block)."""
    if _blocked(moment, config):
        return "blocked"
    hours = _opening(config)
    if not hours:
        return None
    pair = hours.get(moment.weekday())
    # Closing time itself counts as open: "collect by 3pm" at a 3pm close.
    return "open" if pair and pair[0] <= moment.time() <= pair[1] else "closed"


def is_open(moment, config):
    """True or False, or None if nothing is known."""
    state = availability(moment, config)
    return None if state is None else state == "open"


def _next_by_hours(moment, config):
    """The first moment at or after `moment` inside opening hours (or
    `moment` itself when no hours are set)."""
    hours = _opening(config)
    if not hours:
        return moment
    for days in range(8):
        day = (moment + timedelta(days=days)).date()
        pair = hours.get(day.weekday())
        if not pair:
            continue
        opens = datetime.combine(day, pair[0], tzinfo=moment.tzinfo)
        closes = datetime.combine(day, pair[1], tzinfo=moment.tzinfo)
        candidate = max(moment, opens)
        if candidate <= closes:
            return candidate
    return None


def _last_by_hours(moment, config):
    """The latest moment at or before `moment` inside opening hours."""
    hours = _opening(config)
    if not hours:
        return moment
    for days in range(8):
        day = (moment - timedelta(days=days)).date()
        pair = hours.get(day.weekday())
        if not pair:
            continue
        opens = datetime.combine(day, pair[0], tzinfo=moment.tzinfo)
        closes = datetime.combine(day, pair[1], tzinfo=moment.tzinfo)
        candidate = min(moment, closes)
        if candidate >= opens:
            return candidate
    return None


def next_open(moment, config):
    """The first moment at or after `moment` when it's open and not
    blocked off; None when nothing is known."""
    if not _opening(config) and not _blocked(moment, config):
        return None
    t = moment
    for _ in range(100):                  # a long holiday is several blocks
        candidate = _next_by_hours(t, config)
        if candidate is None:
            return None
        block = _blocked(candidate, config)
        if not block:
            return candidate
        t = block[1].replace(tzinfo=moment.tzinfo)
    return None


def last_open_before(moment, config, not_before):
    """The latest moment at or before `moment` when it's open and not
    blocked off, no earlier than `not_before` (the notice); None if none.
    Just before a block, it's "by" the block's start."""
    t = moment
    for _ in range(100):
        candidate = _last_by_hours(t, config)
        if candidate is None or candidate < not_before:
            return None
        block = _blocked(candidate, config)
        if not block:
            return candidate
        start = block[0].replace(tzinfo=moment.tzinfo)
        just_before = start - timedelta(minutes=1)
        if start >= not_before and availability(just_before, config) == "open":
            return start
        t = just_before
    return None


def _closed_sentence(config, needed, earliest, notice, what):
    """What to say when `needed` is outside opening hours: that, and the
    nearest open times either side that still give the notice."""
    name = ((config or {}).get("business") or {}).get("name") or "The business"
    hours = ((config or {}).get("business") or {}).get("hours")
    before = last_open_before(needed, config, earliest)
    after = next_open(max(needed, earliest), config)
    if availability(needed, config) == "blocked":
        # Time the owner blocked off: not "closed" (a 2-hour block isn't),
        # and never why (the owner's note is private, and not loaded).
        say = (f" {name} isn't available on {when(needed)}, so {what} can't be "
               f"collected or delivered then, whatever the notice. Don't give a "
               f"reason.")
    else:
        say = (f" {name} isn't open on {when(needed)}"
               + (f" (hours: {hours})" if hours else "")
               + f", so {what} can't be collected or delivered then, whatever the notice.")
    options = []
    if before is not None:
        options.append(f"by {when(before)}")
    if after is not None:
        options.append(f"from {when(after)}")
    if options:
        say += (f" The nearest times they're open that still give {span(notice)} of "
                f"notice: {' or '.join(options)}. Offer one of those; don't agree "
                f"to {clock_time(needed)}.")
    return say, before, after


def check_notice(needed_by, notice_hours, what, config=None, now=None,
                 order_by=None, order_by_days=None):
    """How much time there is before `needed_by`, against the notice needed,
    and against an order-by time if the facts give one.

    Deliberately says nothing about opening hours: whether the shop is open
    at the moment the order would have to be placed is a separate question,
    and folding it in here would make this answer wrong in a new way.

    order_by / order_by_days (2026-10-03): Crosstown's trays need three
    hours' notice AND, on Friday and Saturday nights, ordering by 4pm. At
    5pm on a Friday the bot offered a tray, 3 runs in 6. The cutoff stays in
    the document (one copy); the model passes it in like notice_hours.
    """
    now = clock.business_now(config, now)
    notice = float(notice_hours)
    if notice < 0:
        raise ValueError("notice_hours can't be negative")
    what = (what or "this").strip()
    earliest = now + timedelta(hours=notice)
    have_it = next_open(earliest, config)
    if not needed_by:
        # "How soon could I get…?" (2026-10-18): no time wanted, so the
        # answer is the earliest it can be ready, moved to when they're open.
        result = {"now": when(now), "notice_needed_hours": notice,
                  "earliest_ready_if_ordered_now": when(earliest),
                  "say": (f"It's {when(now)} now. The notice for {what} is "
                          f"{span(notice)}, so ordered now the earliest it can be "
                          f"ready is {when(earliest)}.")}
        if have_it is not None and have_it != earliest:
            name = ((config or {}).get("business") or {}).get("name") or "The business"
            result["earliest_they_can_have_it"] = when(have_it)
            result["say"] += (f" {name} isn't open or available then, so the earliest "
                              f"they can collect it or have it delivered is "
                              f"{when(have_it)}. Say that time, not {when(earliest)}.")
        return result
    needed = _parse_local(needed_by)
    available = (needed - now).total_seconds() / 3600
    latest = needed - timedelta(hours=notice)
    cutoff = _cutoff_on(needed, order_by, order_by_days)
    if cutoff is not None and cutoff < latest:
        latest = cutoff
    in_time = available >= notice and (cutoff is None or now <= cutoff)
    result = {
        "now": when(now),
        "needed_by": when(needed),
        "hours_available": round(max(available, 0), 1),
        "notice_needed_hours": notice,
        "enough_notice": in_time,
        "order_by": when(latest),
        "earliest_ready_if_ordered_now": when(earliest),
    }
    cut = ""
    if cutoff is not None:
        result["cutoff"] = when(cutoff)
        result["cutoff_passed"] = now > cutoff
        day = _WEEKDAYS[needed.weekday()].capitalize()
        cut = (f" For {day}, {what} must also be ordered by "
               f"{clock_time(cutoff)}"
               + (f", and it's past {clock_time(cutoff)} now." if now > cutoff
                  else "."))
    open_then = is_open(needed, config)
    if open_then is not None:
        result["open_at_needed_by"] = open_then
    if available < 0:
        result["say"] = (f"{when(needed)} has already passed; it's "
                         f"{when(now)} now.")
    elif open_then is False:
        closed, before, after = _closed_sentence(config, needed, earliest, notice, what)
        met = (", which is met" if available >= notice
               else f", so the notice is missed by {span(notice - available)}")
        result["enough_notice"] = False
        result["say"] = (f"From now ({when(now)}) to {when(needed)} is {span(available)}. "
                         f"The notice for {what} is {span(notice)}{met}.{cut}{closed}")
        if before is not None:
            result["latest_open_before"] = when(before)
        if after is not None:
            result["next_open_after"] = when(after)
    elif in_time:
        result["say"] = (f"From now ({when(now)}) to {when(needed)} is "
                         f"{span(available)}. The notice for {what} is "
                         f"{span(notice)}, so there is "
                         f"enough time if it's ordered by {when(latest)}."
                         if not cut else
                         f"From now ({when(now)}) to {when(needed)} is "
                         f"{span(available)}. The notice for {what} is "
                         f"{span(notice)}.{cut} So there is enough time if "
                         f"it's ordered by {when(latest)}.")
    else:
        if available < notice:
            result["short_by_hours"] = round(notice - available, 1)
        gap = (f"It's {when(now)} now and it's wanted right away"
               if round(available * 60) <= 0 else
               f"From now ({when(now)}) to {when(needed)} is only "
               f"{span(available)}")
        if available < notice:
            notice_part = (f"The notice for {what} is {span(notice)}, so the "
                           f"notice is missed by {span(notice - available)}.")
        else:
            notice_part = f"The notice for {what} is {span(notice)}, which is met."
        result["say"] = (f"{gap}. {notice_part}{cut} It can't be promised for "
                         f"{when(needed)}. To have it then, it needed ordering "
                         f"by {when(latest)}."
                         + ("" if cutoff is not None else
                            f" Ordered now, the earliest it can be ready is "
                            f"{when(earliest)}."
                            + (f" They aren't open or available then, so the "
                               f"earliest they can have it is {when(have_it)}."
                               if have_it is not None and have_it != earliest else "")))
        if cutoff is not None and now > cutoff:
            # Live and in journey_eval (2026-10-03, again 2026-10-11): with
            # the tray cutoff passed, the bot offered to "see if the kitchen
            # can squeeze one in", or sent them to another day, and never
            # said regular food was still on tonight, though the document
            # says the cutoff is for trays only.
            result["say"] += (f" Past that cutoff it isn't possible, so don't "
                              f"suggest it might be. The cutoff is for {what} "
                              f"only: if the facts say other things can still be "
                              f"ordered for then, say what they can order instead.")
    return result


# ---------------------------------------------------------------------------
# Business days
# ---------------------------------------------------------------------------
#
# Ridgeline quotes "within three business days" of the visit and books
# estimates "two to five business days out". Counting that from a Thursday
# means skipping a weekend, which is the kind of sum the model gets wrong.
# Business days are the days the business has hours (its calendar's
# business_hours), Monday to Friday if none are set. Public holidays aren't
# known, and the result says so.

def business_weekdays(config=None):
    """Weekday numbers (Monday = 0) the business is open."""
    hours = ((((config or {}).get("calendar") or {}).get("scheduling") or {})
             .get("business_hours") or {})
    days = set()
    for key in hours:
        try:
            if hours[key]:
                days.add(int(key))
        except (TypeError, ValueError):
            continue
    return days or {0, 1, 2, 3, 4}


def _date_only(text):
    text = str(text).strip()
    return datetime.fromisoformat(text[:10]).date()


def _after_business_days(start, n, open_days):
    day, counted = start, 0
    while counted < n:
        day += timedelta(days=1)
        if day.weekday() in open_days:
            counted += 1
    return day


def _day(d):
    return f"{d.strftime('%A, %B')} {d.day}"


def add_business_days(start, business_days, up_to=None, what=None, config=None):
    """The date `business_days` business days after `start` (and, for a
    range, after `up_to`). Counting starts the day after `start`."""
    begin = _date_only(start)
    n = int(business_days)
    if n < 0 or (up_to is not None and int(up_to) < n):
        raise ValueError("business days must be 0 or more, and up_to >= business_days")
    open_days = business_weekdays(config)
    first = _after_business_days(begin, n, open_days)
    closed = [_WEEKDAYS[d].capitalize() + "s" for d in range(7) if d not in open_days]
    result = {"start": _day(begin), "business_days": n, "date": _day(first),
              "closed_days": closed}
    label = (what or "it").strip()
    # The rule first, then the date: with the date first ("3 business days
    # after Thursday is Tuesday"), the model said "by Tuesday" and dropped
    # the rule (rag_eval 2026-10-05), as it did with the deposit tool.
    if up_to is not None and int(up_to) != n:
        last = _after_business_days(begin, int(up_to), open_days)
        result.update(up_to_business_days=int(up_to), date_latest=_day(last))
        say = (f"The rule: {n} to {int(up_to)} business days, for {label}. "
               f"Counting from {_day(begin)}, that's {_day(first)} at the "
               f"earliest and {_day(last)} at the latest")
    else:
        say = (f"The rule: {n} business day{'s' if n != 1 else ''}, for {label}. "
               f"Counting from {_day(begin)}, that's {_day(first)}")
    result["say"] = (f"{say}. "
                     + (f"{' and '.join(closed)} don't count. " if closed else "")
                     + "Public holidays aren't counted, so a holiday in between "
                       "would push it back a day.")
    return result


ADD_BUSINESS_DAYS_TOOL = {
    "name": "add_business_days",
    "description": (
        "Count business days for the customer: the date that is N business "
        "days after a start date (skipping the days this business is "
        "closed), or a range for \"N to M business days\". Use it whenever "
        "the facts give a time in business days and the customer asks when. "
        "Don't count the days yourself."),
    "input_schema": {
        "type": "object",
        "properties": {
            "start": {"type": "string",
                      "description": "The start date, YYYY-MM-DD, worked out "
                                     "from today's date in the prompt (e.g. "
                                     "the day of the visit, or today)."},
            "business_days": {"type": "integer",
                              "description": "How many business days, from the facts."},
            "up_to_business_days": {
                "type": ["integer", "null"],
                "description": "For a range (\"2 to 5 business days\"), the "
                               "upper number; otherwise null."},
            "what": {"type": "string",
                     "description": "What happens then, as the facts put it."},
        },
        "required": ["start", "business_days", "up_to_business_days", "what"],
    },
}


# ---------------------------------------------------------------------------
# Cancellation windows
# ---------------------------------------------------------------------------

def cancellation(config):
    return (((config or {}).get("policies") or {}).get("cancellation")) or None


def _kinds(policy):
    """Kinds of order the rules distinguish (Sunrise: custom and wedding
    cakes). A business with one rule for everything has one kind.

    Without deposits, an owner can give particular services their own
    outcomes (policy["services"], 2026-10-17: "colour needs 48 hours,
    everything else 24"): those services, then the business's own word
    ("appointment") for everything else."""
    kinds = list((policy.get("deposits") or {}).keys())
    if kinds:
        return kinds
    what = policy.get("what", "order")
    services = [s for s in (policy.get("services") or []) if s and s != what]
    return services + [what]


def _by_service(policy):
    """True when the kinds are services (plus "everything else")."""
    return bool(policy.get("services")) and not policy.get("deposits")


def _a(word):
    return "an" if str(word)[:1].lower() in "aeiou" else "a"


def kind_label(policy, kind):
    """A kind as it's said: "a custom cake"; with services, "a colour
    appointment" and, for everything else, "any other appointment"."""
    what = policy.get("what", "order")
    if _by_service(policy):
        return f"any other {what}" if kind == what else f"{_a(kind)} {kind} {what}"
    return f"{_a(kind)} {kind}"


def _next_change(windows, index, kind):
    """(hours, spec) of the nearest longer-notice window whose outcome for
    this kind differs from this window's, or None. With services sharing
    windows, the next window up may change nothing for this one (a haircut
    is free from 24 hours whether or not colour needs 48)."""
    here = json.dumps(_outcome_for(windows[index], kind), sort_keys=True)
    for i in range(index - 1, -1, -1):
        spec = _outcome_for(windows[i], kind)
        if json.dumps(spec, sort_keys=True) != here:
            return windows[i]["at_least_hours"], spec
    return None


def _words(policy):
    """(cancelled, cancelling, price) as this business says them."""
    return (policy.get("action", "cancelled"),
            policy.get("acting", "cancelling"),
            policy.get("price", "the order price"))


def _windows(policy):
    """Windows, longest notice first."""
    return sorted(policy.get("windows") or [],
                  key=lambda w: w["at_least_hours"], reverse=True)


def window_label(policy, index):
    windows = _windows(policy)
    unit = policy.get("before", "the order date")
    lower = windows[index]["at_least_hours"]
    upper = windows[index - 1]["at_least_hours"] if index else None
    if upper is None:
        return f"{label_span(lower)} or more before {unit}"
    if lower == 0:
        return f"less than {label_span(upper)} before {unit}"
    return f"between {label_span(lower)} and {label_span(upper)} before {unit}"


def _outcome_for(window, kind):
    spec = window.get(kind, window.get("any"))
    if spec is None:
        raise ValueError(f"window {window} has no outcome for {kind!r}")
    return spec


def outcome_text(spec, policy, kind):
    price = _words(policy)[2]
    if spec == "no_charge":
        return "there is no charge"
    if isinstance(spec, dict) and "charge_percent" in spec:
        return f"{spec['charge_percent']:g}% of {price} is charged"
    if spec == "refund_deposit":
        return "the deposit comes back in full"
    if spec == "forfeit_deposit":
        return "the whole deposit is forfeited and none of it comes back"
    if spec == "charge_full_price":
        return f"the full {price.replace('the ', '', 1)} is charged"
    if isinstance(spec, dict) and "keep_percent" in spec:
        p = spec["keep_percent"]
        text = (f"{p:g}% of {price} is kept, and only what was paid "
                f"above that {p:g}% is refunded")
        minimum = ((policy.get("deposits") or {}).get(kind) or {}).get("min_percent")
        if minimum == p:
            text += (f"; someone who paid just the {p:g}% minimum gets "
                     f"nothing back")
        return text
    raise ValueError(f"unknown cancellation outcome: {spec!r}")


def _amounts(spec, paid, price):
    """(kept, refunded, still_owed) for known amounts, or None."""
    if paid is None:
        return None
    if spec == "refund_deposit":
        return 0.0, paid, 0.0
    if spec == "forfeit_deposit":
        return paid, 0.0, 0.0
    if spec == "charge_full_price":
        return (paid, 0.0, max(price - paid, 0.0)) if price is not None else None
    if isinstance(spec, dict) and "keep_percent" in spec and price is not None:
        kept = min(paid, price * spec["keep_percent"] / 100)
        return kept, paid - kept, 0.0
    return None


def deposit_text(policy, kind):
    d = (policy.get("deposits") or {}).get(kind) or {}
    if "percent" in d:
        text = f"{d['percent']:g}% of the order price"
    elif "min_percent" in d:
        text = f"at least {d['min_percent']:g}% of the order price"
    else:
        text = "a deposit"
    return text + (f", {d['when']}" if d.get("when") else "")


def cancellation_outcome(config, hours_before=None, days_before=None,
                         kind=None, amount_paid=None, order_price=None,
                         starts_at=None, now=None, service=None):
    policy = cancellation(config)
    if not policy:
        raise ValueError("this business has no cancellation policy on file")
    if starts_at is not None:
        # "My colour is at 9am tomorrow and it's 10am now": the model reads
        # the time, code does the subtraction. Belmont's eval row got "1
        # hour short... cancel by 9am tomorrow" from the model's own sums.
        hours = (_parse_local(starts_at)
                 - clock.business_now(config, now)).total_seconds() / 3600
    elif hours_before is not None or days_before is not None:
        hours = float(hours_before if hours_before is not None
                      else float(days_before) * 24)
    else:
        raise ValueError("say when: starts_at, hours_before or days_before")
    kinds = _kinds(policy)
    if kind is not None and kind not in kinds:
        kind = None                         # unknown kind: give every kind
    windows = _windows(policy)
    index = next((i for i, w in enumerate(windows)
                  if hours >= w["at_least_hours"]), len(windows) - 1)
    window = windows[index]
    label = window_label(policy, index)
    asked = [kind] if kind else kinds
    specs = {k: _outcome_for(window, k) for k in asked}
    texts = {k: outcome_text(s, policy, k) for k, s in specs.items()}
    same = len({json.dumps(s, sort_keys=True) for s in
                (_outcome_for(window, k) for k in kinds)}) == 1
    what = policy.get("what", "order")
    _, acting, _ = _words(policy)

    ahead = (f"after {policy.get('before', 'the order date')}" if hours < 0
             else f"{span(hours)} before {policy.get('before', 'the order date')}")
    # The rule first, then what it means for them. With the window second
    # ("Cancelling 3 days before falls under ..."), the model kept the
    # outcome and dropped the rule: right answer, no reason (rag_eval
    # 2026-10-02, 3/3).
    if kind and len(kinds) > 1:
        rule = f"for {kind_label(policy, kind)}, {texts[kind]}"
    elif len(kinds) == 1:
        rule = texts[kinds[0]]
    elif same:
        rule = (f"{texts[kinds[0]]}, for every {what}"
                + ("" if _by_service(policy) else f" ({' and '.join(kinds)} alike)"))
    else:
        rule = "; ".join(f"for {kind_label(policy, k)}, {t}" for k, t in texts.items())
    say = (f"The rule: {acting} {label}, {rule}. "
           f"{acting[0].upper() + acting[1:]} {ahead} falls in that window, so "
           f"that is what applies.")
    # The customer's service has no rules of its own (policy.kind_from_words
    # decided): say so, naming the ones that do. Live, 2026-10-18, with only
    # "cut and colour" ticked: a colour customer was told (rightly) there's no
    # charge, then "colour appointments get charged differently in that
    # window", the model's shortening of "a cut and colour appointment".
    if service and _by_service(policy) and kind == what and service not in policy["services"]:
        own = " and ".join(f"{_a(s)} {s} {what}" for s in policy["services"])
        say += (f" Their {service} {what} has no rules of its own, so it follows the "
                f"rules for any other {what}; only {own} is different, and that "
                f"isn't theirs, so don't say a {service} {what} is charged differently.")
    # Close to the line above, say the comparison outright. Given only
    # "falls in that window", the model wrote "23 hours... you fall just
    # under that window", which reads as borderline (journey_eval
    # 2026-10-15, 1 run in 3). It isn't: 23 is less than 24.
    # With services, the line that matters is where THIS service's outcome
    # changes; for deposit kinds and single rules it's the next window up,
    # as before (every adjacent window there differs).
    k0 = kind or asked[0]
    change = _next_change(windows, index, k0) if (kind or len(kinds) == 1 or not _by_service(policy)) \
        else None
    upper = change[0] if change else (None if _by_service(policy) else
                                      (windows[index - 1]["at_least_hours"] if index else None))
    if upper is not None and 0 <= hours < upper and upper - hours <= max(3, upper * 0.15):
        say += (f" {span(hours)[0].upper() + span(hours)[1:]} is less than "
                f"{span(upper)}, so this is inside that window, not on the edge of it.")
    # The better outcome's deadline, worked out here. Left to the model it
    # said "to avoid the charge, cancel before 9am tomorrow" (the appointment
    # itself) when the line was 9am TODAY (journey_eval 2026-10-15, 1 in 3).
    if upper is not None and hours >= 0 and change:
        better = outcome_text(change[1], policy, k0)
        if starts_at is not None:
            deadline = _parse_local(starts_at) - timedelta(hours=upper)
            say += (f" With at least {span(upper)} of notice (by {when(deadline)}), "
                    f"{better}; that time has already passed, so it's too late for "
                    f"that now. Don't say they still have time for it, and don't name "
                    f"any later time as a way to avoid this.")
        else:
            # Given only the hours, the model read "with at least 48 hours of
            # notice, there is no charge" as advice: "if you can wait another
            # 18 hours or so, you'll hit the 48-hour mark" (live, Belmont,
            # 2026-10-17). Waiting only ever gives LESS notice, so say so.
            # Then, after that: "the good news is you've still got time — if
            # you cancel more than 48 hours ahead, there's no charge" (live,
            # 30 hours out). So "too late", in those words.
            say += (f" With at least {span(upper)} of notice, {better}; that point "
                    f"passed {span(upper - hours)} ago, so it's too late for that now. "
                    f"Don't say they still have time for it, and don't suggest waiting "
                    f"or any later time (waiting only means less notice).")
    paid = float(amount_paid) if amount_paid is not None else None
    price = float(order_price) if order_price is not None else None
    for k in (asked[:1] if same and not kind else asked):
        sums = _amounts(specs[k], paid, price)
        if sums:
            kept, back, owed = sums
            say += (f" With {money(paid)} paid"
                    + (f" on a {money(price)} order" if price is not None else "")
                    + (f" ({k})" if len(asked) > 1 and not same else "")
                    + f": {money(back)} comes back"
                    + (f", {money(kept)} is kept" if kept else "")
                    + (f", and {money(owed)} more is owed" if owed else "")
                    + ".")
    return {"window": label, "hours_before": hours,
            "outcomes": texts, "same_for_every_kind": same, "say": say}


def policy_text(config):
    """The cancellation rules as the bot (and the judge) read them."""
    policy = cancellation(config)
    if not policy:
        return ""
    kinds = _kinds(policy)
    action = _words(policy)[0]
    lines = [f"{policy.get('title', 'Deposits and cancellation')}:"]
    if policy.get("deposits"):
        lines.append("Deposits: " + "; ".join(f"{k}, {deposit_text(policy, k)}"
                                              for k in kinds) + ".")
    for i, window in enumerate(_windows(policy)):
        label = window_label(policy, i)
        specs = {k: _outcome_for(window, k) for k in kinds}
        texts = {k: outcome_text(s, policy, k) for k, s in specs.items()}
        head = f"{action[0].upper() + action[1:]} {label}"
        if len(kinds) == 1:
            lines.append(f"{head}: {texts[kinds[0]]}.")
        elif len({json.dumps(s, sort_keys=True) for s in specs.values()}) == 1:
            lines.append(f"{head}: {texts[kinds[0]]}, for every "
                         f"{policy.get('what', 'order')}.")
        elif _by_service(policy):
            lines.append(f"{head}: " + "; ".join(
                f"{kind_label(policy, k)}, {t}" for k, t in texts.items()) + ".")
        else:
            lines.append(f"{head}: " + "; ".join(
                f"{k}, {t}" for k, t in texts.items()) + ".")
    lines += list(policy.get("notes") or [])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# What the model is offered
# ---------------------------------------------------------------------------

CHECK_NOTICE_TOOL = {
    "name": "check_notice",
    "description": (
        "Work out whether there is enough notice for something the customer "
        "wants by a particular time. Use it whenever the customer names a "
        "time they need something by (tonight, 8am tomorrow, Saturday, "
        "today), or asks how soon they could have it, and the facts give a "
        "notice period for that thing. Not for cancelling or moving: that is "
        "a different rule. It returns the hours available, whether that is "
        "enough, whether the business is open then, the latest time it could "
        "have been ordered, and a sentence to base the answer on. Do this "
        "arithmetic here, never in your head."),
    "input_schema": {
        "type": "object",
        "properties": {
            # Nullable since 2026-10-18: "what's the earliest I could pick
            # them up?" names no time, the tool wasn't used, and the reply
            # hedged (journey_eval, 2 runs in 3).
            "needed_by": {
                "type": ["string", "null"],
                "description": (
                    "When the customer needs it, business-local, as "
                    "YYYY-MM-DDTHH:MM, worked out from today's date and time "
                    "in the prompt. A day with no time: that day's opening "
                    "time. 'Today' or 'now' with no time: right now. null when "
                    "they ask how soon, or the earliest, they could have it.")},
            "notice_hours": {
                "type": "number",
                "description": (
                    "The notice the facts give for this thing, in hours "
                    "(3 days = 72, 2 weeks = 336).")},
            "what": {
                "type": "string",
                "description": "What it is, as the facts name it."},
            # Required (but nullable) since 2026-10-03: optional, it was left
            # out 1 run in 3 at Crosstown and the bot promised a tray after
            # the 4pm cutoff. Required, the model has to look and decide.
            "order_by": {
                "type": ["string", "null"],
                "description": (
                    "Check the facts for a clock time it must be ordered by "
                    "(\"order by noon on weekends\"): that time as HH:MM, "
                    "24-hour. null only if the facts give none for this thing.")},
            "order_by_days": {
                "type": ["array", "null"], "items": {"type": "string"},
                "description": (
                    "The weekdays that order-by time applies to, if the "
                    "facts limit it (e.g. [\"saturday\", \"sunday\"]). "
                    "null if it applies every day or there is no order-by "
                    "time.")},
        },
        "required": ["needed_by", "notice_hours", "what", "order_by",
                     "order_by_days"],
    },
}


def cancellation_tool(config):
    policy = cancellation(config)
    what = policy.get("what", "order")
    kinds = _kinds(policy)
    tool = {
        "name": "cancellation_outcome",
        "description": (
            "Apply this business's cancellation rules to one cancellation "
            "(or move). Use it whenever the customer asks what happens if "
            "they cancel or move, or whether they get a deposit back, and says or "
            "implies how far ahead (three days before, a month out, "
            "tomorrow). Call it straight away with the timing they gave: it "
            "needs nothing else, so never ask which kind or how much they "
            "paid first. Answer from the sentence it returns; don't "
            "recompute or soften it."),
        "input_schema": {
            "type": "object",
            "properties": {
                "starts_at": {
                    "type": "string",
                    "description": (
                        f"When the {what} is, business-local, as "
                        "YYYY-MM-DDTHH:MM, if the customer said (\"9am "
                        "tomorrow\"). The hours are worked out for you; give "
                        "this instead of hours_before when you can.")},
                "hours_before": {
                    "type": "number",
                    "description": "How long before the order date they "
                                   "cancel(led), in hours."},
                "days_before": {
                    "type": "number",
                    "description": "The same in days, if that's how they "
                                   "said it (a month = 30). Give one of the "
                                   "two."},
                # No amount_paid / order_price here, though
                # cancellation_outcome can use them: offered, they had the
                # model ask "how much did you pay?" instead of answering
                # (rag_eval, 2026-09-30, both deposit rows, every run).
                "kind": {
                    "type": "string", "enum": _kinds(policy),
                    "description": (
                        f"Which service the {what} is for, only if the customer "
                        f"already said: exactly one of "
                        + ", ".join(f"“{k}”" for k in kinds[:-1])
                        + f" (only that service, not one that includes it); for "
                        f"any other service, “{what}”. Leave it out otherwise: "
                        f"the result then covers every kind."
                        if _by_service(policy) else
                        f"Which kind of {what}, only if the "
                        f"customer already said. Leave it out "
                        f"otherwise: the result then covers "
                        f"every kind.")},
            },
        },
    }
    if len(kinds) < 2:
        del tool["input_schema"]["properties"]["kind"]
    return tool


# Tools, and the rules for using them, are offered only to a message that
# needs them: one that names a time, or asks about cancelling. Offered on
# every question (71212cc) they cost ~60% more input tokens a run, and the
# extra rules moved answers that had nothing to do with them (Belmont's
# patch test lost "48 hours"; Ridgeline read "how long until I get the
# quote" as an order lookup). Same lesson as llm.REFERRAL_RULE: every
# other prompt stays exactly what it was.
_DAYS = r"(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?s?"
_TIME_NAMED = re.compile(
    r"\b(?:today|tonight|tomorrow|tmrw|this (?:morning|afternoon|evening)"
    r"|noon|midnight|asap|right now|same[- ]day|weekend"
    r"|(?:this|next|coming) (?:week|month)"
    # A weekday only as a deadline or today's date ("for Saturday", "it's
    # Friday"), not "are you open Mondays".
    rf"|(?:by|on|for|until|till|this|next|it'?s|it is|before) {_DAYS}"
    r"|\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)"
    r"|at (?:\d{1,2}(?::\d{2})?|one|two|three|four|five|six|seven|eight"
    r"|nine|ten|eleven|twelve)\b"
    # "in about 30 hours" too (live 2026-10-17: not counted, so the tool
    # wasn't required).
    r"|in (?:(?:about|around|roughly|under|over|less than|more than|just|only) )?"
    r"(?:\d+|a|an|one|two|three|four|five|six) (?:hours?|days?|weeks?)"
    r"|(?:\d+|one|two|three|four|five|six|seven|a) (?:hours?|days?|weeks?|months?) "
    r"(?:before|out|ahead|away|from now))\b", re.I)
_ABOUT_CANCELLING = re.compile(r"\b(?:cancel\w*|refund\w*|deposits?)\b", re.I)


# A follow-up keeps the tools: "what about a wedding cake?" after a deposit
# question names neither a time nor cancelling, and on the live site
# (2026-10-03) it got "I'm not sure what the deposit policy is". The last
# FOLLOW_UP_TURNS customer messages count as well as this one.
FOLLOW_UP_TURNS = 2


# "How soon / the earliest" names no time but asks the notice question
# (2026-10-18): offered and, when the top excerpt gives a notice, required.
# Only when the retrieved excerpts give a notice period: "how soon can you
# start" at Ridgeline (no notice in its documents) stays tool-free, as
# 5e69c3f decided (extra tools moved answers that didn't need them).
_ASKS_EARLIEST = re.compile(r"\b(?:earliest|soonest|how soon|as soon as possible)\b", re.I)
_GIVES_NOTICE = re.compile(r"\bnotice\b", re.I)


def _wants_tools(text, config):
    """Which tools one message calls for: a set of "notice", "cancellation".

    Separately, not all-or-nothing: once Belmont had cancellation rules,
    "can I get my colour done at 4pm" (a time, nothing about cancelling)
    started getting the cancellation tool and rules too, and answered "which
    day?" instead of "the last colour goes in at 3pm", 3/3 (2026-10-03).
    """
    wanted = set()
    if _TIME_NAMED.search(text or ""):
        wanted.add("notice")
    if cancellation(config) and _ABOUT_CANCELLING.search(text or ""):
        wanted.add("cancellation")
    return wanted


# A day to count from. Offered on any "when?" (2026-10-04), the tool made
# the model ask for a start date it didn't have: "how long until I get the
# quote" got "when is your visit?" instead of "within three business days".
_DAY_TO_COUNT_FROM = re.compile(
    r"\b(?:yesterday|today|tonight|tomorrow|last (?:week|" + _DAYS + r")"
    r"|this (?:week|morning|afternoon)|next (?:week|" + _DAYS + r")|" + _DAYS + r")\b",
    re.I)
_BUSINESS_DAYS = re.compile(r"\bbusiness days?\b", re.I)


def needs_tools(message, config, history=None, facts=None):
    """The tools this message (or the customer's last couple) calls for.

    A set; empty means none, and the prompt is exactly the standing one.
    `facts` are the retrieved excerpts: "business_days" is wanted only when
    they count in business days and the customer gives a day to count from.
    """
    earlier = [m.get("content", "") for m in (history or [])
               if m.get("role") == "user"][-FOLLOW_UP_TURNS:]
    wanted = set()
    for text in [message, *earlier]:
        wanted |= _wants_tools(text, config)
    if _ASKS_EARLIEST.search(message or "") and any(_GIVES_NOTICE.search(f or "")
                                                     for f in (facts or [])):
        wanted.add("notice")
    if (any(_BUSINESS_DAYS.search(f or "") for f in (facts or []))
            and (_DAY_TO_COUNT_FROM.search(message or "")
                 or _TIME_NAMED.search(message or ""))):
        wanted.add("business_days")
    return wanted


_ORDER_BY_CUTOFF = re.compile(
    r"\b(?:order(?:ed|s)?|the order) by \d{1,2}(?::\d{2})?\s*(?:am|pm)", re.I)


def must_check_notice(message, facts=None):
    """A time is named and the closest excerpt sets an order-by cutoff: the
    first call is check_notice, not optional. Offered, it was skipped (Friday
    at five, journey_eval 2026-10-11), and the reply then sold a tray for
    another day. Only the top excerpt counts, so a delivery-time question
    that happens to pull in the tray section isn't forced."""
    top = (facts or [None])[0]
    if top and _ASKS_EARLIEST.search(message or "") and _GIVES_NOTICE.search(top):
        return True
    return bool(top and _ORDER_BY_CUTOFF.search(top)
                and _TIME_NAMED.search(message or ""))


def must_apply_cancellation(message, config, history=None):
    """A cancellation question that says when: the tool is called, not optional.

    Live (2026-10-03), "I cancelled three days before, do I get my deposit
    back?" got "which kind of cake?" though the answer is the same for every
    cake, and the customer's reply "custom cake" then started an order.
    The eval passed the same question with the model pinned; the live site
    isn't pinned. When the message names both cancelling and a time, the
    first call requires cancellation_outcome.

    The timing can come from the customer's last couple of messages too.
    journey_eval, 2026-10-04: "no, I want to know about a cake I already
    ordered and cancelled" (timing given two messages earlier) wasn't forced,
    and the guardrail "you cannot look up existing orders" won: "call us".
    """
    text = message or ""
    if not cancellation(config):
        return False
    earlier = [m.get("content", "") for m in (history or [])
               if m.get("role") == "user"][-FOLLOW_UP_TURNS:]
    # A follow-up about the charge counts as a cancellation question when
    # the customer was just asking about cancelling: "Is there any way I can
    # avoid that charge?" names neither, wasn't required to use the tool, and
    # said "you're within 24 hours" at 30 hours out (journey_eval 2026-10-17).
    about = (_ABOUT_CANCELLING.search(text)
             or (_ABOUT_THE_CHARGE.search(text)
                 and any(_ABOUT_CANCELLING.search(t or "") for t in earlier)))
    if not about:
        return False
    return any(_TIME_NAMED.search(t or "") for t in [text, *earlier])


_ABOUT_THE_CHARGE = re.compile(
    r"\b(?:charged?|fees?|costs?|pay|penalty|avoid|refund\w*|money back)\b", re.I)


# ---------------------------------------------------------------------------
# Which service: from the customer's words, not the model's guess
# ---------------------------------------------------------------------------

def _named(name):
    """A service name as a pattern: whole words, colour or color."""
    return re.compile(r"\b" + re.escape(name.lower()).replace("colour", "colou?r") + r"\b", re.I)


def services_named(text, config):
    """The business's services this text names, longest first wins: "my cut
    and colour" names "cut and colour", not also "colour"."""
    import policy_form
    taken, found = [], []
    for name in sorted(policy_form.service_names(config), key=len, reverse=True):
        for m in _named(name).finditer(text or ""):
            if not any(m.start() < b and a < m.end() for a, b in taken):
                taken.append((m.start(), m.end()))
                if name not in found:
                    found.append(name)
    return found


def kind_from_words(arguments, texts, config):
    """cancellation_outcome's arguments with `kind` set from what the
    customer said, where services have their own rules. journey_eval,
    2026-10-17, 1 run in 3: "My cut and colour is in about 30 hours" was
    looked up as kind "colour" (and charged 50%) though only plain colour
    was ticked, and the tool's description says "not one that includes
    it". `texts` are the customer's messages, latest first; the first that
    names exactly one service decides: a service with its own rules is
    that kind, any other is everything else. Two services named, or none,
    and the model's choice stands."""
    # "service" is ours, not the model's: only ever a name from the
    # business's own list, set below. Anything the model sent is dropped.
    arguments = {k: v for k, v in arguments.items() if k != "service"}
    policy = cancellation(config)
    if not policy or not _by_service(policy):
        return arguments
    for text in texts:
        named = services_named(text, config)
        if len(named) > 1:
            return arguments
        if named:
            kind = named[0] if named[0] in policy["services"] else policy.get("what", "order")
            if arguments.get("kind") != kind:
                log.info("cancellation_outcome kind %r -> %r, from the customer's words",
                         arguments.get("kind"), kind)
            return dict(arguments, kind=kind, service=named[0])
    return arguments


# How far ahead, from the customer's words. Only the two shapes that say
# THEIR timing: "in (about) 30 hours" and "3 days before / out / away".
# Not "do I need 24 hours' notice?", which is a question about the rule.
_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
                 "twelve": 12}
_AMOUNT = r"(\d+(?:\.\d+)?|" + "|".join(_NUMBER_WORDS) + r")"
_UNIT = r"(hours?|hrs?|days?|weeks?)"
_TIMING_SAID = re.compile(
    r"\bin (?:(?:about|around|roughly|under|over|less than|more than|just|only|like) )?"
    + _AMOUNT + r" " + _UNIT + r"\b"
    r"|\b" + _AMOUNT + r" " + _UNIT + r" (?:before|out|ahead|away|from now|early)\b", re.I)
_UNIT_HOURS = {"h": 1, "d": 24, "w": 168}


def hours_said(text):
    """The hours ahead this message says, or None (none said, or two that
    disagree). "in about 30 hours" -> 30; "three days before" -> 72."""
    found = set()
    for m in _TIMING_SAID.finditer(text or ""):
        amount, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        n = _NUMBER_WORDS.get(amount.lower())
        n = float(amount) if n is None else n
        found.add(n * _UNIT_HOURS[unit[0].lower()])
    return found.pop() if len(found) == 1 else None


def timing_from_words(arguments, texts):
    """cancellation_outcome's arguments with the timing the customer said.
    journey_eval, 2026-10-18, 1 run in 3: the follow-up "Is there any way
    I can avoid that charge?" (after "in about 30 hours") was looked up
    with hours_before 0, and the reply said "within 24 hours". `texts` are
    the customer's messages, latest first; the first that says how far
    ahead decides. A start time the model worked out (starts_at, from "9am
    tomorrow") is left alone, and so is anything when no hours are said."""
    if arguments.get("starts_at"):
        return arguments
    for text in texts:
        hours = hours_said(text)
        if hours is not None:
            if arguments.get("hours_before") != hours or arguments.get("days_before") is not None:
                log.info("cancellation_outcome timing %r/%r -> %r hours, from the customer's words",
                         arguments.get("hours_before"), arguments.get("days_before"), hours)
            out = {k: v for k, v in arguments.items() if k != "days_before"}
            out["hours_before"] = hours
            return out
    return arguments


def arguments_from_words(arguments, texts, config):
    """Both corrections: which service, and how far ahead."""
    return timing_from_words(kind_from_words(arguments, texts, config), texts)


# ---------------------------------------------------------------------------
# After the reply: no false hope
# ---------------------------------------------------------------------------

# Sentences that offer a way out the rules don't have, when the tool said a
# cheaper window has passed. Live, Belmont, 2026-10-17, one after another,
# each after a prompt fix for the one before: "if you can wait another 18
# hours… no charge", "wait another 6 hours to get past the 24-hour mark",
# "the good news is you've still got time", "you've still got a bit of
# time — if you need to reschedule instead of cancelling, just call us".
# The prompt says not to; like llm.without_repeated_number, this makes it so.
_FALSE_HOPE = re.compile(
    r"\bstill (?:got|have|has)\b[^.!?]{0,25}\btime\b|\bthere'?s still time\b"
    r"|\bstill time to\b|\bif you (?:can |could )?wait\b"
    r"|\bwait (?:just )?(?:another|a few|an? |until|\d)"
    r"|\b(?:hit|get past|reach) the \d+[- ]hour mark", re.I)
# Rescheduling offered as the way round it, where moving counts the same.
_RESCHEDULE_INSTEAD = re.compile(
    r"\b(?:reschedul\w*|move it|moving it|move (?:the|your) \w+)\b[^.!?]*"
    r"\b(?:instead|rather than|avoid|work something out|see what works|no charge|for free)\b"
    r"|\b(?:instead of|rather than) cancel\w*[^.!?]*\b(?:reschedul\w*|mov(?:e|ing))\b", re.I)


def without_false_hope(reply, results, config):
    """The reply without sentences that offer a way out of a charge that
    can't be avoided. `results` are this turn's cancellation_outcome
    results; this acts only when one of them said a cheaper window has
    passed ("too late for that now"), so "you still have time to cancel
    for free" at 3 days out is left alone. Where moving counts the same as
    cancelling and a "reschedule instead" sentence goes, the reply says
    so. Never empty: if nothing is left, the rule itself, from the result."""
    late = [r for r in (results or []) if "too late for that now" in (r.get("say") or "")]
    policy = cancellation(config)
    if not reply or not late or not policy:
        return reply
    moving = "mov" in _words(policy)[1]
    kept, dropped, rescheduling = [], False, False
    for sentence in re.split(r"(?<=[.!?])\s+", reply.strip()):
        instead = moving and bool(_RESCHEDULE_INSTEAD.search(sentence))
        if instead or _FALSE_HOPE.search(sentence):
            dropped = True
            rescheduling = rescheduling or instead
            continue
        kept.append(sentence)
    if not dropped:
        return reply
    if rescheduling:
        what = policy.get("what", "order")
        kept.append(f"Moving the {what} counts the same as cancelling, so the same charge applies.")
    text = " ".join(kept).strip()
    if not text or (rescheduling and len(kept) == 1):
        r = late[0]
        acting = _words(policy)[1]
        outcome = "; ".join(r.get("outcomes", {}).values())
        text = f"{acting[0].upper() + acting[1:]} {r.get('window')}: {outcome}." + (
            f" {kept[-1]}" if rescheduling else "")
    log.info("Took a false-hope sentence out of a cancellation answer")
    return text


def tools_for(config, which=("notice", "cancellation")):
    tools = []
    if "notice" in which:
        tools.append(CHECK_NOTICE_TOOL)
    if "business_days" in which:
        tools.append(ADD_BUSINESS_DAYS_TOOL)
    if "cancellation" in which and cancellation(config):
        tools.append(cancellation_tool(config))
    return tools


def _rule(text):
    import textwrap
    return "\n" + textwrap.fill(text, 74, initial_indent="- ",
                                 subsequent_indent="  ")


def prompt_section(config, which=("notice", "cancellation")):
    """Rules (and rendered policy) added to the prompt with the tools."""
    text = "\n\nRules for this question, on top of the answering rules:"
    if "notice" in which:
        text += _rule(
            "When the customer names a time they need something by and the "
            "facts give a notice period for it, call check_notice and answer "
            "from its result: say plainly whether the notice is met, and if it "
            "isn't, that it can't be promised for that time. Missed notice is "
            "never \"just under\" or \"right at\" the window. If the facts "
            "also give an order-by time for it, pass that too. With no time "
            "named (\"how much notice for a cake?\"), state the notice and "
            "don't call it.")
    if "business_days" in which:
        text += _rule(
            "When the facts give a time in business days and the customer "
            "gives a day to count from, call add_business_days and answer "
            "from its result: say the business-day figure from the facts AND "
            "the date it comes to. Don't count the days yourself: weekends "
            "and closed days don't count.")
    policy = cancellation(config)
    if policy and "cancellation" in which:
        what = policy.get("what", "order")
        several = len(_kinds(policy)) > 1
        text += _rule(
            "For a question about cancelling, refunds or getting a deposit "
            "back, call cancellation_outcome straight away when they've said "
            "or implied how far ahead, and answer from its result: state the "
            "rule and what it means for them. "
            + (f"Don't ask which kind of {what} or how much they paid before "
               f"answering. If the result is the same for every {what}, say "
               "so. " if several else "")
            # "What's the charge if I cancel my appointment?" got only "how
            # far ahead are you cancelling?" (live, Belmont, 2026-10-17): a
            # question with no answer in it. The rules ARE the answer.
            + "With no timing given, don't just ask: state the rules below "
              "(each window and what it costs, in a sentence or two), then ask "
              f"how far ahead their {what} is if they want the exact figure.")
        # Live, Belmont, 2026-10-17, twice: "if you can wait another 6 hours
        # to get past the 24-hour mark, there'd be no charge". Backwards, and
        # the tool's own warning wasn't enough: the second time, the chat
        # (kept across reloads) still held the first wrong answer. A standing
        # rule, so it holds with or without the tool, and corrects history.
        text += _rule(
            f"Time only runs one way: the closer the {what}, the less notice, "
            "so cancelling or moving later never costs less. Never suggest "
            "waiting, or cancelling later, as a way to pay less or avoid a "
            "charge. If a cheaper window has passed, say plainly that it's too "
            "late for it, and never that they still have time for it. If an "
            "earlier reply in this conversation suggested waiting, it was "
            "wrong: correct it.")
        text += "\n\n" + policy_text(config)
    return text


def run_tool(name, arguments, config, now=None):
    """Run one tool call. Never raises: a bad call becomes an error result
    the model can read and recover from."""
    try:
        arguments = dict(arguments or {})
        if name == "check_notice":
            return check_notice(arguments.get("needed_by"),
                                arguments.get("notice_hours"),
                                arguments.get("what"), config, now,
                                order_by=arguments.get("order_by"),
                                order_by_days=arguments.get("order_by_days"))
        if name == "add_business_days":
            return add_business_days(arguments.get("start"),
                                     arguments.get("business_days"),
                                     up_to=arguments.get("up_to_business_days"),
                                     what=arguments.get("what"),
                                     config=config)
        if name == "cancellation_outcome" and cancellation(config):
            return cancellation_outcome(
                config,
                starts_at=arguments.get("starts_at"), now=now,
                hours_before=arguments.get("hours_before"),
                days_before=arguments.get("days_before"),
                kind=arguments.get("kind"),
                amount_paid=arguments.get("amount_paid"),
                order_price=arguments.get("order_price"),
                service=arguments.get("service"))
        if name == "menu_price":
            import menu
            if menu.items(config):
                return menu.menu_price(config, arguments.get("order"))
        return {"error": f"no tool called {name!r} here"}
    except Exception as e:
        return {"error": f"couldn't work that out: {e}"}

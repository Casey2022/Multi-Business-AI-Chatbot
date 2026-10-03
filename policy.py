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
import re
from datetime import datetime, timedelta

import clock


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


def check_notice(needed_by, notice_hours, what, config=None, now=None):
    """How much time there is before `needed_by`, against the notice needed.

    Deliberately says nothing about opening hours: whether the shop is open
    at the moment the order would have to be placed is a separate question,
    and folding it in here would make this answer wrong in a new way.
    """
    now = clock.business_now(config, now)
    needed = _parse_local(needed_by)
    notice = float(notice_hours)
    if notice < 0:
        raise ValueError("notice_hours can't be negative")
    available = (needed - now).total_seconds() / 3600
    latest = needed - timedelta(hours=notice)
    earliest = now + timedelta(hours=notice)
    what = (what or "this").strip()
    result = {
        "now": when(now),
        "needed_by": when(needed),
        "hours_available": round(max(available, 0), 1),
        "notice_needed_hours": notice,
        "enough_notice": available >= notice,
        "order_by": when(latest),
        "earliest_ready_if_ordered_now": when(earliest),
    }
    if available < 0:
        result["say"] = (f"{when(needed)} has already passed; it's "
                         f"{when(now)} now.")
    elif available >= notice:
        result["say"] = (f"From now ({when(now)}) to {when(needed)} is "
                         f"{span(available)}. The notice for {what} is "
                         f"{span(notice)}, so there is enough time if it's "
                         f"ordered by {when(latest)}.")
    else:
        result["short_by_hours"] = round(notice - available, 1)
        gap = (f"It's {when(now)} now and it's wanted right away"
               if round(available * 60) <= 0 else
               f"From now ({when(now)}) to {when(needed)} is only "
               f"{span(available)}")
        result["say"] = (f"{gap}. The notice for {what} is "
                         f"{span(notice)}, so the notice is missed by "
                         f"{span(notice - available)}: it can't be promised for "
                         f"{when(needed)}. To have it then, it needed ordering "
                         f"by {when(latest)}. Ordered now, the earliest it can "
                         f"be ready is {when(earliest)}.")
    return result


# ---------------------------------------------------------------------------
# Cancellation windows
# ---------------------------------------------------------------------------

def cancellation(config):
    return (((config or {}).get("policies") or {}).get("cancellation")) or None


def _kinds(policy):
    return list((policy.get("deposits") or {}).keys())


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
    if spec == "refund_deposit":
        return "the deposit comes back in full"
    if spec == "forfeit_deposit":
        return "the whole deposit is forfeited and none of it comes back"
    if spec == "charge_full_price":
        return "the full order price is charged"
    if isinstance(spec, dict) and "keep_percent" in spec:
        p = spec["keep_percent"]
        text = (f"{p:g}% of the order price is kept, and only what was paid "
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
                         kind=None, amount_paid=None, order_price=None):
    policy = cancellation(config)
    if not policy:
        raise ValueError("this business has no cancellation policy on file")
    if hours_before is None and days_before is None:
        raise ValueError("say how far ahead: hours_before or days_before")
    hours = float(hours_before if hours_before is not None
                  else float(days_before) * 24)
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

    ahead = (f"after {policy.get('before', 'the order date')}" if hours < 0
             else f"{span(hours)} before {policy.get('before', 'the order date')}")
    # The rule first, then what it means for them. With the window second
    # ("Cancelling 3 days before falls under ..."), the model kept the
    # outcome and dropped the rule: right answer, no reason (rag_eval
    # 2026-10-02, 3/3).
    if kind:
        rule = f"for a {kind}, {texts[kind]}"
    elif same:
        rule = (f"{texts[kinds[0]]}, for every {what} "
                f"({' and '.join(kinds)} alike)")
    else:
        rule = "; ".join(f"for a {k}, {t}" for k, t in texts.items())
    say = (f"The rule: cancelling {label}, {rule}. Cancelling {ahead} falls "
           f"in that window, so that is what applies.")
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
    lines = [f"{policy.get('title', 'Deposits and cancellation')}:",
             "Deposits: " + "; ".join(f"{k}, {deposit_text(policy, k)}"
                                      for k in kinds) + "."]
    for i, window in enumerate(_windows(policy)):
        label = window_label(policy, i)
        specs = {k: _outcome_for(window, k) for k in kinds}
        texts = {k: outcome_text(s, policy, k) for k, s in specs.items()}
        if len({json.dumps(s, sort_keys=True) for s in specs.values()}) == 1:
            lines.append(f"Cancelled {label}: {texts[kinds[0]]}, for every "
                         f"{policy.get('what', 'order')}.")
        else:
            lines.append(f"Cancelled {label}: " + "; ".join(
                f"{k}, {t}" for k, t in texts.items()) + ".")
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
        "today) and the facts give a notice period for that thing. It "
        "returns the hours available, whether that is enough, the latest "
        "time it could have been ordered, and a sentence to base the answer "
        "on. Do this arithmetic here, never in your head."),
    "input_schema": {
        "type": "object",
        "properties": {
            "needed_by": {
                "type": "string",
                "description": (
                    "When the customer needs it, business-local, as "
                    "YYYY-MM-DDTHH:MM, worked out from today's date and time "
                    "in the prompt. A day with no time: that day's opening "
                    "time. 'Today' or 'now' with no time: right now.")},
            "notice_hours": {
                "type": "number",
                "description": (
                    "The notice the facts give for this thing, in hours "
                    "(3 days = 72, 2 weeks = 336).")},
            "what": {
                "type": "string",
                "description": "What it is, as the facts name it."},
        },
        "required": ["needed_by", "notice_hours", "what"],
    },
}


def cancellation_tool(config):
    policy = cancellation(config)
    what = policy.get("what", "order")
    return {
        "name": "cancellation_outcome",
        "description": (
            "Apply this business's deposit and cancellation rules to one "
            "cancellation. Use it whenever the customer asks what happens if "
            "they cancel, or whether they get a deposit back, and says or "
            "implies how far ahead (three days before, a month out, "
            "tomorrow). Call it straight away with the timing they gave: it "
            "needs nothing else, so never ask which kind or how much they "
            "paid first. Answer from the sentence it returns; don't "
            "recompute or soften it."),
        "input_schema": {
            "type": "object",
            "properties": {
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
                    "description": f"Which kind of {what}, only if the "
                                   f"customer already said. Leave it out "
                                   f"otherwise: the result then covers "
                                   f"every kind."},
            },
        },
    }


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
    r"|in (?:\d+|a|an|one|two|three|four|five|six) (?:hours?|days?|weeks?)"
    r"|(?:\d+|one|two|three|four|five|six|seven|a) (?:hours?|days?|weeks?|months?) "
    r"(?:before|out|ahead|away|from now))\b", re.I)
_ABOUT_CANCELLING = re.compile(r"\b(?:cancel\w*|refund\w*|deposits?)\b", re.I)


def needs_tools(message, config):
    """Does this message get the policy tools (and the rules for them)?"""
    text = message or ""
    return bool(_TIME_NAMED.search(text)
                or (cancellation(config) and _ABOUT_CANCELLING.search(text)))


def tools_for(config):
    tools = [CHECK_NOTICE_TOOL]
    if cancellation(config):
        tools.append(cancellation_tool(config))
    return tools


def _rule(text):
    import textwrap
    return "\n" + textwrap.fill(text, 74, initial_indent="- ",
                                 subsequent_indent="  ")


def prompt_section(config):
    """Rules (and rendered policy) added to the prompt with the tools."""
    text = "\n\nRules for this question, on top of the answering rules:" + _rule(
        "When the customer names a time they need something by and the facts "
        "give a notice period for it, call check_notice and answer from its "
        "result: say plainly whether the notice is met, and if it isn't, that "
        "it can't be promised for that time. Missed notice is never \"just "
        "under\" or \"right at\" the window. With no time named (\"how much "
        "notice for a cake?\"), state the notice and don't call it.")
    policy = cancellation(config)
    if policy:
        what = policy.get("what", "order")
        text += _rule(
            "For a question about cancelling, refunds or getting a deposit "
            "back, call cancellation_outcome straight away when they've said "
            "or implied how far ahead, and answer from its result: state the "
            "rule and what it means for them. Don't ask which kind of "
            f"{what} or how much they paid before answering. If the result "
            f"is the same for every {what}, say so. With no timing given, "
            "state the rules below.")
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
                                arguments.get("what"), config, now)
        if name == "cancellation_outcome" and cancellation(config):
            return cancellation_outcome(
                config,
                hours_before=arguments.get("hours_before"),
                days_before=arguments.get("days_before"),
                kind=arguments.get("kind"),
                amount_paid=arguments.get("amount_paid"),
                order_price=arguments.get("order_price"))
        return {"error": f"no tool called {name!r} here"}
    except Exception as e:
        return {"error": f"couldn't work that out: {e}"}

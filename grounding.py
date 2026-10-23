"""grounding.py — facts in a reply that nothing the assistant read contains.

Preview answers (preview.py) shows an owner how the assistant will answer
after they publish. A reply can be fluent, on topic and wrong in one
detail: a price nobody wrote down, a service the business doesn't offer.
The owner can't tell by reading the reply, because the reply never says
which parts came from their documents. This marks the parts that didn't.

"What it read" is everything the reply could rely on, as get_llm_reply
collects it (its `sources`): the system prompt (business details, hours,
phone, the retrieved knowledge-base excerpts and generated lines), the
conversation, and each tool result. A fact in none of those was made up,
or worked out by the model without a tool.

Two checks, for two kinds of fact:

- Numbers, in code: every price, time, percentage, phone number and other
  number in the reply (digits, or a word number before a unit: "three
  weeks") must appear in the sources. Exact, cheap, and never argues. It
  can't tell a right number from the same number used for the wrong thing
  (24 hours for a patch test, where 24 hours is the cancellation window).
- Claims, by the model: a statement a customer would act on ("free parking",
  "certified safe for celiacs") that the sources don't support. It quotes
  the reply's own words, and a quote that isn't in the reply is dropped.

Measured on evals/grounding_cases.json (grounding_eval.py).
"""

import json
import logging
import re

log = logging.getLogger("grounding")

MAX_FLAGS = 4
MAX_SOURCE_CHARS = 24000

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50,
    "sixty": 60, "ninety": 90,
}
_WORD = "|".join(sorted(_WORD_NUMBERS, key=len, reverse=True))
# A number, in digits or words. Not part of a price ($22), a clock time
# (9:45pm, 10pm) or a word.
_NUM = r"(?<![\w.$:])(\d[\d,]*(?:\.\d+)?|(?:" + _WORD + r"))(?![\w:])"
_NUM_RE = re.compile(_NUM, re.I)
# What a number counts: "5 miles", "18-inch", "4 days", "50%".
_UNITS = {"minute": "minute", "minutes": "minute", "min": "minute", "mins": "minute",
          "hour": "hour", "hours": "hour", "day": "day", "days": "day",
          "week": "week", "weeks": "week", "month": "month", "months": "month",
          "year": "year", "years": "year", "mile": "mile", "miles": "mile",
          "people": "people", "person": "people", "guests": "people",
          "inch": "inch", "inches": "inch", "%": "%", "percent": "%"}
_UNIT_AFTER = re.compile(
    r"\s*(?:-\s*)?(?:business\s+)?(%|percent\b|(?:minutes?|mins?|hours?|days?|weeks?"
    r"|months?|years?|miles?|people|person|guests|inch(?:es)?)\b)", re.I)
_RANGE_AFTER = re.compile(r"\s*(?:to|or|-|–)\s*" + _NUM, re.I)
_PHONE = re.compile(r"\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}\b")
_MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")
_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)", re.I)
_CLOCK_24 = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_HOURS_IN = {"hour": 1, "day": 24, "week": 168}


def _value(text):
    text = text.lower()
    return float(_WORD_NUMBERS[text]) if text in _WORD_NUMBERS else float(text.replace(",", ""))


def _minutes(hour, minute, meridiem):
    hour, minute = int(hour), int(minute or 0)
    meridiem = (meridiem or "").lower().replace(".", "")
    if meridiem == "pm" and hour != 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return hour * 60 + minute


def _mentions(text, skip=()):
    """[(start, end, value, unit or None, in_range)] for the numbers in
    `text`, where end covers the unit. In a range ("3 to 4 weeks", "five to eight weeks")
    both numbers take the unit after the second. A word number without a
    unit isn't a fact ("one of our estimators") and is left out."""
    found, ranged = [], set()
    for m in _NUM_RE.finditer(text):
        if any(s < m.end() and m.start() < e for s, e in skip):
            continue
        after, unit, end = m.end(), None, m.end()
        rng = _RANGE_AFTER.match(text, after)
        if rng:
            ranged |= {m.start(), rng.start(1)}
        u = _UNIT_AFTER.match(text, rng.end() if rng else after)
        if u:
            unit = _UNITS[u.group(1).lower()]
            end = u.end() if not rng else after
            if not rng:
                end = u.end()
        if m.group(1).lower() in _WORD_NUMBERS and not unit:
            continue
        found.append((m.start(), end, _value(m.group(1)), unit))
    return [(s, e, v, u, s in ranged) for s, e, v, u in found]


class _Facts:
    """The numbers in the sources, by kind."""

    def __init__(self, text):
        self.phones = {re.sub(r"\D", "", m.group()) for m in _PHONE.finditer(text)}
        self.money = {_value(m.group(1)) for m in _MONEY.finditer(text)}
        self.times = {_minutes(*m.groups()) for m in _TIME.finditer(text)}
        self.times |= {int(h) * 60 + int(mm) for h, mm in _CLOCK_24.findall(text)}
        mentions = _mentions(text)
        self.numbers = {v for _, _, v, _, _ in mentions} | self.money
        self.pairs = {(v, u) for _, _, v, u, _ in mentions if u}
        # Unitless numbers inside a range ("45 to 70 on Friday evenings"):
        # people drop the unit there, not elsewhere.
        self.bare_ranges = {v for _, _, v, u, r in mentions if not u and r}
        self.hours = {v * _HOURS_IN[u] for _, _, v, u, _ in mentions if u in _HOURS_IN}

    def supports(self, value, unit):
        # Servings are written without a unit ("serves 30-40"), so a count
        # of people matches the bare number. Every other unit must match.
        if not unit or unit == "people":
            return value in self.numbers
        if (value, unit) in self.pairs:
            return True
        # A range the sources give without its unit: "45 to 70 on Friday
        # evenings" supports "45 to 70 minutes" (Preview, Crosstown,
        # 2026-10-23). A lone unitless number doesn't: Ridgeline's text has
        # a bare 3 and 4, and "three to four weeks" is still made up.
        if value in self.bare_ranges:
            return True
        # "4 days" is supported by "96 hours", and the other way round.
        return unit in _HOURS_IN and value * _HOURS_IN[unit] in self.hours


def number_flags(reply, sources):
    """[{"phrase", "why"}] for each number in the reply the sources don't
    contain, with its unit ("10% off" needs a 10%, not any 10). `phrase` is
    the reply's own text, for highlighting."""
    have = _Facts("\n".join(sources or []))
    text = reply or ""
    flags, taken = [], []

    def free(span):
        return not any(s < span[1] and span[0] < e for s, e in taken)

    def flag(span, why):
        taken.append(span)
        flags.append({"phrase": text[span[0]:span[1]].strip(), "why": why})

    for m in _PHONE.finditer(text):
        if re.sub(r"\D", "", m.group()) in have.phones:
            taken.append(m.span())
        else:
            flag(m.span(), "a phone number")
    for m in _MONEY.finditer(text):
        if not free(m.span()):
            continue
        if _value(m.group(1)) in have.money:
            taken.append(m.span())
        else:
            flag(m.span(), "a price")
    for m in _TIME.finditer(text):
        if not free(m.span()):
            continue
        if _minutes(*m.groups()) in have.times:
            taken.append(m.span())
        else:
            flag(m.span(), "a time")
    for start, end, value, unit, _ in _mentions(text, skip=list(taken)):
        if have.supports(value, unit):
            continue
        if not unit:
            word = re.match(r"[\s-]*[A-Za-z]+", text[end:])
            end += word.end() if word else 0
        flag((start, end), "a number")
    return flags[:MAX_FLAGS]


CLAIM_PROMPT = """Below are a business assistant's reply to a customer, and \
everything the assistant had to go on: the business's own information \
(details, hours, policies, knowledge-base excerpts) and the results of the \
business's tools.

List each statement in the reply that a customer could act on and that \
this information does NOT support: a fact about the business, its \
services, prices, times, rules or guarantees that isn't stated there, or \
that contradicts it. Numbers count too, including one stated there for \
something else.

Do NOT list: greetings, thanks, offers to help, how or when to book or \
order ("text 'order'", "book during business hours"), suggestions to call, \
or saying something isn't known. Do NOT list a fact that the information \
states in other words, or that names a service the short way the business \
itself does ("custom cakes" for its custom birthday cakes). When unsure, \
leave it out: each item is shown to the owner as a possible mistake.

Respond with ONLY a JSON object, no preamble:
{"unsupported": [{"quote": "...", "why": "..."}]}
"quote" is copied word for word from the reply, a few words long; "why" is \
a short reason. Empty list if everything is supported. At most %d.

""" % MAX_FLAGS


def claim_flags(reply, sources, model=None):
    """[{"phrase", "why"}] for statements the sources don't support, by the
    model. A quote that isn't in the reply word for word is dropped; on any
    failure, [] (the numbers check still runs)."""
    from llm import _create, as_data, text_of, _note_usage
    if not (reply or "").strip():
        return []
    information = "\n\n".join(sources or [])[-MAX_SOURCE_CHARS:]
    call = dict(max_tokens=500, messages=[{"role": "user", "content": (
        CLAIM_PROMPT + as_data(information, tag="business_information") + "\n"
        + as_data(reply, tag="reply"))}])
    if model:
        call["model"] = model
    try:
        response = _create(**call)
        _note_usage(response, "grounding check")
        raw = text_of(response)
        # The object, wherever it is: a reply that put a sentence before it
        # failed to parse, once in 60 (2026-10-23).
        found = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(found.group() if found else raw)
        items = data.get("unsupported") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise ValueError("unexpected shape")
    except Exception as e:
        log.warning("Grounding check failed: %s", e)
        return []
    flags = []
    for item in items[:MAX_FLAGS]:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "").strip().strip('"').strip()
        if quote and quote.lower() in reply.lower():
            flags.append({"phrase": quote[:120], "why": str(item.get("why") or "")[:160]})
    return flags


def _only_numbers(phrase):
    """True when the phrase is nothing but a phone number, price, time or
    number (with its unit): something number_flags has already judged."""
    rest = phrase
    for pattern in (_PHONE, _MONEY, _TIME):
        rest = pattern.sub(" ", rest)
    rest = _NUM_RE.sub(" ", rest)
    rest = _UNIT_AFTER.sub(" ", " " + rest)
    return not re.sub(r"[\s.,:;()\-–]+", "", rest)


def unsupported(reply, sources, model=None):
    """Both checks, numbers first; a claim the numbers check already covers
    (overlapping text) isn't listed twice."""
    flags = number_flags(reply, sources)
    lowered = (reply or "").lower()
    spans = []
    for f in flags:
        at = lowered.find(f["phrase"].lower())
        if at != -1:
            spans.append((at, at + len(f["phrase"])))
    for f in claim_flags(reply, sources, model):
        at = lowered.find(f["phrase"].lower())
        if at != -1 and any(s < at + len(f["phrase"]) and at < e for s, e in spans):
            continue
        # A quote that's only a number the numbers check found in the
        # sources: code settled it. Sonnet 5.5 flagged Bob's after-hours line,
        # "(585) 555-0123", as "only a possible mismatch" (2026-10-23).
        if _only_numbers(f["phrase"]):
            continue
        flags.append(f)
    return flags[:MAX_FLAGS]

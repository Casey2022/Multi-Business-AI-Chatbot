#!/usr/bin/env python3
"""journey_eval.py: whole conversations through the path a live message takes.

    python3 journey_eval.py                  # every journey
    python3 journey_eval.py pickup           # journeys whose name matches
    python3 journey_eval.py --repeat=3 -v    # three runs each, print transcripts

The other evals each test one layer. rag_eval and qa_eval call the Q&A step
directly; conversation_eval drives the booking flow directly. None of them
meets the routing in between (booking state, keyword rules, the
booking-or-question classifier), and on 2026-10-03 that was where the live
site broke: a deposit question that started an order, "custom cake" read as
a new order, a customer stuck in a delivery form asking for pickup, "it's
Friday at 5" filed as the delivery time. Every single-question eval passed
the same day.

So this sends each message through conversation.process_message, the same
function the webchat and SMS endpoints call, exactly as written. What's
faked: the database is a throwaway copy of yours (the real businesses and
documents; nothing written back), every business's calendar is the
simulated one (never your Google Calendar), the address check, and the
clock, pinned per journey. What's real: the keyword rules, the classifier,
the booking flow, retrieval, the policy tools and the model, unpinned (the
live site doesn't pin temperature, so neither does this; use --repeat).

Checks per turn are properties, not exact strings: the booking state after
the turn, patterns that must or must not appear, and optionally a rubric
graded by the judge (judge.py). Start the app once first so the knowledge
collections exist.
"""

import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from logging_setup import setup_logging
setup_logging(level="WARNING")          # the transcript is the output

import db
_REAL_DB = db.DB_PATH
db.DB_PATH = Path(tempfile.mkdtemp(prefix="journey_")) / "journey.db"
if Path(_REAL_DB).exists():
    shutil.copy(_REAL_DB, db.DB_PATH)   # real businesses and documents
db.init_db()

import clock
import geocode
import judge
import progress
import rubrics
from progress import say
from conversation import process_message
from db import get_state, set_state
from rag_eval import config_for


# ---------------------------------------------------------------------------
# Journeys: each one a live conversation that went wrong, or a path that
# must keep working. "state" is the booking state after the turn.
# ---------------------------------------------------------------------------

NAME_ASK = r"what name|name should i put|under what name|who'?s (the|this) order for"
DEPOSIT_GONE = (r"forfeit|won'?t get (it|your deposit|any of it) back|don'?t get "
                r"(it|your deposit) back|doesn'?t come back|no refund|not refunded"
                r"|none of it|lose (it|the|your)|kept")

# "by 9am today" (already gone) is not the wrong "by 9am tomorrow".
_NOT_TODAY = r"(?!(?::00)?\s*(?:am|a\.m\.)?\s*today)"

# Belmont with colour given its own rules (policy_form, 2026-10-17): colour
# needs 48 hours' notice, everything else 24. Only plain "colour" is ticked,
# so a cut and colour follows the rules for everything else.
_COLOUR_48 = {
    "title": "Cancelling or moving an appointment", "what": "appointment",
    "action": "cancelled or moved", "acting": "cancelling or moving",
    "before": "the appointment", "price": "the service", "services": ["colour"],
    "windows": [{"at_least_hours": 48, "any": "no_charge"},
                {"at_least_hours": 24, "colour": {"charge_percent": 50},
                 "appointment": "no_charge"},
                {"at_least_hours": 0, "any": {"charge_percent": 50}}],
    "notes": []}
# The live Belmont setup on 2026-10-18: only "cut and colour" ticked, 48 hours.
_CUT_AND_COLOUR_48 = dict(_COLOUR_48, services=["cut and colour"], windows=[
    {"at_least_hours": 48, "any": "no_charge"},
    {"at_least_hours": 24, "cut and colour": {"charge_percent": 50}, "appointment": "no_charge"},
    {"at_least_hours": 0, "any": {"charge_percent": 50}}])
# Closed at the time wanted, said either way: by the answer or by the
# booking flow ("We're closed then").
_SAYS_CLOSED = r"\bclosed\b|isn'?t open|aren'?t open|not open|close[sd]? at|after (?:we )?close"
_FREE = r"no charge|free of charge|won'?t be charged|not be charged|no (?:fee|cost)|nothing to pay|at no cost"
# Waiting only ever gives less notice (live, Belmont, 2026-10-17: "if you
# can wait just another 18 hours or so, you'll hit the 48-hour mark").
# The keyword shortcuts' own replies (config rules: cancel_existing), whole,
# so a model that adds "to cancel, give us a call" after the answer passes.
_BELMONT_CANNED = r"give us a call at \S+ \S+ and we'?ll sort it out"
_SUNRISE_CANNED = r"call us at \S+ \S+ and we'?ll take care of it right away"
# Rescheduling offered as the way round it; at Belmont moving counts the
# same ("cancelled or moved"). Mirrors policy._RESCHEDULE_INSTEAD.
_RESCHEDULE_INSTEAD = (r"\b(?:reschedul\w*|move it|moving it)\b[^.!?]*\b(?:instead|rather than"
                       r"|avoid|work something out|see what works|no charge|for free)\b")
_WAIT_ADVICE = (r"\bif you (?:can |could )?wait\b|\bwait (?:just )?(?:another|a few|an? |until|\d)"
                r"|(?:hit|get past|reach) the \d+[- ]hour mark"
                # …and "the good news is you've still got time — if you cancel
                # more than 48 hours ahead, there's no charge" (live, 30 hours out).
                r"|still (?:got|have) time|there'?s still time|still time to"
                r"|good news is")

JOURNEYS = [
    {
        "name": "the bakery deposit, four messages (live 2026-10-03)",
        "slug": "sunrise_bakery_and_cafe",
        "clock": (2026, 9, 22, 10, 0),
        "why": "Asked which kind of cake, then 'custom cake' started an "
               "order, the customer couldn't get out, and the follow-up got "
               "'I'm not sure'.",
        "turns": [
            {"say": "I cancelled three days before, do I get my deposit back?",
             "state": "idle",
             # "7[\s-]days": "the 48-hours-to-7-days window" is a right answer
             # that "7 days" failed (2026-10-04). Assert the fact, not a spelling.
             "must": [r"7[\s-]+days?|seven[\s-]+days?|a week", DEPOSIT_GONE],
             "must_not": [r"which (kind|type) of cake", r"custom (cake )?or (a )?wedding( cake)?\?"],
             "rubric": rubrics.DEPOSIT_THREE_DAYS},
            {"say": "custom cake", "state": "idle", "must_not": [NAME_ASK]},
            {"say": "no, I want to know about a cake I already ordered and "
                    "cancelled. Can I get my deposit back?",
             "state": "idle", "must": [DEPOSIT_GONE], "must_not": [NAME_ASK]},
            {"say": "what about a wedding cake?", "state": "idle",
             "must": [DEPOSIT_GONE + r"|same"],
             "must_not": [r"not sure|don'?t know|not certain|can'?t confirm"]},
        ],
    },
    {
        "name": "asking for pickup mid-order at Crosstown (live 2026-10-03)",
        "slug": "crosstown_pizza",
        "clock": (2026, 9, 22, 18, 0),
        "why": "'can I pick it up?' got the phone number and then 'Where "
               "are we delivering to?' again.",
        "turns": [
            {"say": "I'd like to order a pizza for delivery", "state": "collecting"},
            {"say": "can I pick it up?", "state": "idle",
             "must": [r"555-0177"],
             "must_not": [r"where (are we|should we|do we) deliver", r"delivering to\?"]},
        ],
    },
    {
        "name": "Friday at five, food for twenty (live 2026-10-03)",
        "slug": "crosstown_pizza",
        "clock": (2026, 9, 25, 17, 0),
        "why": "Filed 'Friday at 5' as the delivery time; later, applied the "
               "tray cutoff and then told them to order another time.",
        "turns": [
            {"say": "it's Friday at 5, I need food for 20 people",
             # Either path is fine: answered (idle), or an order started.
             "must_not": [r"(order|come back|try) (for|at) (a )?(different|another) (time|day)"],
             "not_filed": {"datetime": r"friday\s+at\s+5"},
             "rubric": rubrics.PARTY_TRAY_FRIDAY_FIVE, "rubric_when": "idle"},
        ],
    },
    {
        "name": "can I order here? (live 2026-09-30)",
        "slug": "sunrise_bakery_and_cafe",
        "clock": (2026, 9, 22, 10, 0),
        "why": "'I can't actually place orders myself... our booking system "
               "will walk you through'. To the customer it's one chat.",
        "turns": [
            {"say": "can I place an order through you?",
             "must": [r"'(order|book)'|\"(order|book)\"|text .{0,3}(order|book)|yes"],
             "must_not": [r"can'?t (actually )?(place|take|process) (an? |your )?orders?",
                          r"separate system"]},
        ],
    },
    {
        "name": "colour at 9am tomorrow, cancelling at 10am (Belmont)",
        "slug": "belmont_hair_studio",
        "clock": (2026, 9, 22, 10, 0),
        "why": "Said 'just under' and that cancelling by 9am tomorrow "
               "avoids the charge; it's 23 hours, so 50%.",
        "turns": [
            {"say": "my colour is at 9am tomorrow and it's 10am now, what if I cancel",
             "state": "idle", "must": [r"50\s?%"],
             # The wrong answer is "cancel by 9 to AVOID the charge"; "if you
             # cancel before 9am tomorrow we'd charge 50%" is right, and the
             # first pattern failed it (2026-10-05). So is "to cancel with no
             # charge you'd need to call by 9am today — that's passed", which
             # the second failed (2026-10-11): 9am TODAY is excluded.
             # "just under" was the hedge that came with the wrong answer;
             # "you're just under 24 hours before, so we'd charge 50%" states
             # the charge and is right (2026-10-11, judge passed it), so a
             # sentence that names the charge is excluded.
             "must_not": [r"just under(?![^.]*(50\s?%|charge))",
                          r"(avoid|no charge|free of charge|without (a |the |any )?charge)"
                          r"[^.]*\b(by|before) 9" + _NOT_TODAY + r"|\b(by|before) 9"
                          + _NOT_TODAY + r"[^.]*"
                          r"(avoid|no charge|free of charge|without (a |the |any )?charge)"],
             "rubric": rubrics.CANCEL_23_HOURS},
        ],
    },
    {
        "name": "service rules: colour needs 48 hours (Belmont)",
        "slug": "belmont_hair_studio", "policy": _COLOUR_48,
        "why": "An owner gave colour its own window; 30 hours' notice is inside it.",
        "turns": [{"say": "My colour appointment is in about 30 hours. What happens if I cancel?",
                   "state": "idle", "must": [r"50\s?%"], "must_not": [_WAIT_ADVICE]}],
    },
    {
        "name": "service rules: asked how to avoid the colour charge (Belmont)",
        "slug": "belmont_hair_studio", "policy": _COLOUR_48,
        "why": "Invites the backwards advice outright: waiting only gives less notice.",
        "turns": [{"say": "My colour appointment is in about 30 hours. What happens if I cancel?",
                   "state": "idle", "must": [r"50\s?%"], "tool": "cancellation_outcome",
                   "must_not": [_WAIT_ADVICE, _RESCHEDULE_INSTEAD]},
                  # Live-style follow-up: no "cancel" in it, but the tool is
                  # required (it said "within 24 hours" at 30 hours without it).
                  {"say": "Is there any way I can avoid that charge?",
                   "state": "idle", "tool": "cancellation_outcome",
                   "must_not": [_WAIT_ADVICE, _RESCHEDULE_INSTEAD, r"within 24 hours"]}],
    },
    {
        "name": "service rules: an earlier wrong 'wait' answer is in the chat (Belmont)",
        "slug": "belmont_hair_studio", "policy": _COLOUR_48,
        "why": "Live 2026-10-17: the chat, kept across reloads, still held 'wait "
               "another 18 hours' and the next answer said 'wait another 6 hours'.",
        "history": [
            ("user", "My colour appointment is in about 30 hours. What happens if I cancel?"),
            ("assistant", "If you cancel a colour appointment between 24 and 48 hours before, we "
                          "charge 50% of the service. Since you're 30 hours out, that's where you "
                          "fall — but if you can wait just another 18 hours or so, you'll hit the "
                          "48-hour mark and there's no charge at all."),
        ],
        "turns": [{"say": "My colour appointment is in about 30 hours. What happens if I cancel?",
                   "state": "idle", "must": [r"50\s?%"], "tool": "cancellation_outcome",
                   "must_not": [_WAIT_ADVICE]}],
    },
    {
        "name": "service rules: only cut and colour ticked, plain colour at 30 hours (Belmont)",
        "slug": "belmont_hair_studio", "policy": _CUT_AND_COLOUR_48,
        "why": "Live 2026-10-18: right answer (free), then 'colour appointments get "
               "charged differently', a shortening of 'cut and colour'.",
        "turns": [{"say": "My colour appointment is in about 30 hours. What happens if I cancel?",
                   "state": "idle", "must": [_FREE], "tool": "cancellation_outcome",
                   "rubric": rubrics.COLOUR_IS_NOT_CUT_AND_COLOUR}],
    },
    {
        "name": "service rules: a haircut at 30 hours is free (Belmont)",
        "slug": "belmont_hair_studio", "policy": _COLOUR_48,
        "why": "Colour's 48-hour window must not leak onto other services.",
        "turns": [{"say": "My haircut is in about 30 hours. What happens if I cancel?",
                   "state": "idle", "must": [_FREE], "tool": "cancellation_outcome",
                   "rubric": rubrics.SERVICE_RULES_FREE_AT_30}],
    },
    {
        "name": "service rules: a cut and colour follows the general rule (Belmont)",
        "slug": "belmont_hair_studio", "policy": _COLOUR_48,
        "why": "Only plain colour was ticked; a combined service isn't colour.",
        "turns": [{"say": "My cut and colour is in about 30 hours. What happens if I cancel?",
                   "state": "idle", "must": [_FREE], "tool": "cancellation_outcome",
                   "rubric": rubrics.SERVICE_RULES_FREE_AT_30}],
    },
    {
        "name": "shortcut: a charge question about 'cancel my appointment' (Belmont)",
        "slug": "belmont_hair_studio",
        "why": "The 'cancel my appointment' keyword rule answered 'give us a call' "
               "before the cancellation rules saw it (fixed 2026-10-17: it steps "
               "aside for charge words).",
        "turns": [{"say": "My appointment is in 10 hours. What's the charge if I cancel my appointment?",
                   "state": "idle", "must": [r"50\s?%"], "tool": "cancellation_outcome",
                   "must_not": [_BELMONT_CANNED, _WAIT_ADVICE]}],
    },
    {
        "name": "shortcut: a charge question with no timing gets the rules (Belmont)",
        "slug": "belmont_hair_studio",
        "why": "'What's the charge if I cancel my appointment?' got only 'how far "
               "ahead are you cancelling?' (live 2026-10-17). The rules are the answer.",
        "turns": [{"say": "What's the charge if I cancel my appointment?",
                   "state": "idle", "must": [r"24[\s-]*hours?", r"50\s?%"],
                   "must_not": [_BELMONT_CANNED]}],
    },
    {
        "name": "shortcut: a deposit question about 'cancel my order' (Sunrise)",
        "slug": "sunrise_bakery_and_cafe",
        "clock": (2026, 9, 22, 10, 0),
        "why": "Sunrise's 'cancel my order' rule gave the phone number instead "
               "of the deposit rules (same fix).",
        "turns": [{"say": "If I cancel my order 3 days before, do I get my deposit back?",
                   "state": "idle", "must": [DEPOSIT_GONE], "tool": "cancellation_outcome",
                   "must_not": [_SUNRISE_CANNED]}],
    },
    # Opening hours (2026-10-18): the notice is met in each, and the shop is
    # shut at the time wanted. Measured before changing check_notice, which
    # deliberately ignores opening hours. The baseline (3 runs each): the
    # Monday cake and the 11pm tray started an order, and the booking flow
    # said "We're closed then" with open times, which is right, so those two
    # take either path, and must say it's closed. Muffins at 5pm (0/3: "Yes!")
    # and the earliest pickup (1/3: no tool) must stay questions.
    {
        "name": "opening hours: custom cake for a Monday pickup (Sunrise)",
        "slug": "sunrise_bakery_and_cafe", "clock": (2026, 9, 24, 10, 0),   # Thursday
        "why": "72 hours' notice is met, but Sunrise is closed on Mondays.",
        "turns": [{"say": "Could I pick up a custom birthday cake on Monday at 10am?",
                   "must": [_SAYS_CLOSED],
                   "rubric": rubrics.CLOSED_MONDAY_CAKE, "rubric_when": "idle"}],
    },
    {
        "name": "opening hours: muffins after closing time (Sunrise)",
        "slug": "sunrise_bakery_and_cafe", "clock": (2026, 9, 26, 13, 0),   # Saturday
        "why": "24 hours' notice is met, but Sunrise closes at 3pm.",
        "turns": [{"say": "Is it possible to get a dozen blueberry muffins tomorrow at 5pm?",
                   "state": "idle", "rubric": rubrics.AFTER_CLOSING_MUFFINS}],
    },
    {
        "name": "opening hours: earliest pickup lands on a closed day (Sunrise)",
        "slug": "sunrise_bakery_and_cafe", "clock": (2026, 9, 27, 13, 0),   # Sunday
        "why": "Ready Monday at 1pm by the notice, but closed Mondays: Tuesday 7am.",
        "turns": [{"say": "If I order a dozen muffins today, what's the earliest I could pick them up?",
                   "state": "idle", "rubric": rubrics.EARLIEST_PICKUP_TUESDAY}],
    },
    {
        "name": "opening hours: a party tray after closing (Crosstown)",
        "slug": "crosstown_pizza", "clock": (2026, 9, 22, 10, 0),           # Tuesday
        "why": "Three hours' notice is met, no Thursday cutoff, but closed after 10pm.",
        "turns": [{"say": "Can I get a party tray on Thursday at 11pm?",
                   "must": [_SAYS_CLOSED],
                   "rubric": rubrics.TRAY_AFTER_CLOSE, "rubric_when": "idle"}],
    },
    {
        "name": "opening hours: a day the owner blocked off (Sunrise)",
        "slug": "sunrise_bakery_and_cafe", "clock": (2026, 9, 26, 9, 0),    # Saturday
        "blocks": [("2026-09-27 00:00", "2026-09-28 00:00")],              # Sunday
        "why": "Sunday is blocked off on the Calendar page; notice is met.",
        "turns": [{"say": "Is it possible to get a dozen muffins tomorrow at 11am?",
                   "must": [r"tuesday"],
                   "must_not": [r"vacation|holiday|dentist|training", r"closed (?:on )?sundays"],
                   "rubric": rubrics.BLOCKED_SUNDAY, "rubric_when": "idle"}],
    },
    {
        "name": "a real booking still starts, after a question (Bob's)",
        "slug": "bobs_plumbing",
        "clock": (2026, 9, 22, 10, 0),
        "why": "Guards the routing fixes: an answer to the bot's question is "
               "a question, but 'I'd like to book one' is still a booking.",
        "turns": [
            {"say": "do you do drain cleaning?", "state": "idle"},
            {"say": "great, I'd like to book a drain cleaning tomorrow at 10",
             "state": "collecting"},
        ],
    },
]


# ---------------------------------------------------------------------------
# Running one
# ---------------------------------------------------------------------------

def pin_clock(local, config):
    """Freeze the clock at a business-local moment, for everything."""
    tz = ZoneInfo(clock.timezone_for(config))
    moment = datetime(*local, tzinfo=tz)
    clock.utc_now = lambda: moment.astimezone(ZoneInfo("UTC"))


def prepare(config):
    """Never touch a real calendar or the geocoding API."""
    cal = config.setdefault("calendar", {})
    cal["provider"] = "simulated"
    cal["enabled"] = True
    return config


# What the model had in front of it, per turn: the sections retrieved and
# every tool call with its result. Printed under a failing transcript (and
# with -v), so a failure says where it came from: the facts it was given,
# the tool's answer, or the model's phrasing. Added 2026-10-11 when "Friday
# at five" started failing 3 runs in 8 with nothing in the output to say why.
TRACE = []


def _install_trace():
    import llm
    import policy
    if getattr(llm.retrieve, "_traced", False):
        return
    real_retrieve, real_run_tool = llm.retrieve, policy.run_tool

    def retrieve(query, config):
        found = real_retrieve(query, config)
        TRACE.append("read: " + ", ".join(
            (c.lstrip().splitlines()[0].lstrip("#").split("(")[0].strip()
             if c.strip() else "?") + f" ({1 - d:.2f})" for c, d in (found or []))
            if found else "read: nothing")
        return found
    retrieve._traced = True

    def run_tool(name, arguments, config, now=None):
        result = real_run_tool(name, arguments, config, now)
        TRACE.append(f"tool {name}({_short(arguments)}) -> {_short(result)}")
        return result

    llm.retrieve, policy.run_tool = retrieve, run_tool


def _short(value, limit=260):
    import json
    text = json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def run(journey, verbose=False):
    config = prepare(config_for(journey["slug"]))
    if journey.get("policy"):
        # Rules an owner set on the Cancellation rules page, for this journey only.
        config.setdefault("policies", {})["cancellation"] = journey["policy"]
    if journey.get("blocks"):
        # Time an owner blocked off on the Calendar page, for this journey only.
        config.setdefault("calendar", {}).setdefault("scheduling", {})["blocks"] = [
            {"start": a, "end": b} for a, b in journey["blocks"]]
    business_id = config["business"].get("id")
    if business_id is None:
        return [], [("setup", f"{journey['slug']} isn't registered in the database")]
    pin_clock(journey.get("clock", (2026, 9, 22, 10, 0)), config)
    sender = f"journey_{abs(hash(journey['name'])) % 10**8}"
    set_state(sender, business_id, "idle", pending={})
    conn = db.get_connection()
    conn.execute("DELETE FROM messages WHERE phone = ?", (sender,))
    conn.commit()
    conn.close()
    # "history": messages already in the chat before the first turn, as
    # (role, text). The web chat keeps a conversation across reloads, so an
    # earlier wrong answer can still be there (live 2026-10-17).
    for role, text in journey.get("history", []):
        db.save_message(sender, role, text, business_id,
                        source="customer" if role == "user" else "llm")

    transcript, problems = [], []
    previous = None
    for n, turn in enumerate(journey["turns"], 1):
        TRACE.clear()
        reply = process_message(turn["say"], sender, business_id, config,
                                channel="webchat")
        current = get_state(sender, business_id)
        transcript.append((turn["say"], reply, current["state"], list(TRACE)))
        where = f"turn {n}"
        if not (reply or "").strip():
            problems.append((where, "empty reply"))
            continue
        if previous and reply.strip() == previous.strip():
            problems.append((where, "the same reply twice running"))
        previous = reply
        if "state" in turn and current["state"] != turn["state"]:
            problems.append((where, f"booking state {current['state']!r}, "
                                    f"expected {turn['state']!r}"))
        for pattern in turn.get("must", []):
            if not re.search(pattern, reply, re.I):
                problems.append((where, f"should match /{pattern}/"))
        for pattern in turn.get("must_not", []):
            if re.search(pattern, reply, re.I):
                problems.append((where, f"shouldn't match /{pattern}/"))
        # "tool": the turn must have gone through this tool, the proof that
        # a keyword shortcut didn't answer first (a shortcut reply calls
        # none). Words can't tell: "give us a call" is the canned reply AND
        # a true thing the model may add after the right answer (2026-10-17).
        if turn.get("tool") and not any(line.startswith(f"tool {turn['tool']}(")
                                        for line in TRACE):
            problems.append((where, f"didn't call {turn['tool']}"))
        for key, pattern in turn.get("not_filed", {}).items():
            value = str((current.get("pending") or {}).get(key, ""))
            if value and re.search(pattern, value, re.I):
                problems.append((where, f"filed {key} = {value!r}"))
        rubric = turn.get("rubric")
        if rubric and turn.get("rubric_when", current["state"]) == current["state"]:
            passed, reason = judge.judge(turn["say"], reply, rubric)
            if not passed:
                problems.append((where, f"judge: {reason}"))
    return transcript, problems


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose = "-v" in sys.argv
    repeat = 1
    for flag in sys.argv[1:]:
        if flag.startswith("--repeat="):
            repeat = int(flag.split("=", 1)[1])

    # The address check has its own tests; here it gets out of the way.
    geocode.check_service_area = lambda addr, cfg, business_id=None: {
        "status": "inside", "miles": 2.0, "formatted": addr, "address": addr,
        "reason": "stubbed for journey_eval", "locality": "Rochester",
        "state": "NY", "customer_can_fix": False,
    }

    _install_trace()
    chosen = [j for j in JOURNEYS
              if not args or any(a.lower() in j["name"].lower() for a in args)]
    if not chosen:
        print("no journeys matched")
        return 1

    import textwrap
    failed = 0
    with progress.bar(len(chosen) * repeat, unit="journey") as bar:
        for journey in chosen:
            runs = []
            for _ in range(repeat):
                runs.append(run(journey, verbose))
                bar.update()
            clean = sum(1 for _, p in runs if not p)
            transcript, problems = next(((t, p) for t, p in runs if p), runs[0])
            mark = ("FAIL" if problems else "pass") + (
                f"  [{clean}/{repeat} clean]" if repeat > 1 else "")
            say(f"\n{'─' * 72}\n{mark}  {journey['name']}")
            if problems or verbose:
                if problems:
                    say(f"      why it's here: {journey['why']}")
                for said, reply, state, trace in transcript:
                    say(f"      cust  {said}")
                    for j, line in enumerate(textwrap.wrap(" ".join(reply.split()), 90)):
                        say(f"      {'BOT ' if j == 0 else '    '}  {line}")
                    say(f"            [{state}]")
                    for item in trace:
                        for j, line in enumerate(textwrap.wrap(item, 100)):
                            say(f"            {'·' if j == 0 else ' '} {line}")
                for where, text in problems:
                    say(f"      ✗ {where}: {text}")
            failed += bool(problems)
    say(f"\n{'─' * 72}\n{len(chosen) - failed}/{len(chosen)} journeys clean")
    spent = judge.cost_summary()
    if spent:
        say(spent)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

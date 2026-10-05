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
             # first pattern failed it (2026-10-05).
             "must_not": [r"just under",
                          r"(avoid|no charge|free of charge|without (a |the |any )?charge)"
                          r"[^.]*\b(by|before) 9|\b(by|before) 9[^.]*"
                          r"(avoid|no charge|free of charge|without (a |the |any )?charge)"],
             "rubric": rubrics.CANCEL_23_HOURS},
        ],
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


def run(journey, verbose=False):
    config = prepare(config_for(journey["slug"]))
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

    transcript, problems = [], []
    previous = None
    for n, turn in enumerate(journey["turns"], 1):
        reply = process_message(turn["say"], sender, business_id, config,
                                channel="webchat")
        current = get_state(sender, business_id)
        transcript.append((turn["say"], reply, current["state"]))
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
                for said, reply, state in transcript:
                    say(f"      cust  {said}")
                    for j, line in enumerate(textwrap.wrap(" ".join(reply.split()), 90)):
                        say(f"      {'BOT ' if j == 0 else '    '}  {line}")
                    say(f"            [{state}]")
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

#!/usr/bin/env python3
"""qa_eval.py: several questions in one conversation, against the real model.

Run it:  python3 qa_eval.py [--repeat=3] [scenario filter]

conversation_eval.py scripts BOOKINGS. This scripts the other half: a
customer asking one question after another, with the conversation so far
passed back each time, the way the app does. It exists for defect #6
(2026-08-02): four bakery answers in a row ended "give us a call" — each
right on its own, together a brush-off.

Each scenario mixes questions the documents answer with ones they don't, so
the bot has every reason to reach for the phone. Checks:
- number_not_repeated_soon: the business's number isn't given again within
  3 replies (llm.REFERRAL_LOOKBACK), unless the customer asked how to reach
  us;
- no_back_to_back_referral: no two replies in a row both send them to call;
- every_reply_says_something: no empty or phone-only replies;
- reply_must / reply_must_not: per-scenario patterns for one reply.

Costs a few cents per run (about 5 model calls per scenario). Uses the
real document search, so run it on the Mac with the app's collections
built (start the app once first).
"""

import re
import sys

from dotenv import load_dotenv
load_dotenv()

from logging_setup import setup_logging
setup_logging(level="WARNING")

from datetime import datetime

import progress
from progress import say
from llm import get_llm_reply, _phone_pattern, _REFERRAL_SENTENCE, REFERRAL_LOOKBACK
from rag_eval import config_for

# Tuesday 10am: every business open, nothing about the clock in play.
CLOCK = datetime(2026, 9, 22, 10, 0)

SCENARIOS = [
    {
        "name": "the bakery, five questions (defect #6)",
        "slug": "sunrise_bakery_and_cafe",
        "script": [
            "do you have gluten free cakes?",
            "how much is delivery?",
            "do you use organic flour?",
            "can you put a photo on a cake?",
            "are your kitchens nut free?",
        ],
    },
    {
        "name": "the plumber, questions it can't all answer",
        "slug": "bobs_plumbing",
        "script": [
            "do you charge for service calls?",
            "do you install tankless water heaters?",
            "what brand of fixtures do you use?",
            "are your plumbers licensed?",
            "do you offer financing?",
        ],
    },
    {
        "name": "asking for the number again still gets it",
        "slug": "sunrise_bakery_and_cafe",
        "script": [
            "can you do a three-tier wedding cake?",
            "what flavours do you have?",
            "sorry, what was your phone number again?",
        ],
        "asks_for_number_at": [2],
    },
    {
        # Casey's live test after the FAQ fold (2026-09-30). Turn 4 got "I
        # can't actually place orders myself — text 'order' and our booking
        # system will walk you through": the prompt called the booking flow
        # "a separate system, not you", and the model told the customer so.
        "name": "the live bakery conversation (2026-09-30)",
        "slug": "sunrise_bakery_and_cafe",
        "script": [
            "Hello, do you sell cakes?",
            "just checking, do you make cookies?",
            "Do you have gluten free options?",
            "can I place an order through you?",
            "do you deliver?",
            "so there is no delivery fee for pastry trays over $50?",
        ],
        "reply_must_not": [
            (1, r"^\W*(yes|yep|yeah|we do|we sure do|absolutely)\b",
             "cookies aren't in the documents, so no \"yes\""),
            (3, r"\b(can'?t|cannot|can not|unable to)\b.{0,25}\b(place|take|"
                r"handle|process)\b.{0,15}\borders?\b",
             "this chat IS where they order"),
            (3, r"separate system", "no talk of a separate system"),
        ],
        "reply_must": [
            (3, r"'(order|book)'", "tells them how: text 'order'"),
        ],
    },
]

# The booking-or-question classifier, given what the bot last said
# (llm.classify_and_extract's previous_reply). Live, 2026-10-03, "custom
# cake" in answer to the bot's own "which kind of cake was it?" started an
# order. (slug, bot's previous message, customer's reply, expected intent)
ROUTING = [
    ("sunrise_bakery_and_cafe",
     "I need to know which kind of cake you ordered to give you the right "
     "answer — was it a custom cake or a wedding cake?",
     "custom cake", "question"),
    ("sunrise_bakery_and_cafe",
     "We'd love to make it! Text 'order' to get started, or tell me what "
     "you'd like.",
     "a custom cake for Saturday please", "book"),
    ("sunrise_bakery_and_cafe", None, "I'd like to order a wedding cake for June",
     "book"),
]


def check_routing():
    from llm import classify_and_extract
    from scheduler import get_slot_definitions
    problems = []
    for slug, previous, message, expected in ROUTING:
        config = config_for(slug)
        got = classify_and_extract(message, get_slot_definitions(config), config,
                                   previous_reply=previous).get("intent")
        if got != expected:
            problems.append(f"{message!r} after {str(previous)[:40]!r}: "
                            f"{got}, expected {expected}")
    return problems


def sends_them_to_call(reply, pattern):
    """The number, or a sentence whose point is "go call us" ("Give us a
    call to set up a tasting!"). The same definition the guard in llm.py
    uses; a sentence with real information that mentions calling
    ("…confirm both when you call") doesn't count."""
    if pattern and pattern.search(reply):
        return True
    return any(_REFERRAL_SENTENCE.search(s)
               for s in re.split(r"(?<=[.!?])\s+", reply.strip()))


def run(scenario):
    config = config_for(scenario["slug"])
    history, turns = [], []
    for question in scenario["script"]:
        reply = get_llm_reply(question, list(history), config, temperature=0,
                              now=CLOCK)
        turns.append((question, reply))
        history += [{"role": "user", "content": question},
                    {"role": "assistant", "content": reply}]
    return config, turns


def check(scenario, config, turns):
    problems = []
    pattern = _phone_pattern(config["business"].get("phone"))
    asked = set(scenario.get("asks_for_number_at", []))
    with_number = [i for i, (_, r) in enumerate(turns) if pattern and pattern.search(r)]
    # Not "only once per conversation": the rule (and the guard in llm.py)
    # is "not again within REFERRAL_LOOKBACK replies". qa_eval 2026-09-29
    # failed a conversation for giving it in replies 1 and 5, and reply 5
    # was a severe-allergy answer where the document says to call first.
    unasked = [i for i in with_number if i not in asked]
    for a, b in zip(unasked, unasked[1:]):
        if b - a <= REFERRAL_LOOKBACK:
            problems.append(("number_not_repeated_soon",
                             f"the number was in replies {a + 1} and {b + 1}"))
            break
    for i in asked:
        if i not in with_number:
            problems.append(("number_when_asked",
                             f"asked for the number in turn {i + 1} and didn't get it"))
    refers = [sends_them_to_call(r, pattern) for _, r in turns]
    for i in range(1, len(turns)):
        if refers[i] and refers[i - 1] and i not in asked and i - 1 not in asked:
            problems.append(("no_back_to_back_referral",
                             f"replies {i} and {i + 1} both send them to call"))
            break
    for i, regex, why in scenario.get("reply_must_not", []):
        if i < len(turns) and re.search(regex, turns[i][1], re.I):
            problems.append(("reply_must_not", f"reply {i + 1}: {why}"))
    for i, regex, why in scenario.get("reply_must", []):
        if i < len(turns) and not re.search(regex, turns[i][1], re.I):
            problems.append(("reply_must", f"reply {i + 1}: {why}"))
    for i, (_, r) in enumerate(turns):
        if i in asked:
            continue            # "It's (585) 555-0188." IS the answer there
        rest = pattern.sub("", r) if pattern else r
        if len(re.sub(r"\W", "", rest)) < 12:
            problems.append(("every_reply_says_something",
                             f"reply {i + 1} says almost nothing: {r!r}"))
    return problems


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    repeat = 1
    for flag in sys.argv[1:]:
        if flag.startswith("--repeat="):
            repeat = int(flag.split("=", 1)[1])
    chosen = [s for s in SCENARIOS
              if not args or any(a.lower() in s["name"].lower() for a in args)]
    if not chosen:
        print("no scenarios matched")
        return 1

    import textwrap
    failed = 0
    with progress.bar(len(chosen) * repeat, unit="conversation") as bar:
        for scenario in chosen:
            runs = []
            for _ in range(repeat):
                config, turns = run(scenario)
                runs.append((turns, check(scenario, config, turns)))
                bar.update()
            clean = sum(1 for _, p in runs if not p)
            turns, problems = next(((t, p) for t, p in runs if p), runs[0])
            mark = ("FAIL" if problems else "pass") + (
                f"  [{clean}/{repeat} clean]" if repeat > 1 else "")
            say(f"\n{'─' * 72}\n{mark}  {scenario['name']}")
            if problems or "-v" in sys.argv:
                for q, r in turns:
                    say(f"      cust  {q}")
                    for j, line in enumerate(textwrap.wrap(" ".join(r.split()), 92)):
                        say(f"      {'BOT ' if j == 0 else '    '}  {line}")
                for name, text in problems:
                    say(f"      ✗ {name}: {text}")
            failed += bool(problems)
    say(f"\n{'─' * 72}\nbooking-or-question routing ({len(ROUTING)} checks "
        f"x {repeat}):")
    routing_failed = 0
    for _ in range(repeat):
        for problem in check_routing():
            routing_failed += 1
            say(f"      ✗ {problem}")
    say(f"      {len(ROUTING) * repeat - routing_failed}/{len(ROUTING) * repeat} right")
    say(f"\n{'─' * 72}\n{len(chosen) - failed}/{len(chosen)} scenarios clean")
    return 1 if failed or routing_failed else 0


if __name__ == "__main__":
    sys.exit(main())

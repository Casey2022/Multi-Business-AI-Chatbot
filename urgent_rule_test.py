#!/usr/bin/env python3
"""urgent_rule_test.py: a death that has just happened gets the phone
numbers first, with no booking questions (Miller Funeral, 2026-11-04).

Run it:  python3 urgent_rule_test.py

A funeral home's assistant books pre-planning consultations. Someone
writing "my mother just passed away" must not be asked which funeral home
would suit them, even if they were halfway through booking one. Miller's
"death_just_happened" rule is marked urgent: true, and conversation.py
checks urgent rules before the booking flow (rules.get_urgent_reply).

No API calls: the booking flow and the model are faked and only counted.
"""

import sys
import tempfile
from pathlib import Path

import yaml

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="urgent_test_")) / "test.db"

import booking_state_test as helpers

ROOT = Path(__file__).resolve().parent
PASSED, FAILED = [], []

ROCHESTER, VICTOR = "(585) 424-3700", "(585) 924-5730"

# A death that is happening now, in the words people use.
JUST_DIED = [
    "My mother just passed away",
    "my dad just died at home, what do I do",
    "My husband passed away this morning",
    "Grandma died last night in hospice",
    "hospice called, she's gone",
    "My wife has passed. I don't know what to do.",
    "my father passed away an hour ago, can you help",
    "Our son died today",
    "MY BROTHER JUST DIED",
]

# A death in the past, or a question that only mentions one: the model
# answers these, from the knowledge base.
NOT_NOW = [
    "My father died two years ago and I want to plan ahead",
    "my mom passed away last year, I'd like to pre-plan my own funeral",
    "how much is a simple cremation",
    "do you have a location in Victor",
    "I'd like to book a pre-planning consultation",
]


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def main():
    helpers._load_scheduler()
    import rules
    import conversation
    from config import load_config
    from db import init_db, set_state, get_state
    init_db()

    miller = load_config("config/miller_funeral.yaml")
    miller["business"]["id"] = 1

    heading("Miller: a death that just happened gets both numbers")
    for message in JUST_DIED:
        reply = rules.get_urgent_reply(message, miller)
        check(f"{message!r}", reply and ROCHESTER in reply and VICTOR in reply, reply)
        check(f"  …and asks nothing back", reply and "?" not in reply, reply)

    heading("Miller: a death in the past, or no death, is not urgent")
    for message in NOT_NOW:
        check(f"{message!r}", rules.get_urgent_reply(message, miller) is None,
              rules.get_urgent_reply(message, miller))

    heading("the ordinary rules still answer it too, before the model")
    check("get_reply gives the same numbers (the rule is first in the list)",
          rules.get_reply("my mother just passed away", miller)
          == rules.get_urgent_reply("my mother just passed away", miller))

    heading("businesses without an urgent rule: nothing changes")
    for p in sorted((ROOT / "config").glob("*.yaml")):
        raw = yaml.safe_load(p.read_text())
        if not isinstance(raw, dict) or "rules" not in raw or p.stem == "miller_funeral":
            continue
        check(f"{p.stem}: no urgent rule",
              not any(r.get("urgent") for r in raw["rules"]))
    bob = load_config("config/bobs_plumbing.yaml")
    check("Bob's 'emergency' isn't urgent: mid-booking, the booking answers it",
          rules.get_urgent_reply("this is an emergency", bob) is None)

    heading("mid-booking: the urgent rule answers, and the booking ends")
    booked, asked_model = [], []
    real = (conversation.handle_booking, conversation.get_llm_reply,
            conversation.save_message, conversation.get_reply)
    conversation.handle_booking = lambda *a, **k: booked.append(a[1]) or "Which funeral home?"
    conversation.get_llm_reply = lambda *a, **k: asked_model.append(a[0]) or "model reply"
    conversation.save_message = lambda *a, **k: None
    try:
        phone, biz = "web_urgent", 1
        set_state(phone, biz, "collecting",
                  pending={"service": "pre-planning consultation"})
        reply = conversation.process_message("my mother just passed away", phone, biz,
                                             miller, channel="webchat")
        check("the reply is the phone numbers", ROCHESTER in reply and VICTOR in reply, reply)
        check("the booking flow never saw the message", booked == [], booked)
        check("the booking is over (state idle, nothing kept)",
              get_state(phone, biz)["state"] == "idle"
              and not get_state(phone, biz)["pending"], get_state(phone, biz))

        set_state(phone, biz, "collecting",
                  pending={"service": "pre-planning consultation"})
        reply = conversation.process_message("Rochester", phone, biz, miller,
                                             channel="webchat")
        check("an ordinary answer mid-booking still goes to the booking",
              booked == ["Rochester"] and reply == "Which funeral home?", (booked, reply))
        check("…and the booking carries on", get_state(phone, biz)["state"] == "collecting")

        set_state(phone, biz, "idle", pending={})
        booked.clear()
        reply = conversation.process_message("Our son died today", phone, biz, miller,
                                             channel="sms")
        check("not mid-booking: the same numbers, no booking, no model call",
              ROCHESTER in reply and booked == [] and asked_model == [], (reply, booked))
    finally:
        (conversation.handle_booking, conversation.get_llm_reply,
         conversation.save_message, conversation.get_reply) = real

    heading("the classifier is told a death is never a booking (booking.not_a_booking)")
    import llm
    prompts = []
    real_create, real_text = llm._create, llm.text_of
    llm._create = lambda **k: prompts.append(k["messages"][0]["content"])
    llm.text_of = lambda response: '{"intent": "question"}'
    try:
        llm.classify_and_extract("The nursing home just called, Dad is gone", [], miller)
        check("Miller's classifier prompt carries the line",
              "Never a consultation, whatever it asks for: a message saying someone "
              "has just died" in prompts[-1], prompts[-1][:400])
        sunrise = load_config("config/sunrise_bakery_and_cafe.yaml")
        llm.classify_and_extract("can I get a cake", [], sunrise)
        check("a business without the list: no such line",
              "whatever it asks for" not in prompts[-1])
    finally:
        llm._create, llm.text_of = real_create, real_text

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

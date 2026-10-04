#!/usr/bin/env python3
"""followup_test.py: the live bakery conversation of 2026-10-03, turn by turn.

Run it:  python3 followup_test.py

  cust  I cancelled three days before, do I get my deposit back?
  BOT   I need to know which kind of cake you ordered...        (1)
  cust  custom cake
  BOT   Happy to help with custom birthday cakes. What name...  (2)
  cust  no, I want to know about a cake I already ordered and cancelled...
  BOT   ...the whole deposit is forfeited... What name should I put this
        order under?                                             (3)
  cust  what about a wedding cake?
  BOT   I'm not sure what the deposit policy is...               (4)

(1) the tool was optional and the unpinned live model asked first;
(2) the booking classifier never saw the bot's question, so "custom cake"
    read as an order; (3) only "cancel"/"stop" left the booking flow;
(4) the follow-up named neither a time nor cancelling, so it got no tools
    and no rules. No network, no model: fakes throughout.
"""

import json
import sys
import tempfile
import types
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="followup_test_")) / "test.db"

import booking_state_test as helpers

ROOT = Path(__file__).resolve().parent
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


OPENING = "I cancelled three days before, do I get my deposit back?"
ASKED = ("I need to know which kind of cake you ordered to give you the right "
         "answer — was it a custom cake or a wedding cake?")


def main():
    sched = helpers._load_scheduler()
    import llm
    import policy
    from config import load_config
    from db import init_db, set_state, get_state
    init_db()
    cfg = load_config(str(ROOT / "config" / "sunrise_bakery_and_cafe.yaml"))
    bobs = load_config(str(ROOT / "config" / "bobs_plumbing.yaml"))

    heading("(1) a cancellation question that says when: the tool is required")
    check("the opening message must apply the rules",
          policy.must_apply_cancellation(OPENING, cfg))
    check("'a month out' too",
          policy.must_apply_cancellation(
              "if I cancel my wedding cake a month out do I get my deposit back", cfg))
    check("not 'what's your cancellation policy' (no timing to apply)",
          not policy.must_apply_cancellation("what's your cancellation policy", cfg))
    check("not at a business without cancellation rules",
          not policy.must_apply_cancellation(OPENING, bobs))

    real = (llm._create, llm.retrieve)
    llm.retrieve = lambda *a, **k: []
    sent = []
    block = lambda **k: types.SimpleNamespace(**k)
    reply = lambda *b: types.SimpleNamespace(content=list(b), usage=None,
                                             stop_reason="end_turn")

    def fake(script):
        queue = list(script)
        def _create(**call):
            sent.append(json.loads(json.dumps(call, default=vars)))
            return queue.pop(0) if queue else reply(block(type="text", text="ok"))
        return _create
    try:
        llm._create = fake([
            reply(block(type="tool_use", id="t1", name="cancellation_outcome",
                        input={"days_before": 3})),
            reply(block(type="text", text="Cancelling less than 7 days before "
                                          "forfeits the deposit, for any cake."))])
        got = llm.get_llm_reply(OPENING, [], cfg)
        check("the first call requires cancellation_outcome",
              sent[0].get("tool_choice") == {"type": "tool",
                                             "name": "cancellation_outcome"},
              sent[0].get("tool_choice"))
        check("the second call doesn't force it again", "tool_choice" not in sent[1],
              sent[1].get("tool_choice"))
        check("and the answer comes back", got.startswith("Cancelling less than 7"))

        sent.clear()
        llm._create = fake([reply(block(type="text", text="We open at 7am."))])
        llm.get_llm_reply("can I get muffins tomorrow at 8am?", [], cfg)
        check("a notice question offers the tools without forcing one",
              "tools" in sent[0] and "tool_choice" not in sent[0])

        heading("(4) the follow-up keeps the tools and the rules")
        history = [{"role": "user", "content": OPENING},
                   {"role": "assistant", "content": "The whole deposit is forfeited."}]
        check("'what about a wedding cake?' after a deposit question needs tools",
              policy.needs_tools("what about a wedding cake?", cfg, history))
        check("on its own it doesn't",
              not policy.needs_tools("what about a wedding cake?", cfg, []))
        old = history + [{"role": "user", "content": "do you deliver?"},
                         {"role": "assistant", "content": "Yes."},
                         {"role": "user", "content": "what flavours?"},
                         {"role": "assistant", "content": "Six."}]
        check(f"more than {policy.FOLLOW_UP_TURNS} customer messages back, it lapses",
              not policy.needs_tools("what about a wedding cake?", cfg, old))
        sent.clear()
        llm._create = fake([reply(block(type="text", text="Same: forfeited."))])
        llm.get_llm_reply("what about a wedding cake?", history, cfg,
                          mid_booking=True)
        flat = " ".join(sent[0]["system"].split())
        check("mid-booking, the follow-up gets the tools",
              {t["name"] for t in sent[0].get("tools", [])} >=
              {"cancellation_outcome"})
        check("and the rendered rules", "Deposits and cancellation:" in flat)
        check("but isn't forced (it names no timing itself)",
              "tool_choice" not in sent[0])

        heading("(2) the booking check sees what the bot just asked")
        sent.clear()
        llm._create = fake([reply(block(type="text", text='{"intent": "question"}'))])
        slots = sched.get_slot_definitions(cfg)
        result = llm.classify_and_extract("custom cake", slots, cfg,
                                          previous_reply=ASKED)
        prompt = sent[0]["messages"][0]["content"]
        check("the bot's last message is in the prompt, fenced",
              "<assistant_message>" in prompt and "which kind of cake" in prompt)
        check("with the rule: answering the bot's question is a question",
              "it is a question, even if it names a service" in prompt)
        check("and an example of exactly that shape",
              'Message: "the deluxe"' in prompt)
        check("the result is passed through", result == {"intent": "question"})
        sent.clear()
        llm._create = fake([reply(block(type="text", text='{"intent": "book"}'))])
        llm.classify_and_extract("a custom cake for Friday", slots, cfg)
        check("no previous message: no context block",
              "<assistant_message>" not in sent[0]["messages"][0]["content"])
        sent.clear()
        llm._create = fake([reply(block(type="text", text='{"intent": "book", '
                                                     '"service": "custom cake"}'))])
        result = llm.classify_and_extract("custom cake", slots, cfg,
                                          previous_reply=ASKED)
        check("the model says book anyway (as it did 3/3): code makes it a question",
              result == {"intent": "question"}, result)
        offered = ("We'd love to make it! Text 'order' to get started, or tell "
                   "me what you'd like.")
        for message, previous, answering in (
                ("custom cake", ASKED, True),
                ("the wedding one", ASKED, True),
                ("custom cake", ASKED + " 🎂", True),
                ("a custom cake for Saturday please", offered, False),
                ("custom cake", offered, False),      # not a question: no "?"
                ("custom cake", None, False),
                ("I'd like to order one", ASKED, False),
                ("2 dozen", ASKED, False)):
            check(f"answers the bot's question = {answering}: {message!r} after "
                  f"{(previous or 'nothing')[-25:]!r}",
                  llm.answers_previous_question(message, previous) is answering)
        sent.clear()
        llm._create = fake([reply(block(type="text", text='{"intent": "book", '
                                                     '"datetime": "Saturday"}'))])
        result = llm.classify_and_extract("a custom cake for Saturday please",
                                          slots, cfg, previous_reply=offered)
        check("a real order after the bot's offer is still a booking",
              result.get("intent") == "book", result)
        app_src = (ROOT / "conversation.py").read_text()
        check("conversation.py hands the classifier the bot's previous reply",
              "previous_reply=previous_reply" in app_src)
    finally:
        llm._create, llm.retrieve = real

    heading("journey_eval: a follow-up about a cancelled order isn't a new order")
    told = [{"role": "user", "content": OPENING},
            {"role": "assistant", "content": "Cancelling between 48 hours and 7 "
             "days before forfeits the whole deposit."}]
    check("'custom cake' after a deposit answer (no question back) continues it",
          llm.about_existing_order("custom cake", told))
    check("but 'I'd like to order a custom cake for Saturday' is a new order",
          not llm.about_existing_order("I'd like to order a custom cake for Saturday", told))
    check("and with no cancel/refund/deposit talk, nothing changes",
          not llm.about_existing_order("custom cake", [
              {"role": "user", "content": "do you do custom cakes?"},
              {"role": "assistant", "content": "We do, from $45."}]))
    sent.clear()
    real_c = llm._create
    llm._create = lambda **call: types.SimpleNamespace(content=[types.SimpleNamespace(
        type="text", text='{"intent": "book", "service": "custom cake"}')], usage=None)
    try:
        result = llm.classify_and_extract("custom cake", sched.get_slot_definitions(cfg),
                                          cfg, previous_reply=told[-1]["content"],
                                          history=told)
    finally:
        llm._create = real_c
    check("the classifier says book; with the history, code makes it a question",
          result == {"intent": "question"}, result)
    check("conversation.py passes the history to the classifier",
          "history=history" in (ROOT / "conversation.py").read_text())

    heading("(3) saying it isn't an order leaves the booking flow")
    for text in ("no, I want to know about a cake I already ordered and cancelled. "
                 "Can I get my deposit back?",
                 "I don't want to order anything",
                 "I'm not ordering, just asking",
                 "I already paid for one, I'm asking about the refund"):
        check(f"leaves: {text[:45]}", sched._leaving_booking(text))
    for text in ("no", "no thanks", "do you have gluten free?",
                 "custom cake", "Saturday at 10", "no nuts please"):
        check(f"stays: {text}", not sched._leaving_booking(text))

    answered = []
    real_s = (sched._answer_outside_booking, sched._answer_mid_booking,
              sched.extract_booking_slots)
    sched._answer_outside_booking = lambda phone, message, *a, **k: (
        answered.append(message) or
        "Cancelling less than 7 days before forfeits the deposit, for any cake.")
    sched._answer_mid_booking = lambda *a, **k: "We do gluten-free sponge."
    sched.extract_booking_slots = lambda *a, **k: {}
    try:
        phone, biz = "web_followup", 1
        set_state(phone, biz, "collecting",
                  pending={"service": "custom birthday cakes"})
        got = sched._handle_booking_inner(
            phone, "no, I want to know about a cake I already ordered and "
                   "cancelled. Can I get my deposit back?", cfg, biz)
        check("the booking is dropped", get_state(phone, biz)["state"] == "idle")
        check("it says so", got.startswith("No problem, I've stopped the order."),
              got)
        check("and answers the question", "forfeits the deposit" in got, got)
        check("and doesn't ask for a name", "name" not in got.lower(), got)

        set_state(phone, biz, "collecting",
                  pending={"service": "custom birthday cakes"})
        sched._handle_booking_inner(phone, "do you have gluten free?", cfg, biz)
        check("an ordinary question mid-booking keeps the booking",
              get_state(phone, biz)["state"] == "collecting")
    finally:
        (sched._answer_outside_booking, sched._answer_mid_booking,
         sched.extract_booking_slots) = real_s

    heading("Crosstown, live: 'can I pick it up?' mid-order leaves the order")
    pizza = load_config(str(ROOT / "config" / "crosstown_pizza.yaml"))
    for text in ("can I pick it up?", "I'll pick up instead", "can I do pickup",
                 "is pick-up ok?", "I'd rather collect it"):
        check(f"Crosstown leaves: {text}", sched._leaving_booking(text, pizza))
    for text in ("pizza and wings", "Pizza Paul", "12 Elm St", "as soon as possible",
                 "pickles on the side"):
        check(f"Crosstown stays: {text}", not sched._leaving_booking(text, pizza))
    check("Sunrise, where pickup is normal, stays: 'can I pick it up?'",
          not sched._leaving_booking("can I pick it up?", cfg))
    real_s = (sched._answer_outside_booking, sched.extract_booking_slots)
    sched._answer_outside_booking = lambda *a, **k: (
        "For pickup, give us a ring at (585) 555-0177 — usually ready in 20 minutes.")
    sched.extract_booking_slots = lambda *a, **k: {}
    try:
        phone, biz = "web_pizza", 3
        set_state(phone, biz, "collecting",
                  pending={"service": "pizza", "customer_name": "Pizza Paul"})
        got = sched._handle_booking_inner(phone, "can I pick it up?", pizza, biz)
        check("the order is dropped", get_state(phone, biz)["state"] == "idle")
        check("it says so, then how pickup works",
              got.startswith("No problem, I've stopped the order.")
              and "give us a ring" in got, got)
        check("and doesn't ask where to deliver", "deliver" not in got.lower(), got)
    finally:
        sched._answer_outside_booking, sched.extract_booking_slots = real_s

    heading("'it's Friday at 5' is when they're writing, not when they want it")
    seen = []
    real = llm._create
    llm._create = lambda **call: (seen.append(call["messages"][0]["content"]) or
                                  types.SimpleNamespace(content=[types.SimpleNamespace(
                                      type="text", text="{}")], usage=None))
    try:
        llm.extract_booking_slots("it's Friday at 5, I need food for 20 people",
                                  sched.get_slot_definitions(pizza), pizza)
        llm.classify_and_extract("it's Friday at 5, I need food for 20 people",
                                 sched.get_slot_definitions(pizza), pizza)
    finally:
        llm._create = real
    rule = "telling you when they're writing, not when they want it"
    check("the slot extractor is told", rule in " ".join(seen[0].split()))
    check("the classifier is told", rule in " ".join(seen[1].split()))

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

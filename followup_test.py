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
        app_src = (ROOT / "app.py").read_text()
        check("app.py hands the classifier the bot's previous reply",
              "previous_reply=previous_reply" in app_src)
    finally:
        llm._create, llm.retrieve = real

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

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

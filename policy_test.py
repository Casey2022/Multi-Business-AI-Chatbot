#!/usr/bin/env python3
"""policy_test.py: rule arithmetic is done in code, and the model is handed it.

Run it:  python3 policy_test.py

policy.py exists because a week of rewording Sunrise's deposit rules moved
the wrong answer between two eval rows without fixing both, and the 6pm
muffins row kept calling 14 hours "just under" 24. These checks pin the
arithmetic (windows, boundaries, amounts, notice), the rendered text the
bot and the judge read, and the tool loop in llm.get_llm_reply, against a
fake model. No network, no ChromaDB.
"""

import json
import sys
import types
from datetime import datetime
from pathlib import Path

import yaml

import booking_state_test as helpers    # stubs the integrations llm imports

ROOT = Path(__file__).resolve().parent
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def yaml_config(slug):
    return yaml.safe_load((ROOT / "config" / f"{slug}.yaml").read_text())


# The eval's clocks: Tuesday 6pm (muffins), Wednesday 10am (gluten-free).
TUE_6PM = datetime(2026, 9, 22, 18, 0)
WED_10AM = datetime(2026, 9, 23, 10, 0)


def main():
    import policy

    heading("how time and money are said")
    check("7 days", policy.label_span(168) == "7 days")
    check("48 hours stays hours", policy.label_span(48) == "48 hours")
    check("14 hours", policy.span(14) == "14 hours")
    check("96 hours reads as days too", policy.span(96) == "4 days (96 hours)")
    check("half an hour", policy.span(0.5) == "30 minutes")
    check("nothing is 'no time'", policy.span(0) == "no time")
    check("money drops .00", policy.money(50) == "$50")
    check("money keeps cents", policy.money(12.5) == "$12.50")

    heading("notice: the 6pm muffins row")
    r = policy.check_notice("2026-09-23T08:00", 24, "a dozen muffins", now=TUE_6PM)
    check("14 hours available", r["hours_available"] == 14.0, r)
    check("not enough notice", r["enough_notice"] is False)
    check("short by 10 hours", r["short_by_hours"] == 10.0)
    check("says it can't be promised", "can't be promised" in r["say"], r["say"])
    check("never 'just under'", "just under" not in r["say"].lower())
    check("names when it needed ordering",
          "Tuesday, September 22 at 8:00 AM" in r["say"], r["say"])
    check("and the earliest it can be ready",
          "Wednesday, September 23 at 6:00 PM" in r["say"], r["say"])

    heading("notice: the gluten-free Wednesday row")
    r = policy.check_notice("2026-09-23T10:00", 96, "gluten-free muffins",
                            now=WED_10AM)
    check("96 hours back from Wednesday 10am is SATURDAY 10am",
          r["order_by"] == "Saturday, September 19 at 10:00 AM", r["order_by"])
    check("wanted right now reads naturally", "wanted right away" in r["say"],
          r["say"])

    heading("notice: enough, and already past")
    r = policy.check_notice("2026-09-26T09:00", 72, "a custom cake", now=TUE_6PM)
    check("87 hours is enough for 72", r["enough_notice"] and
          r["hours_available"] == 87.0, r)
    check("gives the order-by time", "Wednesday, September 23 at 9:00 AM"
          in r["say"], r["say"])
    r = policy.check_notice("2026-09-21T09:00", 24, "muffins", now=TUE_6PM)
    check("a time in the past says so", "already passed" in r["say"], r["say"])
    r = policy.check_notice("2026-09-23T08:00:00Z", 24, "muffins", now=TUE_6PM)
    check("a Z suffix from the model is tolerated", r["hours_available"] == 14.0)
    r = policy.check_notice("2026-09-23T18:00", 24, "muffins", now=TUE_6PM)
    check("exactly the notice is enough", r["enough_notice"] is True)

    heading("cancellation windows: Sunrise's real rules")
    cfg = yaml_config("sunrise_bakery_and_cafe")
    out = lambda **k: policy.cancellation_outcome(cfg, **k)
    r = out(days_before=3)
    check("3 days: between 48 hours and 7 days",
          r["window"] == "between 48 hours and 7 days before the order date", r["window"])
    check("3 days: the same for every cake", r["same_for_every_kind"] is True)
    check("3 days: forfeited, for wedding cakes too",
          "forfeited" in r["say"] and "wedding cake alike" in r["say"], r["say"])
    check("3 days: no refund language", "refunded" not in r["say"], r["say"])
    r = out(days_before=30)
    check("a month: 7 days or more", r["window"].startswith("7 days or more"))
    check("a month: custom and wedding differ", r["same_for_every_kind"] is False)
    check("a month: wedding keeps 25%, only the excess back",
          "only what was paid above that 25% is refunded" in r["outcomes"]["wedding cake"])
    check("a month: the 25% minimum gets nothing back",
          "just the 25% minimum gets nothing back" in r["outcomes"]["wedding cake"])
    check("a month: custom comes back in full",
          r["outcomes"]["custom cake"] == "the deposit comes back in full")
    check("exactly 7 days counts as 7 days or more",
          out(hours_before=168)["window"].startswith("7 days or more"))
    check("just under 7 days is the forfeit window",
          out(hours_before=167.5)["window"].startswith("between"))
    check("exactly 48 hours is still the forfeit window",
          out(hours_before=48)["window"].startswith("between"))
    check("47 hours: full price", "full order price" in out(hours_before=47)["say"])
    r = out(hours_before=-5)
    check("after the order date: full price, and says 'after'",
          "full order price" in r["say"] and "after the order date" in r["say"],
          r["say"])

    heading("cancellation: amounts, when the customer gives them")
    r = out(days_before=30, kind="wedding cake", amount_paid=200, order_price=600)
    check("wedding, $200 of $600, a month out: $50 back, $150 kept",
          "$50 comes back, $150 is kept" in r["say"], r["say"])
    r = out(days_before=30, kind="wedding cake", amount_paid=150, order_price=600)
    check("paid exactly the 25% minimum: $0 back",
          "$0 comes back, $150 is kept" in r["say"], r["say"])
    r = out(days_before=10, kind="custom cake", amount_paid=60)
    check("custom, 10 days: all $60 back", "$60 comes back" in r["say"], r["say"])
    r = out(hours_before=20, amount_paid=100, order_price=400)
    check("20 hours: $300 more owed, said once not per kind",
          r["say"].count("$300 more is owed") == 1, r["say"])
    r = out(days_before=3, kind="birthday cake")
    check("an unknown kind gives every kind", r["same_for_every_kind"] and
          "alike" in r["say"], r["say"])

    heading("the text the bot and the judge read")
    text = policy.policy_text(cfg)
    import rubrics, judge
    for name in ("DEPOSIT_THREE_DAYS", "WEDDING_CANCEL_MONTH_OUT"):
        rubric = getattr(rubrics, name)
        check(f"{name} quotes the rendered policy",
              all(judge.flatten(q) in judge.flatten(text) for q in rubric.quotes))
    check("no business without the data gets policy text",
          policy.policy_text(yaml_config("bobs_plumbing")) == "")

    heading("what the model is offered")
    names = lambda slug: [t["name"] for t in policy.tools_for(yaml_config(slug))]
    check("every business gets check_notice",
          all("check_notice" in names(s) for s in
              ("bobs_plumbing", "belmont_hair_studio", "ridgeline_contracting",
               "crosstown_pizza", "sunrise_bakery_and_cafe")))
    check("only Sunrise gets cancellation_outcome",
          names("sunrise_bakery_and_cafe") == ["check_notice", "cancellation_outcome"]
          and names("bobs_plumbing") == ["check_notice"])
    tool = policy.cancellation_tool(cfg)
    check("kind is an enum of the policy's kinds",
          tool["input_schema"]["properties"]["kind"]["enum"]
          == ["custom cake", "wedding cake"])
    check("bad arguments become an error result, not an exception",
          "error" in policy.run_tool("check_notice", {"needed_by": "soon",
                                     "notice_hours": 24, "what": "x"}, cfg))
    check("no timing for a cancellation is an error result",
          "error" in policy.run_tool("cancellation_outcome", {}, cfg))
    check("cancellation_outcome at a business without the rules: error",
          "error" in policy.run_tool("cancellation_outcome", {"days_before": 3},
                                     yaml_config("bobs_plumbing")))
    check("an unknown tool: error", "error" in policy.run_tool("rm_rf", {}, cfg))

    heading("the prompt says when to use them")
    helpers._load_scheduler()
    import llm
    from config import load_config
    sunrise = load_config(str(ROOT / "config" / "sunrise_bakery_and_cafe.yaml"))
    bobs = load_config(str(ROOT / "config" / "bobs_plumbing.yaml"))
    flat = lambda s: " ".join(s.split())      # rules are line-wrapped
    p_sun = llm.build_system_prompt(sunrise, now=TUE_6PM)
    p_bob = llm.build_system_prompt(bobs, now=TUE_6PM)
    check("Sunrise prompt carries the rendered rules", text in p_sun)
    check("Sunrise prompt says to call cancellation_outcome",
          "call cancellation_outcome" in flat(p_sun))
    check("every prompt says to call check_notice",
          "call check_notice" in flat(p_sun) and "call check_notice" in flat(p_bob))
    check("Bob's prompt has no cancellation rules",
          "cancellation_outcome" not in p_bob and "Deposits and cancellation" not in p_bob)
    check("mid-booking prompts get the rules too",
          "call cancellation_outcome" in
          flat(llm.build_system_prompt(sunrise, mid_booking=True, now=TUE_6PM)))

    heading("the tool loop, against a fake model")
    real = (llm._create, llm.retrieve)
    llm.retrieve = lambda *a, **k: []
    sent = []
    text_block = lambda s: types.SimpleNamespace(type="text", text=s)
    use_block = lambda i, n, a: types.SimpleNamespace(type="tool_use", id=i,
                                                      name=n, input=a)
    reply = lambda *blocks: types.SimpleNamespace(content=list(blocks), usage=None,
                                                  stop_reason="end_turn")

    def fake(script):
        queue = list(script)
        def _create(**call):
            sent.append(json.loads(json.dumps(call, default=vars)))
            return queue.pop(0) if queue else reply(text_block("fallback"))
        return _create

    try:
        sent.clear()
        llm._create = fake([
            reply(text_block("Let me check."),
                  use_block("t1", "cancellation_outcome", {"days_before": 3})),
            reply(text_block("Three days out, the whole deposit is forfeited, "
                             "for any cake."))])
        got = llm.get_llm_reply("I cancelled three days before, do I get my "
                                "deposit back", [], sunrise, now=TUE_6PM)
        check("the answer is the text written after the result",
              got.startswith("Three days out"), got)
        check("the 'let me check' preamble isn't sent", "Let me check" not in got)
        check("two calls", len(sent) == 2, len(sent))
        check("tools offered on the first call",
              {t["name"] for t in sent[0]["tools"]} ==
              {"check_notice", "cancellation_outcome"})
        results = sent[1]["messages"][-1]["content"]
        check("the second call carries the tool result for t1",
              results[0]["type"] == "tool_result" and
              results[0]["tool_use_id"] == "t1")
        payload = json.loads(results[0]["content"])
        check("the result is the computed outcome",
              payload.get("same_for_every_kind") is True and
              "forfeited" in payload.get("say", ""), payload)
        check("the assistant's tool call is echoed back before it",
              sent[1]["messages"][-2]["role"] == "assistant" and
              any(b.get("type") == "tool_use"
                  for b in sent[1]["messages"][-2]["content"]))

        sent.clear()
        llm._create = fake([reply(text_block("We're open 7am to 3pm."))])
        got = llm.get_llm_reply("when are you open", [], sunrise, now=TUE_6PM)
        check("no tool asked for: one call, answer as written",
              len(sent) == 1 and got == "We're open 7am to 3pm.")

        sent.clear()
        loop = [reply(use_block(f"t{i}", "check_notice",
                                {"needed_by": "2026-09-23T08:00",
                                 "notice_hours": 24, "what": "muffins"}))
                for i in range(10)]
        llm._create = fake(loop)
        got = llm.get_llm_reply("muffins by 8am?", [], sunrise, now=TUE_6PM)
        check("a looping model is cut off after MAX_TOOL_ROUNDS + 1 calls",
              len(sent) == llm.MAX_TOOL_ROUNDS + 1, len(sent))
        check("the last call forbids tools",
              sent[-1].get("tool_choice") == {"type": "none"})
        check("and a reply that never came is an honest 'try again', not blank",
              got and "try again" in got, got)

        sent.clear()
        llm._create = fake([
            reply(use_block("t1", "check_notice", {"needed_by": "whenever",
                                                  "notice_hours": 24, "what": "x"})),
            reply(text_block("Could you tell me the day and time?"))])
        got = llm.get_llm_reply("muffins soon?", [], sunrise, now=TUE_6PM)
        payload = json.loads(sent[1]["messages"][-1]["content"][0]["content"])
        check("a bad tool call reaches the model as an error it can recover from",
              "error" in payload and got.startswith("Could you"), payload)
    finally:
        llm._create, llm.retrieve = real

    heading("one copy of the rules")
    doc = (ROOT / "documents" / "sunrise_bakery_and_cafe" / "services.md").read_text()
    check("Sunrise's document has no deposits section",
          "## Deposits" not in doc and "forfeit" not in doc.lower())

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

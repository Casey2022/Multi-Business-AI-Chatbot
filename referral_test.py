#!/usr/bin/env python3
"""referral_test.py: the phone number is given once, not in every reply.

Run it:  python3 referral_test.py

Defect #6 (2026-08-02): four bakery answers in a row ended "give us a
call". Seen again live on 2026-09-28: a gluten-free answer ended "call us at
(585) 555-0188" and the next one, about delivery, sent them to call again.
The prompt now says to give the number once; llm.without_repeated_number
makes sure of it.

No API calls: the model is faked where get_llm_reply is exercised.
"""

import sys
import types

import booking_state_test as helpers    # stubs the integrations llm imports

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


CFG = {"business": {"phone": "(585) 555-0188"}}
GAVE_IT = [{"role": "user", "content": "do you have gluten free cakes?"},
           {"role": "assistant", "content": "We do, with 96 hours' notice. For "
            "Friday you'd need to call us at (585) 555-0188 to see if we can "
            "make it work."}]


def main():
    helpers._load_scheduler()
    import llm
    strip = llm.without_repeated_number

    heading("a number given in a recent reply isn't given again")
    for message, reply, expected in [
        ("how much is delivery?",
         "Delivery is $15 for cake orders over $50 within 5 miles. For anything "
         "else, give us a call at (585) 555-0188.",
         # right after a referral, the repeat "go call us" goes entirely
         "Delivery is $15 for cake orders over $50 within 5 miles."),
        ("do you use organic flour?",
         "I'm not sure about that — please call (585) 555-0188 and the team can "
         "tell you.",
         "I'm not sure about that — please call us and the team can tell you."),
        ("do you use organic flour?",
         "I don't have that detail. Our number is (585) 555-0188 if you want to "
         "ask the kitchen.",
         "I don't have that detail."),
        ("anything nut free?", "Nut-free isn't something we can promise. "
         "Call 585.555.0188.", "Nut-free isn't something we can promise."),
        ("anything nut free?", "Please ring us on 5855550188 about that.",
         "Please ring us about that."),
    ]:
        got = strip(reply, GAVE_IT, message, CFG)
        check(f"{reply[:48]!r}…", got == expected, f"got {got!r}")

    heading("a second 'go call us' in a row is dropped, number or not")
    wedding = [{"role": "assistant", "content": "We do three-tier cakes. To get "
                "started, give us a call at (585) 555-0188 so we can talk design."}]
    flavours = ("Our flavors are vanilla, chocolate, lemon and red velvet. "
                "Give us a call to set up a tasting!")
    got = strip(flavours, wedding, "what flavours do you have?", CFG)
    check("qa_eval 2026-09-29: 'Give us a call to set up a tasting!' goes",
          got == "Our flavors are vanilla, chocolate, lemon and red velvet.", repr(got))
    for lead in ("For anything else, give us a call.", "Please call us.",
                 "Just give the bakery a call.", "Feel free to reach out."):
        got = strip("Delivery is $15. " + lead, wedding, "delivery?", CFG)
        check(f"{lead!r} goes", got == "Delivery is $15.", repr(got))
    mixed = "Since it's custom, the bakery can confirm both when you call."
    check("a sentence with real information that mentions calling stays",
          strip(mixed, wedding, "delivery?", CFG) == mixed)
    quiet = [{"role": "assistant", "content": "We're open Tue-Sun, 7am-3pm."}]
    check("left alone when the previous reply didn't send them to call",
          strip(flavours, quiet, "flavours?", CFG) == flavours)

    heading("left alone when it should be")
    reply = "Delivery is $15. Call us at (585) 555-0188 for anything else."
    check("the first time it's given", strip(reply, [], "delivery?", CFG) == reply)
    for ask in ("what's your phone number?", "how do I reach you?",
                "can I talk to someone?", "can I call you?"):
        check(f"the customer asks: {ask!r}", strip(reply, GAVE_IT, ask, CFG) == reply)
    long_ago = GAVE_IT + [{"role": "assistant", "content": f"Answer {i}."}
                          for i in range(llm.REFERRAL_LOOKBACK)]
    check(f"given more than {llm.REFERRAL_LOOKBACK} replies ago",
          strip(reply, long_ago, "delivery?", CFG) == reply)
    only = "(585) 555-0188."
    check("a reply that's nothing but the number stays (never empty)",
          strip(only, GAVE_IT, "hmm", CFG) == only)
    check("no phone configured: a repeated 'call us' still goes, nothing breaks",
          strip(reply, GAVE_IT, "x", {"business": {}}) == "Delivery is $15.")
    check("other numbers in the reply are untouched",
          strip("Trays feed 8-12 people for $65.", GAVE_IT, "trays?", CFG)
          == "Trays feed 8-12 people for $65.")

    heading("the rule is in the prompt only once they've been sent to call")
    from config import load_config
    cfg = load_config("config/sunrise_bakery_and_cafe.yaml")
    plain = llm.build_system_prompt(cfg)
    check("the standing prompt doesn't carry it (single questions unchanged)",
          "already given the customer our phone number" not in plain)
    sent = []
    real = (llm._create, llm.retrieve)
    llm.retrieve = lambda *a, **k: []
    text = types.SimpleNamespace(type="text",
                                 text="Not sure about that — call us at (585) 555-0188.")
    def fake(**k):
        sent.append(k["system"])
        return types.SimpleNamespace(content=[text], stop_reason="end_turn",
                                     usage=types.SimpleNamespace(input_tokens=0,
                                                                 output_tokens=0))
    llm._create = fake
    try:
        first = llm.get_llm_reply("do you use organic flour?", [], cfg)
        got = llm.get_llm_reply("do you use organic flour?", GAVE_IT, cfg)
        llm.get_llm_reply("do you use organic flour?", long_ago, cfg)
    finally:
        llm._create, llm.retrieve = real
    check("a first question: no rule", llm.REFERRAL_RULE not in sent[0])
    check("after a referral: the rule is added", llm.REFERRAL_RULE in sent[1])
    check("a referral more than 3 replies back: no rule", llm.REFERRAL_RULE not in sent[2])
    check("the rule still says never claim what the facts don't say",
          "never claim something the facts" in llm.REFERRAL_RULE)
    check("a first answer keeps its number", first.endswith("(585) 555-0188."))
    check("get_llm_reply removes the repeat", got == "Not sure about that — call us.",
          repr(got))

    heading("no markdown reaches the customer (rag_eval, 2026-10-20)")
    from pathlib import Path
    llm_src = (Path(__file__).resolve().parent / "llm.py").read_text()
    plain = llm.plain_text
    seen = ("Here's how it works:\n\n**Custom cakes:** 50% deposit upfront.\n"
            "- Cancel 7+ days ahead: full deposit back\n"
            "* Cancel within 48 hours: charged the full order price\n"
            "## Wedding cakes\n__Note__: lose the whole deposit.")
    got = plain(seen)
    check("bold markers and heading hashes go; '* ' bullets become '- '",
          got == ("Here's how it works:\n\nCustom cakes: 50% deposit upfront.\n"
                  "- Cancel 7+ days ahead: full deposit back\n"
                  "- Cancel within 48 hours: charged the full order price\n"
                  "Wedding cakes\nNote: lose the whole deposit."), repr(got))
    check("ordinary text is untouched: sums, prices, a lone asterisk, dashes",
          plain("2 * 3 trays at $15 each — 5*4 is 20, *not* bold") ==
          "2 * 3 trays at $15 each — 5*4 is 20, *not* bold")
    check("empty stays empty", plain("") == "" and plain(None) is None)
    check("every model reply goes through it, before the referral check",
          llm_src.index("reply = plain_text(reply)") < llm_src.index("reply = without_repeated_number("))

    heading("never a dead end (2026-10-23)")
    # The other side of defect #6: once the number was given, "don't refer
    # again" left "I don't have any parking information" with nothing more,
    # 4 runs in 6 (journey "way forward: ... after a referral").
    import llm as _llm
    way = _llm.with_way_forward
    cfg = {"business": {"phone": "(585) 555-0188"}}
    gave = [{"role": "assistant", "content": "Call us at (585) 555-0188 for that."}]
    got = way("I don't have any parking information for the shop.", [], cfg)
    check("a dead end, number not given yet: the number is added",
          got.endswith("Give us a call at (585) 555-0188 and we can tell you."), got)
    got = way("I don't have any parking information for the shop.", gave, cfg)
    check("…given a turn ago: pointed back to us without the digits (defect #6)",
          got.endswith("Give us a call and the team can tell you.") and "555-0188" not in got, got)
    got = way("I don't have a documented follow-up step for a quote that arrives late, "
              "so our office would need to confirm what happens next.", gave, cfg)
    check("Ridgeline's 'our office would need to confirm' gets a way forward too",
          "Give us a call" in got, got)
    for reply in ("I'm not sure about parking. Please call us.",
                  "I can't confirm that, so text 'order' and the team will check.",
                  "I don't have that listed; contact the office."):
        check(f"left alone: it already has a way forward ({reply[:28]}…)",
              way(reply, gave, cfg) == reply)
    for reply in ("We don't currently offer vegan cakes.", "We don't have parking, sorry.",
                  "A 6-inch cake is $45 and serves 6-8."):
        check(f"left alone: it answers ({reply[:28]}…)", way(reply, [], cfg) == reply)
    reply = ("Delivery is within 5 miles, for orders over $50. Correction to my last "
             "message: I can't confirm our gluten-free cake flavors.")
    check("left alone: it answers first, whatever it says after (qa_eval, 2026-10-23)",
          way(reply, gave, cfg) == reply)
    check("empty stays empty", way("", [], cfg) == "" and way(None, [], cfg) is None)
    a, b = (llm_src.find("reply = without_repeated_number("),
            llm_src.find("return with_way_forward(reply, history, config)"))
    check("it runs last, after the repeat check (so that can't strip it)", -1 < a < b, (a, b))

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

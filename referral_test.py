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

    heading("the prompt says it, and every Q&A reply goes through the guard")
    from config import load_config
    cfg = load_config("config/sunrise_bakery_and_cafe.yaml")
    prompt = llm.build_system_prompt(cfg)
    check("the answering rules say to give the number once",
          "Give the phone number once" in prompt)
    check("and when that doesn't apply (they ask for it)",
          "asks how to reach us" in prompt)

    real = (llm._create, llm.retrieve)
    llm.retrieve = lambda *a, **k: []
    text = types.SimpleNamespace(type="text",
                                 text="Not sure about that — call us at (585) 555-0188.")
    llm._create = lambda **k: types.SimpleNamespace(
        content=[text], stop_reason="end_turn",
        usage=types.SimpleNamespace(input_tokens=0, output_tokens=0))
    try:
        got = llm.get_llm_reply("do you use organic flour?", GAVE_IT, cfg)
    finally:
        llm._create, llm.retrieve = real
    check("get_llm_reply removes the repeat", got == "Not sure about that — call us.",
          repr(got))

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""price_rule_test.py: the pricing instructions reach price questions only.

Run it:  python3 price_rule_test.py

2026-10-08: three price rows failed on every run, and none was a sum. The
bot priced 30 wings at the 40-wing price, gave a Sicilian the large's topping
price, and sent a customer with a $600 quote to "call us" instead of
applying the rule that the detection fee is credited. llm.PRICE_RULE says
what to do; it's added only when the message asks about a price, so every
other prompt stays byte-identical (standing-prompt changes move unrelated
rows). No API calls.
"""

import re
import sys
import types

import booking_state_test as helpers

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


PRICE = ["how much for 30 wings", "what does a sicilian with two toppings cost",
         "you found my leak and quoted $600 to fix it, what do I pay in total",
         "it's 11pm and my main line needs hydro jetting, what's that going to run me",
         "is there a fee for after hours", "what's your cheapest haircut",
         "Prices for cupcakes?", "do I pay anything up front for balayage"]
NOT_PRICE = ["do you deliver to Webster", "when should I expect my quote",
             "can I book Marcus for my balayage", "what time do you shut on a Sunday",
             "is the gluten free crust safe for celiac", "do you do vegan cakes",
             "I cancelled three days before, do I get my deposit back",
             "how long does a hidden leak take",
             "how much notice for a custom cake", "how much time do I need to book ahead"]


def main():
    helpers._load_scheduler()
    import llm

    heading("which messages are about a price")
    for q in PRICE:
        check(f"price: {q!r}", llm.asks_about_price(q))
    for q in NOT_PRICE:
        check(f"not price: {q!r}", not llm.asks_about_price(q))
    check("'pay' inside another word doesn't count", not llm.asks_about_price("paypal or venmo?"))
    check("'price' inside 'priceless' doesn't count",
          not llm.asks_about_price("the view is priceless"))

    heading("what the rule says")
    rule = llm.PRICE_RULE
    check("no real business facts in it (no amounts)", not re.search(r"\$\s?\d|\d", rule), rule)
    check("it says a price for one item is never another's", "never the" in rule)
    check("it covers things that aren't listed", "isn't listed" in rule)
    check("it covers the customer's own figure", "figure of their own" in rule)
    check("it says when it does NOT apply", "doesn't apply to anything other than prices" in rule)

    heading("it reaches the prompt only for a price question")
    from config import load_config
    cfg = load_config("config/crosstown_pizza.yaml")
    sent = []
    real = (llm._create, llm.retrieve)
    llm.retrieve = lambda *a, **k: []
    text = types.SimpleNamespace(type="text", text="Wings come in 10, 20 or 40.")
    def fake(**k):
        sent.append(k["system"])
        return types.SimpleNamespace(content=[text], stop_reason="end_turn",
                                     usage=types.SimpleNamespace(input_tokens=0,
                                                                 output_tokens=0))
    llm._create = fake
    try:
        llm.get_llm_reply("how much for 30 wings", [], cfg)
        llm.get_llm_reply("what time do you shut on a Sunday", [], cfg)
    finally:
        llm._create, llm.retrieve = real
    check("a price question gets the rule", llm.PRICE_RULE in sent[0])
    check("anything else gets exactly the prompt it had",
          llm.PRICE_RULE not in sent[1]
          and "PRICES (this message" not in sent[1])
    check("the standing prompt doesn't carry it",
          "PRICES (this message" not in llm.build_system_prompt(cfg))

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

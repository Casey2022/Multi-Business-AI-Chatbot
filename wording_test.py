#!/usr/bin/env python3
"""wording_test.py: the assistant says a business's own words.

Run it:  python3 wording_test.py

Miller Funeral, 2026-11-12, 3 runs of 15 questions on SMS and web chat:
- "Green burial isn't listed on our menu": the price tool said "isn't on
  the menu", 12 replies in 90. menu_name ("price list") is said instead,
  and only where it's set (menu.named).
- "Text 'consultation' to start" in a web chat, 12 replies in 45: the
  prompt said customers start a booking "by texting". Web chat says
  "type", voice "say"; SMS keeps "text", word for word.

No API calls.
"""

import sys
from datetime import datetime
from pathlib import Path

import booking_state_test as helpers

ROOT = Path(__file__).resolve().parent
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def main():
    helpers._load_scheduler()
    import llm
    import menu
    from config import load_config

    miller = load_config("config/miller_funeral.yaml")
    crosstown = load_config("config/crosstown_pizza.yaml")
    green = [{"item": "burial package", "asked_for": "green burial", "quantity": 1,
              "add_ons": []}]
    calzone = [{"item": "pizza", "asked_for": "calzone", "quantity": 1, "add_ons": []}]

    heading("Miller says 'price list', not 'menu'")
    said = str(menu.menu_price(miller, green))
    check("not listed: 'isn't on the price list'", "isn't on the price list" in said, said)
    check("…and no 'menu' anywhere in the result", "menu" not in said.replace("menu_price", ""),
          said)
    rule = menu.prompt_rule(miller)
    check("the prompt rule, capital and all", rule.startswith("Price list prices come from "
                                                              "menu_price"), rule[:80])
    tool = menu.tool(miller)
    check("the tool keeps its name (the code calls it by name)", tool["name"] == "menu_price")
    check("the tool's description says 'price list'",
          "price list" in tool["description"] and " menu " not in tool["description"],
          tool["description"])
    enum = tool["input_schema"]["properties"]["order"]["items"]["properties"]["item"]["enum"]
    check("item names are left as they are", enum == [it["item"] for it in menu.items(miller)])

    heading("a business without menu_name: exactly as before")
    check("Crosstown's rule is the same text", menu.prompt_rule(crosstown) == menu.PROMPT_RULE)
    check("Crosstown's tool result still says 'menu'",
          "isn't on the menu" in str(menu.menu_price(crosstown, calzone)))
    check("named() returns the very same object when there's nothing to say",
          menu.named(calzone, crosstown) is calzone)

    heading("starting a booking: the channel's own verb")
    now = datetime(2026, 11, 12, 10, 0)
    sms = llm.build_system_prompt(miller, channel="sms", now=now)
    web = llm.build_system_prompt(miller, channel="webchat", now=now)
    voice = llm.build_system_prompt(miller, channel="voice", now=now)
    check("SMS: 'by texting' and 'text 'consultation''",
          "by texting 'book'" in sms and "text 'consultation' to start" in sms)
    check("web chat: 'by typing' and 'type 'consultation''",
          "by typing 'book'" in web and "type 'consultation' to start" in web
          and "texting" not in web and "text 'consultation'" not in web)
    check("voice: 'say'", "by saying 'book'" in voice and "say 'consultation'" in voice)
    mid = llm.build_system_prompt(crosstown, channel="webchat", mid_booking=True, now=now)
    check("mid-booking in web chat: 'Do NOT tell them to type'",
          "Do NOT tell them to type 'order'" in mid, mid[-900:])

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

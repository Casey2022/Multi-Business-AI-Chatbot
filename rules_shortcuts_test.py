#!/usr/bin/env python3
"""rules_shortcuts_test.py: each business's keyword shortcuts answer what
they're for, and step aside for anything else.

Run it:  python3 rules_shortcuts_test.py

Each business's YAML has keyword rules that answer instantly, without the
model. Two of them took questions that weren't theirs (2026-10-17):

- Opening hours: one keyword was the bare word "hours", so "My colour
  appointment is in about 30 hours. What happens if I cancel?" got the
  opening times instead of the cancellation charge (journey_eval "service
  rules", 0/3). The keywords are now phrases about opening hours.
- Cancelling or changing: "cancel my appointment" says "give us a call",
  right for doing it, wrong for "what's the charge if I cancel my
  appointment tomorrow?". An "unless" list (rules.py) makes it step aside
  when the message asks about charges, so the cancellation rules answer.
  Ridgeline's "how much" price rule steps aside for cancelling the same way.

This checks every business's rules, so a new config can't bring either
back. No server, network or model.
"""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


# Messages that count hours of notice: none of them is asking when the
# business is open. The first three are the journey_eval messages verbatim.
NOT_ABOUT_OPENING = [
    "My colour appointment is in about 30 hours. What happens if I cancel?",
    "My haircut is in about 30 hours. What happens if I cancel?",
    "My cut and colour is in about 30 hours. What happens if I cancel?",
    "I need to cancel, it's in 5 hours",
    "Do I need to give 24 hours' notice?",
    "can I move it with 48 hours notice",
    "the job took about 3 hours, is that normal?",
    "how many hours before pickup can I change my cake order?",
]
# Asking what cancelling or changing costs: the cancellation rules (or the
# knowledge base) answer these, never a canned "call us".
ABOUT_CHARGES = [
    "What's the charge if I cancel my appointment tomorrow?",
    "If I cancel my order 3 days before, do I get my deposit back?",
    "How much does it cost to cancel my appointment?",
    "what happens if I reschedule with 10 hours notice",
    "Is there a fee to change my order?",
    "do I get my money back if I cancel my order",
    "will I be charged if I change my appointment?",
]
ABOUT_OPENING = [
    "what are your hours", "What are your opening hours?", "business hours?",
    "when do you open tomorrow", "Are you closed on Mondays?", "hours of operation",
]


def main():
    import rules
    configs = sorted(p for p in (ROOT / "config").glob("*.yaml")
                     if (yaml.safe_load(p.read_text()) or {}).get("rules"))
    check("there are business configs to check", len(configs) >= 5, configs)
    for path in configs:
        config = yaml.safe_load(path.read_text())
        hours_rule = next((r for r in config["rules"] if r.get("name") == "hours"), None)
        if not hours_rule:
            continue
        print(f"\n{path.stem}\n{'-' * len(path.stem)}")
        opening = rules.substitute(hours_rule["reply"], config)
        check("no rule has the bare word 'hours' as a keyword",
              all("hours" not in r.get("keywords", []) for r in config["rules"]))
        stolen = [m for m in NOT_ABOUT_OPENING if rules.get_reply(m, config) == opening]
        check("a message counting hours of notice isn't answered with the opening times",
              not stolen, stolen)
        missed = [m for m in ABOUT_OPENING if rules.get_reply(m, config) != opening]
        check("a question about opening hours still is", not missed, missed)

    print("\ncancelling: doing it vs asking what it costs\n" + "-" * 44)
    for path in configs:
        config = yaml.safe_load(path.read_text())
        name = path.stem
        caught = {m: rules.get_reply(m, config) for m in ABOUT_CHARGES}
        caught = {m: r for m, r in caught.items() if r is not None}
        check(f"{name}: a question about what cancelling costs reaches the model",
              not caught, caught)
        cancel_rule = next((r for r in config["rules"] if r.get("name") == "cancel_existing"), None)
        if cancel_rule:
            canned = rules.substitute(cancel_rule["reply"], config)
            missed = [k for k in cancel_rule["keywords"]
                      if rules.get_reply(f"I need to {k}", config) != canned]
            check(f"{name}: asking to do it ('I need to cancel my ...') still gets the canned reply",
                  not missed, missed)
    ridgeline = yaml.safe_load((ROOT / "config" / "ridgeline_contracting.yaml").read_text())
    price = next(r for r in ridgeline["rules"] if r["name"] == "quote_price")
    check("Ridgeline: a project price question still gets its price answer",
          rules.get_reply("how much for a kitchen remodel?", ridgeline)
          == rules.substitute(price["reply"], ridgeline))

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

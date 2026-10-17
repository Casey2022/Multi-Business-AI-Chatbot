#!/usr/bin/env python3
"""rules_hours_test.py: the opening-hours shortcut answers questions about
opening hours, and nothing else.

Run it:  python3 rules_hours_test.py

Each business's YAML has a keyword rule that answers "We're open ..."
without asking the model. Until 2026-10-17 one of its keywords was the bare
word "hours", so "My colour appointment is in about 30 hours. What happens
if I cancel?" got the opening times instead of the cancellation charge
(journey_eval "service rules", 0/3). Any message that counts hours of
notice (cancelling, moving, "24 hours' notice") has the word in it. The
keywords are now phrases about opening hours. This checks every
business's rules, so a new config can't bring the bare word back. No
server, network or model.
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

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

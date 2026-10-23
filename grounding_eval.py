#!/usr/bin/env python3
"""grounding_eval.py — does grounding.py flag made-up facts, and only those?

Run it:
    python3 grounding_eval.py                 the full check (numbers + model)
    python3 grounding_eval.py --numbers       the numbers check only (free)
    python3 grounding_eval.py --model=claude-sonnet-5-5 [--repeat=3]

evals/grounding_cases.json holds 30 cases: 15 real replies (captured
2026-10-22, one per question, five businesses) with everything they could
rely on (get_llm_reply's `sources`), and the same 15 with one unsupported
fact planted in each: a changed price or time, a number in words ("three to
four weeks"), a number right for something else (24 hours for a patch
test), or a claim nobody made ("certified safe for celiacs"). One real
reply already had one: "$24.75" for a large pepperoni, added up by the
model after the menu tool declined to price it.

Two scores:
  caught      — planted facts flagged (the flag's text overlaps the plant)
  false flags — flags on text that isn't planted, across all 30 cases.
                Each is something an owner would be told is wrong when it
                isn't, which is how a warning gets ignored.
"""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import grounding
import llm
import progress
from progress import say

CASES = Path(__file__).resolve().parent / "evals" / "grounding_cases.json"


def _overlaps(reply, a, b):
    low = reply.lower()
    i, j = low.find(a.lower()), low.find(b.lower())
    return i != -1 and j != -1 and i < j + len(b) and j < i + len(a)


def _same_sentence(reply, a, b):
    import re
    return any(a.lower() in s.lower() and b.lower() in s.lower()
               for s in re.split(r"(?<=[.!?])\s+", reply))


def score(cases, check):
    caught, planted, false_flags, lines = 0, 0, 0, []
    for c in cases:
        flags = check(c["reply"], c["sources"])
        hits = [p for p in c["planted"]
                if any(_overlaps(c["reply"], f["phrase"], p) for f in flags)]
        # A flag in the same sentence as a plant is its consequence ("so
        # that's the latest you can order" after a planted wrong time).
        # "acceptable": a fair flag the case doesn't require either.
        wrong = [f for f in flags
                 if not any(_overlaps(c["reply"], f["phrase"], p)
                            or _same_sentence(c["reply"], f["phrase"], p)
                            for p in c["planted"])
                 and not any(_overlaps(c["reply"], f["phrase"], p)
                             for p in c.get("acceptable", []))]
        caught += len(hits)
        planted += len(c["planted"])
        false_flags += len(wrong)
        if len(hits) < len(c["planted"]) or wrong:
            lines.append(f"  {c['slug']} | {c['question']} [{c['kind']}]"
                         + (f"\n     missed: {[p for p in c['planted'] if p not in hits]}"
                            if len(hits) < len(c["planted"]) else "")
                         + "".join(f"\n     false flag: {f['phrase']!r} ({f['why']})"
                                   for f in wrong))
    return caught, planted, false_flags, lines


def main():
    cases = json.load(open(CASES))
    numbers_only = "--numbers" in sys.argv
    model = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--model=")), None)
    repeat = next((int(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--repeat=")), 1)
    check = (grounding.number_flags if numbers_only
             else lambda reply, sources: grounding.unsupported(reply, sources, model))
    llm.start_turn_accounting()
    failed = 0
    for run in range(repeat):
        caught, planted, false_flags, lines = score(cases, check)
        say(f"\nrun {run + 1}: caught {caught}/{planted}, false flags {false_flags} "
            f"({'numbers only' if numbers_only else model or llm.MODEL})")
        for line in lines:
            say(line)
        failed += caught < planted or false_flags > 0
    spent = llm.turn_cost_summary()
    if spent:
        say(spent)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

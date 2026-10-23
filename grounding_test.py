#!/usr/bin/env python3
"""grounding_test.py — the numbers half of grounding.py, offline.

Run it:  python3 grounding_test.py

The numbers check decides, in code, whether each number in a reply is in
what the reply could rely on. It must never flag a true reply (an owner
told "this is wrong" when it isn't stops reading the warnings), and it
must catch a changed price, time, distance or duration, in digits or in
words. The model half is measured by grounding_eval.py (it costs money);
here only its handling of the model's answer is tested, with a fake.
"""

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import grounding
import llm

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def flagged(reply, *sources):
    return [f["phrase"] for f in grounding.number_flags(reply, list(sources))]


def main():
    heading("the captured replies (evals/grounding_cases.json)")
    cases = json.load(open(ROOT / "evals" / "grounding_cases.json"))
    clean = [c for c in cases if not c["planted"]]
    false = [(c["question"], flagged(c["reply"], *c["sources"])) for c in clean]
    check(f"no flag on any of the {len(clean)} true replies",
          not any(f for _, f in false), [x for x in false if x[1]])
    numeric = {"$130", "$95", "10% off", "$23.75", "10:30pm", "three to four weeks",
               "five years", "2 days (48 hours)", "10 miles", "$24.75"}
    for c in cases:
        for p in c["planted"]:
            if p in numeric:
                got = flagged(c["reply"], *c["sources"])
                check(f"caught: {p!r} ({c['slug']})",
                      any(g.lower() in p.lower() or p.lower() in g.lower() for g in got), got)

    heading("numbers, in context")
    src = ("Standard cut $48. Patch test 48 hours before. Delivery within 5 miles. "
           "Kitchen remodels take five to eight weeks. Cancel 24 hours ahead or pay 50%. "
           "Open 7am-3pm. Pickup 15:30. Call (585) 555-0188. A 12-inch cake serves 30-40.")
    check("a supported price, duration, distance, percent, time and phone: nothing",
          flagged("A cut is $48, give 48 hours, we go 5 miles, it's 50%, we open at 7am, "
                  "pickup 3:30 PM, call (585) 555-0188.", src) == [])
    check("a changed price", flagged("A cut is $45.", src) == ["$45"])
    check("a number that's there, but for something else: '10% off' isn't any 10",
          flagged("Seniors get 10% off.", src + " Open 10 years.") == ["10%"])
    check("words before a unit, read as digits: 'five to eight weeks' is supported",
          flagged("It takes five to eight weeks.", src) == [])
    check("…'three to four weeks' isn't", flagged("It takes three to four weeks.", src)
          == ["three to four weeks"] or "four weeks" in " ".join(
              flagged("It takes three to four weeks.", src)))
    check("'4 days' is supported by '96 hours', and the other way round",
          flagged("Give us 4 days.", "needs 96 hours") == []
          and flagged("Give us 96 hours.", "needs 4 days") == [])
    check("a number the sources give with no unit supports it with one",
          flagged("Delivery takes 45 to 70 minutes on Fridays.",
                  "Delivery takes 30 to 45 minutes, or 45 to 70 on Friday evenings.") == [])
    check("a count of people matches the bare number ('serves 30-40')",
          flagged("It serves 30-40 people.", src) == [])
    check("a word number with no unit isn't a fact ('one of our estimators')",
          flagged("One of our estimators will call.", src) == [])
    check("a phone number nobody gave", flagged("Call (585) 555-0199.", src)
          == ["(585) 555-0199"])
    check("a time nobody gave", flagged("We close at 9pm.", src) == ["9pm"])
    check("12-hour and 24-hour clocks agree (15:30 is 3:30pm)",
          flagged("Pickup at 3:30pm.", "Pickup 15:30") == [])
    check("at most MAX_FLAGS", len(grounding.number_flags(
        "$1 $2 $3 $4 $5 $6", ["nothing"])) == grounding.MAX_FLAGS)

    heading("the model's answer, handled in code")
    real = llm._create

    def answering(text):
        return lambda **call: types.SimpleNamespace(
            usage=None, content=[types.SimpleNamespace(type="text", text=text)])
    try:
        llm._create = answering(json.dumps({"unsupported": [
            {"quote": "free parking", "why": "not stated"},
            {"quote": "something it never said", "why": "x"}]}))
        got = grounding.claim_flags("We have free parking out back.", ["Hours 9-5."])
        check("a quote from the reply is kept, with its reason",
              got == [{"phrase": "free parking", "why": "not stated"}], got)
        llm._create = answering('Here is the list:\n{"unsupported": [{"quote": "free parking", '
                                '"why": "not stated"}]}')
        check("an object after a sentence is still read",
              [f["phrase"] for f in grounding.claim_flags("We have free parking.", ["x"])]
              == ["free parking"])
        for bad in ("not json", '{"unsupported": "x"}', "[]"):
            llm._create = answering(bad)
            check(f"an unreadable answer ({bad!r}) flags nothing",
                  grounding.claim_flags("We have free parking.", ["x"]) == [])
        llm._create = lambda **c: (_ for _ in ()).throw(RuntimeError("down"))
        check("a failed call flags nothing (the numbers check still runs)",
              grounding.claim_flags("x", ["y"]) == []
              and grounding.unsupported("It's $9.", ["It's $8."]) == [
                  {"phrase": "$9", "why": "a price"}])
        llm._create = answering(json.dumps({"unsupported": [
            {"quote": "$9", "why": "price not stated"}]}))
        check("a claim the numbers check already flagged isn't listed twice",
              grounding.unsupported("It's $9.", ["It's $8."]) == [
                  {"phrase": "$9", "why": "a price"}])
        llm._create = answering(json.dumps({"unsupported": [
            {"quote": "(585) 555-0123", "why": "possible mismatch"},
            {"quote": "call (585) 555-0123 tonight", "why": "the wrong line"}]}))
        got = [f["phrase"] for f in grounding.unsupported(
            "Please call (585) 555-0123 tonight.", ["After hours call (585) 555-0123."])]
        check("a quote that's only a number the sources have is code's call, not the model's",
              got == ["call (585) 555-0123 tonight"], got)
        check("…which numbers count as 'only a number'",
              grounding._only_numbers("(585) 555-0123") and grounding._only_numbers("$24.75")
              and grounding._only_numbers("70 minutes") and grounding._only_numbers("9:45pm")
              and not grounding._only_numbers("free parking")
              and not grounding._only_numbers("call (585) 555-0124 right away"))
    finally:
        llm._create = real

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""json_object_test.py: a model's JSON is read wherever it is in the reply.

Run it:  python3 json_object_test.py

Three checks ask a model for "ONLY a JSON object" and read the answer:
Preview's Now/After comparison (preview.compare_facts), the grounding check
and the contradiction check. Models still add a sentence before it, a note
after, a ```json fence, a second object. Measured 2026-10-24 on
compare_facts: of six shapes, it read the clean and the fenced ones; a
sentence before, a note after, two objects and a "{" in the preamble all
fell back to "wording differs" (Preview then says Changes, with nothing to
show). The other two each read some of those and not others. Now all three
use llm.json_object, held here on every shape.

No API calls: the model is faked.
"""

import types

import booking_state_test as helpers    # stubs the integrations llm imports

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def shapes(obj):
    """The ways a reply has carried (or might carry) the object."""
    return {
        "clean": obj,
        "fenced": "```json\n" + obj + "\n```",
        "a sentence before": "Comparing the two, here is the result.\n" + obj,
        "a note after": obj + "\nNote: that's all I found.",
        "two objects (the first counts)": obj + "\n{\"other\": true}",
        "a brace in the preamble": "The {fee} part:\n" + obj,
    }


def replying(text):
    return lambda **call: types.SimpleNamespace(
        usage=None, content=[types.SimpleNamespace(type="text", text=text)])


def main():
    import llm

    heading("llm.json_object")
    for name, text in shapes('{"a": 1, "b": {"c": [1, 2]}}').items():
        try:
            got = llm.json_object(text)
        except ValueError as e:
            got = e
        check(f"{name}: the object", got == {"a": 1, "b": {"c": [1, 2]}}, got)
    for bad in ("no object here", "", None, '["a", "list"]', "{not json}", "{\"a\": 1"):
        try:
            llm.json_object(bad)
            raised = False
        except ValueError:
            raised = True
        check(f"{bad!r}: ValueError", raised)

    real = llm._create
    try:
        heading("Preview's Now/After comparison reads every shape")
        import preview
        obj = '{"facts_changed": true, "differences": [{"topic": "fee", "now": "$3", "after": "$5"}]}'
        for name, text in shapes(obj).items():
            llm._create = replying(text)
            r = preview.compare_facts("fee?", "The fee is $3.", "The fee is $5.")
            check(f"{name}: read, not the fallback", r["method"] == "model"
                  and r["differences"] == [{"topic": "fee", "now": "$3", "after": "$5"}], r)
        llm._create = replying("I couldn't compare these.")
        check("no object at all still falls back (never hides a change)",
              preview.compare_facts("q", "old", "new")["method"] == "exact")

        heading("the grounding check reads every shape")
        import grounding
        obj = '{"unsupported": [{"quote": "open till 9", "why": "the hours say 5"}]}'
        for name, text in shapes(obj).items():
            llm._create = replying(text)
            flags = grounding.claim_flags("We're open till 9 tonight.", ["Open 9am-5pm."])
            check(f"{name}: the flag", [f["phrase"] for f in flags] == ["open till 9"], flags)

        heading("the contradiction check reads every shape")
        import contradictions
        sections = [{"title": "Delivery", "body": "Delivery is free over $50."},
                    {"title": "Fees", "body": "Delivery costs $5 on every order."}]
        obj = ('{"contradictions": [{"a": {"where": "Delivery", "quote": "Delivery is free '
               'over $50."}, "b": {"where": "Fees", "quote": "Delivery costs $5 on every '
               'order."}, "why": "free vs $5"}]}')
        for name, text in shapes(obj).items():
            llm._create = replying(text)
            found = contradictions.check({"business": {}}, sections)
            check(f"{name}: the pair", len(found) == 1 and found[0]["b"]["where"] == "Fees",
                  found)
    finally:
        llm._create = real

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

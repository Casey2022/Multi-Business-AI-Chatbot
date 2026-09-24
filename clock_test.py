#!/usr/bin/env python3
"""clock_test.py — date parsing and the system prompt use the business's clock.

Run it:  python3 clock_test.py

Render's servers run on UTC. Before clock.py, at 9:30pm Eastern the server's
datetime.now() said 1:30am tomorrow, so parse_datetime told the model today
was Wednesday and "tomorrow" meant Thursday: a booking a day late, with
nothing anywhere saying so.

No API calls: llm.client is replaced by a fake that records the prompt it
was sent and answers with a fixed timestamp. The dates are hard-coded, which
is safe here because the clock is fixed too (clock.utc_now is replaced).
"""

import sys
import types
from datetime import datetime, timezone

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def import_llm():
    """llm.py, with only the modules this machine lacks stubbed out.

    On a full dev install nothing is stubbed. The fake client below is what
    keeps the test from calling the API either way.
    """
    class _Any:
        def __getattr__(self, name): return _Any()
        def __call__(self, *a, **k): return _Any()
    stubbed = set()
    for name in ("anthropic", "chromadb", "chromadb.utils",
                 "chromadb.utils.embedding_functions", "dotenv"):
        parent = name.rsplit(".", 1)[0]
        if parent != name and parent in stubbed:
            missing = True          # a stub has no real submodules to find
        else:
            try:
                __import__(name)
                missing = False
            except ImportError:
                missing = True
        if missing:
            module = types.ModuleType(name)
            module.__getattr__ = lambda attr: _Any()
            sys.modules[name] = module
            stubbed.add(name)
    if not hasattr(sys.modules["anthropic"], "Anthropic"):
        sys.modules["anthropic"].Anthropic = lambda *a, **k: _Any()
    import llm
    return llm


def main():
    import clock
    llm = import_llm()
    config = {"calendar": {"timezone": "America/New_York"}}

    sent = []
    def fake_create(**call):
        sent.append(call)
        text = types.SimpleNamespace(type="text", text="2026-09-23 09:00")
        return types.SimpleNamespace(content=[text], stop_reason="end_turn",
                                     usage=types.SimpleNamespace(input_tokens=0,
                                                                 output_tokens=0))
    llm.client = types.SimpleNamespace(messages=types.SimpleNamespace(create=fake_create))

    real_utc_now = clock.utc_now
    try:
        heading("parse_datetime reads the date where the business is")
        # Tuesday 22 Sep 2026, 9:30pm Eastern = Wednesday 1:30am UTC.
        clock.utc_now = lambda: datetime(2026, 9, 23, 1, 30, tzinfo=timezone.utc)
        result = llm.parse_datetime("tomorrow at 9am", config)
        # The RETURN value, not just the prompt. The first version of this
        # test only read the prompt, and passed while parse_datetime returned
        # None for every input (a NameError swallowed by its except), which
        # broke every booking on the live demo.
        check("parse_datetime returns the model's timestamp",
              result == "2026-09-23 09:00", repr(result))
        prompt = sent[-1]["messages"][0]["content"]
        check("at 9:30pm Eastern the model is told it's Tuesday",
              "Tuesday, September 22, 2026" in prompt,
              prompt.splitlines()[0])
        check("... and its 'tomorrow' example is Wednesday the 23rd",
              '"tomorrow morning" -> 2026-09-23' in prompt,
              [l for l in prompt.splitlines() if "tomorrow morning" in l])

        # And an answer that isn't a date comes back as None, not a crash.
        real_create = llm.client.messages.create
        llm.client.messages.create = lambda **c: types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="NONE")],
            stop_reason="end_turn",
            usage=types.SimpleNamespace(input_tokens=0, output_tokens=0))
        check("a model answer of NONE gives None",
              llm.parse_datetime("whenever", config) is None)
        llm.client.messages.create = lambda **c: types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="2026-02-31 09:00")],
            stop_reason="end_turn",
            usage=types.SimpleNamespace(input_tokens=0, output_tokens=0))
        check("an impossible date (Feb 31) gives None",
              llm.parse_datetime("feb 31", config) is None)
        llm.client.messages.create = real_create

        heading("the system prompt agrees with parse_datetime")
        system = llm.build_system_prompt({**config, "business": {
            "name": "Test", "phone": "", "address": "", "hours": ""},
            "bot": {"persona": "", "guardrails": ""}, "services": []})
        check("the receptionist is told the same day and the local time",
              "Tuesday, September 22, 2026, and the time is 9:30 PM" in system,
              [l.strip() for l in system.splitlines() if "Today is" in l])

        heading("the eval's pinned clock still wins")
        pinned = llm.business_now(config, now=datetime(2026, 9, 25, 17, 0))
        check("an explicit now= is returned untouched",
              pinned == datetime(2026, 9, 25, 17, 0))
    finally:
        clock.utc_now = real_utc_now

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

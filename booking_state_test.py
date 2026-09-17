#!/usr/bin/env python3
"""booking_state_test.py — the conversation-state race, reproduced and fixed.

Run it:  python3 booking_state_test.py

What this is about. On 16 September one web-chat request hung for 116
seconds inside a Google Calendar availability check. While it hung the
customer sent three more messages; each was handled correctly and saved.
Then the slow request finished and wrote its two-minute-old view of the
conversation over the top, deleting two answers and re-asking a question
the customer had already answered.

The test below builds that exact interleaving out of plain function calls —
no threads, no timing, no flakiness — because "slow request finishes last"
is just an ordering, and an ordering can be written down.
"""

import sys
import tempfile
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="state_test_")) / "test.db"

from db import get_state, init_db, set_state

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n" + "-" * len(text))


PHONE, BIZ = "web_test", 1


def main():
    init_db()

    heading("Revisions move forward")
    start = get_state(PHONE, BIZ)
    check("a conversation that doesn't exist starts at revision 0",
          start["revision"] == 0 and start["state"] == "idle")
    r1 = set_state(PHONE, BIZ, "collecting", {"service": "leak repair"})
    check("the first write returns revision 1", r1 == 1, r1)
    r2 = set_state(PHONE, BIZ, "collecting", {"service": "leak repair",
                                              "datetime": "Tuesday at 2"})
    check("the next write returns revision 2", r2 == 2, r2)
    check("and the state reads back at that revision",
          get_state(PHONE, BIZ)["revision"] == 2)

    heading("An unguarded write still works")
    # Callers outside a conversation turn pass no revision and must not be
    # made to care about one.
    check("a write with no expectation lands",
          set_state(PHONE, BIZ, "collecting",
                    {"service": "leak repair"}) == 3)

    heading("The race, as it actually happened")
    # The slow request reads the conversation and then goes away for two
    # minutes.
    slow = get_state(PHONE, BIZ)
    slow_pending = dict(slow["pending"])

    # Meanwhile the customer answers three more questions, each one a
    # complete turn that reads, writes, and moves the revision on.
    for key, value in (("service_address", "522 Penbrooke Dr"),
                       ("problem_description", "toilet is leaking"),
                       ("service_type", "no")):
        live = get_state(PHONE, BIZ)
        pending = dict(live["pending"])
        pending[key] = value
        written = set_state(PHONE, BIZ, "collecting", pending,
                            expect_revision=live["revision"])
        check(f"the customer's answer to {key} is saved", written is not None)

    before = get_state(PHONE, BIZ)
    check("three answers are on file", len(before["pending"]) == 4,
          before["pending"])

    # Now the slow request finishes and tries to write what it read.
    refused = set_state(PHONE, BIZ, "collecting", slow_pending,
                        expect_revision=slow["revision"])
    check("the slow request's write is refused", refused is None, refused)

    after = get_state(PHONE, BIZ)
    check("nothing the customer said was lost",
          after["pending"] == before["pending"], after["pending"])
    check("and the revision didn't move",
          after["revision"] == before["revision"])

    heading("What the bug did before the fix")
    # The same interleaving with no expectation passed: last writer wins,
    # and the customer's three answers disappear. Kept as a test so the
    # failure mode stays legible, and so a future change that quietly drops
    # the guard shows up here rather than in a transcript.
    set_state(PHONE, BIZ, "collecting", slow_pending)
    lost = get_state(PHONE, BIZ)
    check("an unguarded late write does destroy them",
          "problem_description" not in lost["pending"],
          "if this fails, the reproduction no longer reproduces")

    heading("Two turns that don't overlap are fine")
    live = get_state(PHONE, BIZ)
    a = set_state(PHONE, BIZ, "collecting", {"service": "a"},
                  expect_revision=live["revision"])
    b = set_state(PHONE, BIZ, "confirming", {"service": "b"},
                  expect_revision=a)
    check("each write chains onto the one before", b == a + 1, (a, b))
    check("the last one is what's stored",
          get_state(PHONE, BIZ)["state"] == "confirming")

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        import shutil
        shutil.rmtree(db.DB_PATH.parent, ignore_errors=True)

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


def _load_scheduler():
    """Import scheduler without the integrations it only needs at runtime.

    scheduler pulls in the calendar and the vector store at import time, and
    neither has anything to do with the question of which slots a booking
    should ask about. Stubbing them keeps this file what the rest of it
    already is: no network, no API keys, no flakiness.
    """
    import importlib.machinery as mach, types

    class Stub(types.ModuleType):
        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            child = Stub(f"{self.__name__}.{name}")
            setattr(self, name, child)
            return child
        def __call__(self, *a, **k): return Stub(self.__name__)
        def __getitem__(self, k):    return Stub(self.__name__)

    class Finder:
        ROOTS = {"google", "googleapiclient", "anthropic", "chromadb",
                 "phonenumbers", "flask", "twilio", "numpy"}
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in self.ROOTS:
                spec = mach.ModuleSpec(name, self, is_package=True)
                spec.submodule_search_locations = []
                return spec
        def create_module(self, spec):
            module = Stub(spec.name); module.__path__ = []; return module
        def exec_module(self, module): pass

    sys.meta_path.append(Finder())
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import scheduler
    return scheduler


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

    heading("A question set for some services is asked for those only")
    import yaml
    scheduler = _load_scheduler()
    config = yaml.safe_load(Path("config/belmont_hair_studio.yaml").read_text())
    slots  = scheduler.get_slot_definitions(config)

    def asked_for(service):
        """Every question this booking ends up answering, in order."""
        pending, order = ({"service": service} if service else {}), []
        while True:
            nxt = scheduler._first_missing_slot(pending, slots)
            if not nxt:
                return order
            if nxt["key"] != "service":
                order.append(nxt["key"])
            pending[nxt["key"]] = "answered"

    check("the condition survived into the slot definition",
          any(s.get("services") for s in slots),
          "get_slot_definitions dropped it, so nothing downstream can apply it")
    check("a colour booking is asked the colour question",
          "colour_history" in asked_for("balayage"))
    check("a kids cut is not",
          "colour_history" not in asked_for("kids cut"))
    check("the unconditional questions are still asked either way",
          "stylist_preference" in asked_for("kids cut")
          and "stylist_preference" in asked_for("balayage"),
          "a condition on one question must not affect its neighbours")
    check("matching ignores case and spacing",
          "colour_history" in asked_for("Cut  And  Colour"))
    # The one that would be a real bug: before the service is known, a
    # conditional question must be held back rather than asked on the chance
    # it applies -- but it must not be lost either.
    check("nothing conditional is asked before the service is known",
          scheduler._first_missing_slot({}, slots)["key"] == "service")
    check("a booking with no service never reaches finalize",
          scheduler._first_missing_slot({"datetime": "x"}, slots) is not None)

    heading("A yes or no is read back with the question that produced it")
    # The bug: "Have you been to us before?" answered "no" was read back as
    # "First visit: no" — which states the opposite of what the customer
    # said. The label and the question pointed in opposite directions, and
    # nothing could notice, because the label is just the key with the
    # underscore taken out.
    booked = {"service": "kids cut", "datetime_parsed": "2026-09-19 17:30",
              "stylist_preference": "no preference", "first_visit": "no"}
    said = scheduler._confirmation_question(booked, config)
    check("the question appears in the read-back",
          "Is this your first visit" in said, said)
    check("the bare label form is gone",
          "First visit: no" not in said,
          "label and answer alone can't say which way the question ran")
    check("a substantive answer still uses its short label",
          "Stylist preference: no preference" in said,
          "replaying a whole prompt for a real answer is just noise")

    check("yes and no are recognised whatever the punctuation",
          all(scheduler._is_bare_yes_no(a)
              for a in ("no", "No.", "YES", "nope", " y ")))
    # AFFIRMATIVE exists to spot agreement with a confirmation, and contains
    # words that are perfectly good answers to a question. Borrowing it here
    # would have swallowed them.
    check("a real answer is not mistaken for a bare yes/no",
          not any(scheduler._is_bare_yes_no(a)
                  for a in ("no preference", "kitchen sink dripping",
                            "perfect", "sounds good")))

    heading("Every question can be read back as a question")
    import glob
    for path in sorted(glob.glob("config/*.yaml")):
        if "personas" in path:
            continue
        cfg = yaml.safe_load(Path(path).read_text())
        for q in ((cfg.get("booking") or {}).get("extra_questions") or []):
            # Without a prompt the read-back silently falls back to the
            # ambiguous label form for yes/no answers.
            from config import question_only
            check(f"{Path(path).name}: {q['key']} keeps words once the aside is stripped",
                  bool(question_only(q.get("prompt", ""))),
                  "nothing left to quote back")

    heading("Restating an answer gets acknowledged, not echoed")
    plain = scheduler._confirmation_question(booked, config)
    led   = scheduler._confirmation_question(booked, config,
                                             lead="Thanks — I have that already.")
    check("the lead goes in front", led.startswith("Thanks — I have that already."))
    check("the confirmation itself is unchanged", plain in led)
    check("and the two replies differ", plain != led,
          "a customer who restates an answer must not get their own words "
          "back verbatim — that is what makes them restate it again")

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

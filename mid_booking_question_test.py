#!/usr/bin/env python3
"""mid_booking_question_test.py: a question mid-booking gets an answer.

Run it:  python3 mid_booking_question_test.py

Live, 2026-09-26, Sunrise Bakery. Asked "Anything else we should know?
(allergies, pickup person — or reply 'none')", the customer asked "do you
have gluten free?". The extractor filed it as the note "gluten free", the
read-back showed "Notes: gluten free", and the question was never
answered: the question check only ran when nothing was extracted. A
question at the read-back itself got "Sorry, I didn't quite catch that".

No API calls: the extractor and the Q&A answer are faked, and the fake
extractor does exactly what the real one did live.
"""

import sys
import tempfile
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="question_test_")) / "test.db"

import booking_state_test as helpers

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def main():
    sched = helpers._load_scheduler()
    from config import load_config
    from db import init_db, set_state, get_state
    init_db()
    cfg = load_config("config/sunrise_bakery_and_cafe.yaml")
    cfg["business"]["id"] = 1
    cfg.setdefault("calendar", {})["enabled"] = False
    phone, biz = "web_q", 1

    answered = []
    extract_returns = {}
    real = (sched.extract_booking_slots, sched._answer_mid_booking,
            sched.parse_datetime)
    sched.extract_booking_slots = lambda *a, **k: dict(extract_returns)
    # Reading a new date is a model call too; any open weekday will do.
    sched.parse_datetime = lambda text, config=None: "2026-10-05 11:00"
    sched._answer_mid_booking = lambda phone, message, *a, **k: (
        answered.append(message) or "Yes, we make gluten-free cupcakes with 96 hours' notice.")

    base = {"service": "cupcake orders (dozen minimum)", "customer_name": "Casey",
            "datetime": "Tuesday at 9am", "datetime_parsed": "2026-09-29 09:00",
            "customization": "none"}
    try:
        heading("the live conversation: a question at the notes prompt")
        set_state(phone, biz, "collecting", pending=dict(base))
        extract_returns.clear(); extract_returns["notes"] = "gluten free"
        reply = sched._handle_booking_inner(phone, "do you have gluten free?", cfg, biz)
        pending = get_state(phone, biz)["pending"]
        check("the question is answered", answered == ["do you have gluten free?"]
              and "gluten-free cupcakes" in reply, reply)
        check("and NOT stored as the note", "notes" not in pending, str(pending))
        check("and the notes question is asked again", "anything else" in reply.lower(),
              reply)

        heading("a real note still gets stored")
        answered.clear()
        extract_returns.clear(); extract_returns["notes"] = "gluten free please"
        sched._handle_booking_inner(phone, "gluten free please", cfg, biz)
        pending = get_state(phone, biz)["pending"]
        check("'gluten free please' is a note, not a question",
              pending.get("notes") == "gluten free please" and not answered,
              str(pending))

        heading("a question that carries a structured answer is still that answer")
        set_state(phone, biz, "collecting",
                  pending={k: v for k, v in base.items()
                           if k not in ("datetime", "datetime_parsed")})
        extract_returns.clear(); extract_returns["datetime"] = "Monday at 11"
        answered.clear()
        sched._handle_booking_inner(phone, "can I pick them up Monday at 11?", cfg, biz)
        pending = get_state(phone, biz)["pending"]
        check("'can I pick them up Monday at 11?' fills the time",
              pending.get("datetime") == "Monday at 11" and not answered,
              str(pending))

        heading("a question at the read-back")
        full = dict(base, notes="none")
        for extracted, label in (({}, "nothing extracted"),
                                 ({"notes": "gluten free"}, "read as a note")):
            set_state(phone, biz, "confirming", pending=dict(full))
            extract_returns.clear(); extract_returns.update(extracted)
            answered.clear()
            reply = sched._handle_booking_inner(phone, "do you have gluten free?",
                                                cfg, biz)
            state = get_state(phone, biz)
            check(f"({label}) the question is answered",
                  answered and "gluten-free cupcakes" in reply, reply)
            check(f"({label}) then the booking is read back again",
                  "Just to confirm" in reply and "Is that right?" in reply, reply)
            check(f"({label}) nothing changed, still waiting for a yes",
                  state["state"] == "confirming"
                  and state["pending"].get("notes") == "none", str(state))

        heading("a correction at the read-back is still a correction")
        set_state(phone, biz, "confirming", pending=dict(full))
        extract_returns.clear(); extract_returns["datetime"] = "Wednesday at 10"
        answered.clear()
        sched._handle_booking_inner(phone, "could we do Wednesday at 10 instead?",
                                    cfg, biz)
        state = get_state(phone, biz)
        check("'could we do Wednesday at 10 instead?' changes the time",
              state["pending"].get("datetime") == "Wednesday at 10" and not answered,
              str(state["pending"]))
    finally:
        (sched.extract_booking_slots, sched._answer_mid_booking,
         sched.parse_datetime) = real

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

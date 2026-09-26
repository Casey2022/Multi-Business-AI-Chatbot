#!/usr/bin/env python3
"""customer_name_test.py: every booking asks for, keeps and shows a name.

Run it:  python3 customer_name_test.py

Until 2026-09-26 an appointment had a phone number (on the demo, a random
web id like web_8051pzu7), a service and a time, and no name. The owner's
list, their calendar and their Google event all identified the customer by
that id.

No API calls: the extractor and the calendar are faked; the database is a
throwaway file.
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="name_test_")) / "test.db"

import booking_state_test as helpers     # its scheduler loader stubs the integrations

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def configs():
    from config import load_config
    out = {}
    for path in sorted(Path("config").glob("*.yaml")):
        if path.stem == "personas":      # shared personas, not a business
            continue
        cfg = load_config(str(path))
        cfg.setdefault("business", {})["id"] = 1
        cfg.setdefault("calendar", {})["enabled"] = False
        out[path.stem] = cfg
    return out


def test_storage():
    heading("the database keeps the name, and older databases gain the column")
    old = Path(tempfile.mkdtemp(prefix="name_old_")) / "old.db"
    conn = sqlite3.connect(old)
    conn.execute("""CREATE TABLE appointments (id INTEGER PRIMARY KEY,
                    business_id INTEGER NOT NULL, phone TEXT NOT NULL,
                    service TEXT NOT NULL, datetime TEXT NOT NULL,
                    status TEXT DEFAULT 'booked', details TEXT DEFAULT '{}')""")
    conn.execute("INSERT INTO appointments (business_id, phone, service, datetime) "
                 "VALUES (1, 'web_old', 'drain cleaning', '2026-09-01 10:00')")
    conn.commit(); conn.close()
    real = db.DB_PATH
    db.DB_PATH = old
    try:
        db.init_db()
        cols = {r[1] for r in sqlite3.connect(old).execute("PRAGMA table_info(appointments)")}
        check("an existing database gains customer_name on startup", "customer_name" in cols)
        row = sqlite3.connect(old).execute(
            "SELECT customer_name FROM appointments WHERE phone='web_old'").fetchone()
        check("appointments from before have no name (shown as '—')", row[0] is None)
    finally:
        db.DB_PATH = real

    db.init_db()
    db.save_appointment("web_1", "drain cleaning", "2026-10-01 10:00", 1,
                        details={"problem_description": "sink"},
                        customer_name="Casey")
    db.save_appointment("web_2", "drain cleaning", "2026-10-01 11:00", 1)
    rows = {r["phone"]: r for r in db.get_appointments(1)}
    check("save_appointment stores the name", rows["web_1"]["customer_name"] == "Casey")
    check("and stores nothing when there isn't one",
          rows["web_2"]["customer_name"] is None)
    check("the name is not duplicated into details",
          "Casey" not in (rows["web_1"]["details"] or ""))


def test_question(sched, cfgs):
    heading("every business asks for the name, second, in its own words")
    for slug, cfg in cfgs.items():
        slots = sched.get_slot_definitions(cfg)
        keys = [s["key"] for s in slots]
        check(f"{slug}: name asked right after the service",
              keys[:2] == ["service", "customer_name"], str(keys))
        check(f"{slug}: asked exactly once", keys.count("customer_name") == 1)
    bakery = sched.get_slot_definitions(cfgs["sunrise_bakery_and_cafe"])[1]
    check("the prompt uses the business's noun ('order' for the bakery)",
          bakery["prompt"] == "What name should I put this order under?",
          bakery["prompt"])
    check("the extractor is told not to take a stylist's name as the customer's",
          "stylist" in bakery["description"])

    cfg = cfgs["bobs_plumbing"]
    cfg["booking"]["extra_questions"] = list(cfg["booking"]["extra_questions"]) + [
        {"key": "customer_name", "prompt": "Who should we ask for when we arrive?"}]
    slots = sched.get_slot_definitions(cfg)
    names = [s for s in slots if s["key"] == "customer_name"]
    check("an owner's own name question is used once, with their wording",
          len(names) == 1 and names[0]["prompt"] == "Who should we ask for when we arrive?")


def test_clean(sched):
    heading("replies are tidied to just the name")
    for reply, expected in [("Casey", "Casey"), ("casey caudle", "Casey Caudle"),
                            ("It's Casey, thanks!", "Casey"),
                            ("my name is Casey Caudle", "Casey Caudle"),
                            ("Hi, this is Maria.", "Maria"), ("I'm Jo", "Jo"),
                            ("put it under Smith please", "Smith"),
                            ("Dr. Ruth O'Neil", "Dr. Ruth O'Neil"), ("   ", None)]:
        got = sched.clean_name(reply)
        check(f"{reply!r} -> {expected!r}", got == expected, f"got {got!r}")


def test_flow(sched, cfgs):
    heading("the booking flow fills, reads back and saves the name")
    from db import set_state, get_state
    cfg = cfgs["sunrise_bakery_and_cafe"]
    phone, biz = "web_flow", 1

    # The extractor finds nothing, so the raw reply becomes the answer.
    real_extract = sched.extract_booking_slots
    sched.extract_booking_slots = lambda *a, **k: {}
    try:
        set_state(phone, biz, "collecting", pending={"service": "custom cake"})
        reply = sched._handle_booking_inner(phone, "It's Casey, thanks!", cfg, biz)
    finally:
        sched.extract_booking_slots = real_extract
    pending = get_state(phone, biz)["pending"]
    check("an un-extracted reply to the name question is stored, tidied",
          pending.get("customer_name") == "Casey", str(pending))
    check("and the next question is asked", "when" in reply.lower() or "?" in reply,
          reply)

    pending.update({"datetime": "Friday at 10am",
                    "datetime_parsed": "2026-10-02 10:00",
                    "customization": "chocolate", "notes": "none"})
    readback = sched._confirmation_question(pending, cfg)
    check("the read-back says whose booking it is",
          "under the name Casey" in readback, readback)
    check("without a second 'Customer Name:' line", "Customer Name" not in readback
          and readback.count("Casey") == 1, readback)

    set_state(phone, biz, "confirming", pending=pending)
    sched._finalize_booking(phone, biz, pending, cfg)
    row = [r for r in db.get_appointments(biz) if r["phone"] == phone][-1]
    check("the saved appointment carries the name", row["customer_name"] == "Casey")
    check("and not in its details", "customer_name" not in (row["details"] or ""))

    heading("the calendar event is titled with the name")
    seen = {}
    class FakeCalendar:
        is_enabled = staticmethod(lambda cfg: True)
        is_slot_available = staticmethod(lambda *a, **k: True)
        @staticmethod
        def create_event(config, **kw):
            seen.update(kw); return "evt-1"
    real_cal = sched.calendar_sync
    sched.calendar_sync = FakeCalendar
    try:
        cfg["calendar"]["calendar_id"] = "cal"
        sched._finalize_booking("web_cal", biz, dict(pending), cfg)
    finally:
        sched.calendar_sync = real_cal
    check("create_event receives the name", seen.get("customer_name") == "Casey",
          str(seen))
    check("and it isn't in the event's detail lines",
          "customer_name" not in (seen.get("details") or {}))

    import calendar_google
    inserted = {}
    class Events:
        def insert(self, calendarId, body):
            inserted.update(body)
            return type("X", (), {"execute": lambda self: {"id": "g1"}})()
    real_service = calendar_google._get_service
    calendar_google._get_service = lambda: type("S", (), {"events": lambda self: Events()})()
    try:
        gcfg = dict(cfg, calendar={"calendar_id": "cal", "timezone": "America/New_York"})
        calendar_google.create_event(gcfg, "custom cake", "2026-10-02 10:00",
                                     "web_8051pzu7", details={}, customer_name="Casey")
        named = inserted.get("summary")
        calendar_google.create_event(gcfg, "custom cake", "2026-10-02 10:00",
                                     "web_8051pzu7", details={})
        unnamed = inserted.get("summary")
    finally:
        calendar_google._get_service = real_service
    check("Google event title: 'custom cake — Casey'", named == "custom cake — Casey",
          repr(named))
    check("without a name it falls back to the contact id",
          unnamed == "custom cake — web_8051pzu7", repr(unnamed))


def test_portal():
    heading("the owner's portal shows the name")
    t = Path("admin/templates/admin")
    listing = (t / "appointments.html").read_text()
    detail = (t / "appointment_detail.html").read_text()
    calendar = (t / "calendar.html").read_text()
    check("appointment list leads with the name, '—' when missing",
          'appt.customer_name or "—"' in listing)
    check("appointment page shows the name, '—' when missing",
          'appt.customer_name or "—"' in detail)
    check("calendar slots show the name", "appt.customer_name" in calendar)


def main():
    sched = helpers._load_scheduler()
    cfgs = configs()
    test_storage()
    test_question(sched, cfgs)
    test_clean(sched)
    test_flow(sched, configs())
    test_portal()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

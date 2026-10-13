#!/usr/bin/env python3
"""booking_control_test.py — pausing new bookings, and blocked time.

Run it:  python3 booking_control_test.py

The owner's two ways of saying when the assistant may book (2026-10-12):
"Taking new bookings?" on Settings, with an editable message and an
optional day it switches back on; and time blocked off on the Calendar
page, a whole day or specific hours. What matters most is that both are
enforced in code: a paused business can't be booked whatever the customer
says, and a blocked hour is never offered by either calendar.
No network, no model.
"""

import copy
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="booking_control_test_")) / "test.db"

import yaml

import booking_state_test as helpers

ROOT = Path(__file__).resolve().parent
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def next_weekday(day, weekday):
    return day + timedelta(days=(weekday - day.weekday()) % 7 or 7)


def main():
    helpers._load_scheduler()
    import booking_control as bc
    import scheduler
    import scheduling
    import llm
    from config import load_config
    db.init_db()

    bob = yaml.safe_load((ROOT / "config" / "bobs_plumbing.yaml").read_text())
    today = date(2026, 10, 12)

    heading("pausing new bookings")
    on = copy.deepcopy(bob)
    check("never set: taking bookings", not bc.paused(on, today))
    off = copy.deepcopy(bob); off["booking"]["accepting"] = False
    check("set to No: paused, until switched back", bc.pause_state(off, today) ==
          {"paused": True, "set_off": True, "resume_on": None, "resumed": False})
    off["booking"]["resume_on"] = "2026-10-20"
    check("with a resume day ahead: paused until then",
          bc.pause_state(off, today)["paused"] and bc.pause_state(off, today)["resume_on"] == date(2026, 10, 20))
    check("on the resume day it's on again by itself (no job flips anything)",
          not bc.paused(off, date(2026, 10, 20)) and bc.pause_state(off, date(2026, 10, 20))["resumed"])
    yes = copy.deepcopy(bob); yes["booking"]["accepting"] = True; yes["booking"]["resume_on"] = "2026-10-20"
    check("taking bookings, a leftover resume day means nothing", not bc.paused(yes, today)
          and bc.pause_state(yes, today)["resume_on"] is None)
    noun = bob["booking"].get("noun") or "appointment"
    check("the standard message names the booking and the phone number",
          bc.paused_reply(off) == f"Sorry, we're not taking new {noun}s right now. Please give us "
          f"a call at {bob['business']['phone']} for more information.", bc.paused_reply(off))
    custom = copy.deepcopy(off); custom["booking"]["paused_message"] = "Away till the 20th! Call {phone}."
    check("the owner's own wording, with {phone} filled in",
          bc.paused_reply(custom) == f"Away till the 20th! Call {bob['business']['phone']}.")

    heading("the booking flow refuses in code")
    conn = db.get_connection()
    conn.execute("INSERT INTO businesses (id, name, slug, config_path) VALUES (1, 'Bob''s', "
                 "'bobs_plumbing', 'config/bobs_plumbing.yaml')")
    conn.commit(); conn.close()
    paused = copy.deepcopy(bob); paused["booking"]["accepting"] = False
    reply = scheduler.handle_booking("+15855550142", "I need a plumber tomorrow at 10", paused, 1)
    check("asking to book while paused gets the owner's message",
          reply == bc.paused_reply(paused), reply)
    db.set_state("+15855550143", 1, "collecting", pending={"service": "drain cleaning"})
    reply = scheduler.handle_booking("+15855550143", "tomorrow at 10am", paused, 1)
    check("a booking already under way is stopped at the next message, and its state cleared",
          reply == bc.paused_reply(paused) and db.get_state("+15855550143", 1)["state"] == "idle",
          (reply, db.get_state("+15855550143", 1)))
    loaded = load_config("config/bobs_plumbing.yaml")
    loaded_paused = copy.deepcopy(loaded); loaded_paused["booking"]["accepting"] = False
    check("the assistant's instructions say so only while paused",
          "New bookings are paused" in llm.build_system_prompt(loaded_paused)
          and "New bookings are paused" not in llm.build_system_prompt(loaded))

    heading("blocked time in scheduling")
    monday = next_weekday(date.today(), 0)
    tuesday = monday + timedelta(days=1)
    blocked = copy.deepcopy(bob)
    blocked["calendar"]["scheduling"]["blocks"] = [
        {"start": f"{monday} 00:00", "end": f"{tuesday} 00:00"},          # Monday, all day
        {"start": f"{tuesday} 10:00", "end": f"{tuesday} 12:00"}]         # Tuesday 10 to 12
    iso = lambda d, t: f"{d} {t}"
    check("a whole-day block: nothing that day", not scheduling.is_slot_available(
        blocked, iso(monday, "10:00"), []) and not scheduling.is_slot_available(blocked, iso(monday, "15:00"), []))
    check("…and the customer is told it's not available (the note stays private)",
          scheduling.slot_rejection_reason(blocked, iso(monday, "10:00"), []) == "blocked"
          and "not available" in scheduler._unavailable_message(iso(monday, "10:00"), [], blocked, "blocked"))
    check("an hours block: an appointment that would run into it is refused too (9:30 + 60 min)",
          not scheduling.is_slot_available(blocked, iso(tuesday, "09:30"), [])
          and not scheduling.is_slot_available(blocked, iso(tuesday, "11:00"), []))
    check("…and the hours either side are fine",
          scheduling.is_slot_available(blocked, iso(tuesday, "09:00"), [])
          and scheduling.is_slot_available(blocked, iso(tuesday, "13:00"), []))
    alts = scheduling.find_alternatives(blocked, iso(monday, "10:00"), [])
    check("alternatives skip blocked time",
          alts and all(not a.startswith(str(monday)) and not (a.startswith(str(tuesday))
                       and "10:00" <= a[11:] < "12:00") for a in alts), alts)
    check("no blocks: the same Monday is open", scheduling.is_slot_available(bob, iso(monday, "10:00"), []))

    heading("blocks in the database, loaded with the config")
    b_id = db.add_time_block(1, f"{monday} 00:00", f"{tuesday} 00:00", all_day=True,
                             note="Vacation", created_by="o@x.com")
    db.add_time_block(1, "2020-01-01 00:00", "2020-01-02 00:00", all_day=True)
    cfg = load_config("config/bobs_plumbing.yaml", 1)
    check("load_config puts this business's current blocks in the scheduling settings",
          cfg["calendar"]["scheduling"]["blocks"] == [{"start": f"{monday} 00:00", "end": f"{tuesday} 00:00"}],
          cfg["calendar"]["scheduling"]["blocks"])
    check("…so the real config refuses that day", not scheduling.is_slot_available(cfg, iso(monday, "10:00"), []))
    check("another business can't remove it", not db.delete_time_block(2, b_id))
    check("its own business can", db.delete_time_block(1, b_id)
          and not any(b["id"] == b_id for b in db.list_time_blocks(1)))
    check("blocks go with a demo business", ("time_blocks", "business_id") in db._TENANT_TABLES)

    heading("the Calendar page's form")
    form = lambda **kw: kw
    block, errors = bc.parse_block_form(form(from_date="2026-10-20", all_day="yes"), today)
    check("whole day, one day: midnight to midnight",
          block == ("2026-10-20 00:00", "2026-10-21 00:00", True, "") and not errors, (block, errors))
    block, _ = bc.parse_block_form(form(from_date="2026-10-19", until_date="2026-10-23", all_day="yes",
                                        note="  Family   vacation "), today)
    check("whole days, a range: through the end of the last day, note tidied",
          block == ("2026-10-19 00:00", "2026-10-24 00:00", True, "Family vacation"), block)
    block, _ = bc.parse_block_form(form(from_date="2026-10-20", from_time="09:00", until_time="12:00"), today)
    check("specific hours, one day", block == ("2026-10-20 09:00", "2026-10-20 12:00", False, ""), block)
    block, _ = bc.parse_block_form(form(from_date="2026-10-19", until_date="2026-10-21",
                                        from_time="14:00", until_time="12:00"), today)
    check("specific hours across days: Monday 2pm through Wednesday noon",
          block == ("2026-10-19 14:00", "2026-10-21 12:00", False, ""), block)
    for label, f, word in [
            ("no day", form(all_day="yes"), "Choose the day"),
            ("hours with no times", form(from_date="2026-10-20"), "Choose a start time"),
            ("ends before it starts", form(from_date="2026-10-20", from_time="12:00", until_time="09:00"), "ends before"),
            ("Until before From", form(from_date="2026-10-20", until_date="2026-10-18", all_day="yes"), "ends before"),
            ("in the past", form(from_date="2026-10-01", all_day="yes"), "past"),
            ("not a date", form(from_date="tomorrow", all_day="yes"), "isn't a date")]:
        block, errors = bc.parse_block_form(f, today)
        check(f"refused in words: {label}", block is None and any(word in e for e in errors), errors)

    heading("what the owner sees")
    check("described the way a person says it",
          bc.describe({"starts_at": "2026-10-20 00:00", "ends_at": "2026-10-21 00:00", "all_day": 1}) == "Tue, Oct 20, all day"
          and bc.describe({"starts_at": "2026-10-19 00:00", "ends_at": "2026-10-24 00:00", "all_day": 1}) == "Mon, Oct 19 – Fri, Oct 23, all day"
          and bc.describe({"starts_at": "2026-10-20 09:00", "ends_at": "2026-10-20 12:30", "all_day": 0}) == "Tue, Oct 20, 9:00am – 12:30pm")
    blk = {"starts_at": "2026-10-20 10:00", "ends_at": "2026-10-20 12:00"}
    appts = [{"id": 1, "datetime": "2026-10-20 09:30", "service": "leak repair", "status": "booked"},
             {"id": 2, "datetime": "2026-10-20 11:00", "service": "leak repair", "status": "cancelled"},
             {"id": 3, "datetime": "2026-10-20 13:00", "service": "leak repair", "status": "booked"}]
    check("appointments already inside a block are found (not the cancelled one, not the one after)",
          [a["id"] for a in bc.appointments_in(blk, appts, bob)] == [1])
    cells = bc.cells([blk], [date(2026, 10, 19), date(2026, 10, 20)], [9, 10, 11, 12])
    check("the week grid shades exactly the blocked hours", set(cells) == {(1, 10), (1, 11)}, cells)

    heading("Settings and the portal")
    routes = (ROOT / "admin" / "routes.py").read_text()
    check("an empty or standard message goes back to the standard wording",
          "if not value or value in (booking_control.DEFAULT_MESSAGE," in routes
          and bc.default_message(bob) == f"Sorry, we're not taking new {noun}s right now. Please "
          "give us a call at {phone} for more information.")
    check("the resume day must be a real day after today",
          "choose a day after today" in routes and "that isn't a date" in routes)
    check("saving 'Yes' clears the resume day",
          'if request.form.get("booking.accepting") == "yes":\n            clear_config_override(business_id, "booking.resume_on")' in routes)
    check("taking bookings isn't pinned as an override (it's the default)",
          'field == "booking.accepting" and value is True and get_nested(base, field) is None' in routes)
    base = (ROOT / "admin" / "templates" / "admin" / "base.html").read_text()
    check("every business page reminds the owner while bookings are paused",
          "booking_pause(business)" in base and "New bookings are paused" in base)
    deps = (ROOT / "static" / "field-deps.js").read_text()
    check("the Whole day box hides the times (field-deps understands checkboxes)",
          '(controller.checked ? "checked" : "unchecked") === wanted' in deps
          and 'data-enabled-when="unchecked"' in (ROOT / "admin" / "templates" / "admin" / "calendar.html").read_text())

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

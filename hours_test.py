#!/usr/bin/env python3
"""hours_test.py — opening hours have one copy: the weekly table.

Run it:  python3 hours_test.py

Before 2026-10-23 the hours lived twice: the text customers are told
(business.hours, "Tue-Sun, 7am-3pm (closed Mondays)") and the weekly table
bookings follow (calendar.scheduling.business_hours). Owners could edit
the text and not the table. journey_eval "hours: the owner moved closing
to 5pm": the bot said "we close at 3pm" while bookings took 4pm, 0/3.
Now the text is made from the table, and owners edit the table.
"""

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.update(SECRET_KEY="t" * 64, COOKIE_SECURE="false",
                  ANTHROPIC_API_KEY="fake", VOYAGE_API_KEY="fake")

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="hours_test_")) / "test.db"

import yaml

import scheduling

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def week(days, start, end):
    return {d: [start, end] for d in days}


def main():
    heading("the text is made from the table, as the five businesses had it")
    # What each YAML said in words before the text field was removed.
    before = {"belmont_hair_studio": "Tue-Sat, 9am-6pm (closed Sun & Mon)",
              "bobs_plumbing": "Mon-Fri, 9am-5pm",
              "crosstown_pizza": "Tue-Sun, 11am-10pm (closed Mondays)",
              "ridgeline_contracting": "Mon-Fri, 7am-4pm",
              "sunrise_bakery_and_cafe": "Tue-Sun, 7am-3pm (closed Mondays)"}
    from config import load_config
    for slug, text in before.items():
        raw = yaml.safe_load(open(ROOT / "config" / f"{slug}.yaml"))
        check(f"{slug}: the YAML has no hours text, only the table",
              "hours" not in raw["business"] and raw["calendar"]["scheduling"]["business_hours"])
        check(f"{slug}: the text customers are told is what it was",
              load_config(f"config/{slug}.yaml")["business"].get("hours") == text,
              load_config(f"config/{slug}.yaml")["business"].get("hours"))

    heading("how a table is said")
    check("days in a row with the same hours are one group",
          scheduling.hours_text(week(range(5), "09:00", "17:00")) == "Mon-Fri, 9am-5pm")
    check("different hours on Saturday: a second group, and Sunday closed",
          scheduling.hours_text({**week(range(5), "09:00", "17:00"), 5: ["10:00", "14:00"]})
          == "Mon-Fri, 9am-5pm; Sat, 10am-2pm (closed Sundays)")
    check("half hours and noon", scheduling.hours_text({0: ["08:30", "12:00"]}).startswith(
        "Mon, 8:30am-12pm"))
    check("a gap in the week splits the group",
          scheduling.hours_text({0: ["09:00", "17:00"], 2: ["09:00", "17:00"]})
          == "Mon, 9am-5pm; Wed, 9am-5pm (closed Thu, Fri, Sat, Sun & Tue)")
    check("string keys (a saved override) read the same as YAML's ints",
          scheduling.hours_text({"1": ["07:00", "15:00"], "2": ["07:00", "15:00"]})
          == scheduling.hours_text({1: ["07:00", "15:00"], 2: ["07:00", "15:00"]}))
    check("no table, no text", scheduling.hours_text({}) == "")

    heading("the hours are in every prompt, and in no document")
    # After the table changed, the bot said "our hours are listed as 7am to
    # 3pm in one of our documents" (journey_eval, 2026-10-23): the prose was
    # a third copy. A line generated into the index went stale the same way
    # (it's rebuilt on Publish; hours take effect on save). So the hours live
    # in the prompt's business facts, made from the table, and nowhere else.
    import llm
    sun = load_config("config/sunrise_bakery_and_cafe.yaml")
    check("the business facts state the hours made from the table",
          "- Hours: Tue-Sun, 7am-3pm (closed Mondays)" in llm.build_system_prompt(sun, channel="webchat"))
    said = re.compile(r"\b(?:open|closed)\b[^.]{0,60}\b(?:monday|tuesday|wednesday|thursday|"
                      r"friday|saturday|sunday)s?\b[^.]{0,40}\b\d{1,2}\s*(?:am|pm)", re.I)
    for doc in sorted((ROOT / "documents").glob("*/*.md")):
        hits = said.findall(doc.read_text())
        check(f"{doc.parent.name}: the documents don't state the opening hours", not hits, hits)
    import menu
    check("no generated hours line in the index (it would go stale until Publish)",
          "hours" not in menu.section_lines_for("Pickup, Delivery, and Hours (Topics: x)", sun).lower())

    heading("an owner's change reaches both")
    db.init_db()
    conn = db.get_connection()
    conn.execute("INSERT INTO businesses (id, name, slug, config_path) VALUES "
                 "(9, 'Sunrise', 'sun9', 'config/sunrise_bakery_and_cafe.yaml')")
    conn.commit(); conn.close()
    db.set_config_override(9, "calendar.scheduling.business_hours",
                           {str(d): ["07:00", "17:00"] for d in range(1, 7)})
    cfg = load_config("config/sunrise_bakery_and_cafe.yaml", 9)
    check("customers are told the new closing time",
          cfg["business"].get("hours") == "Tue-Sun, 7am-5pm (closed Mondays)", cfg["business"].get("hours"))
    sched = scheduling.settings(cfg)
    from datetime import datetime
    check("and bookings follow it (Saturday 4pm is open)",
          scheduling.within_business_hours(datetime(2026, 9, 26, 16, 0), 30, sched))
    check("the table's keys are 0-6 ints after loading",
          set(cfg["calendar"]["scheduling"]["business_hours"]) == set(range(1, 7)))
    db.set_config_override(9, "business.hours", "Mon-Sun, 24 hours")
    check("an old hours-text override is ignored (the text has no field now)",
          load_config("config/sunrise_bakery_and_cafe.yaml", 9)["business"].get("hours")
          == "Tue-Sun, 7am-5pm (closed Mondays)")

    heading("the Settings table, read back")
    from config import parse_weekly_hours, EDITABLE_FIELDS
    f = "calendar.scheduling.business_hours"

    def form(**days):
        out = {f"{f}.posted": "1"}
        for day, (start, end) in days.items():
            d = int(day[1:])
            out.update({f"{f}[{d}].open": "1", f"{f}[{d}].from": start, f"{f}[{d}].to": end})
        return out
    table, errors = parse_weekly_hours(form(d1=("07:00", "15:00"), d6=("8:00", "12:30")), f)
    check("ticked days with their times; unticked days closed",
          not errors and table == {"1": ["07:00", "15:00"], "6": ["08:00", "12:30"]}, (table, errors))
    for name, bad, want in (("closing before opening", ("15:00", "07:00"), "after opening"),
                            ("not a time", ("seven", "15:00"), "opening and a closing")):
        _, errors = parse_weekly_hours(form(d2=bad), f)
        check(f"refused: {name}", any(want in e for e in errors), errors)
    _, errors = parse_weekly_hours({f"{f}.posted": "1"}, f)
    check("refused: every day closed (nothing could be booked)",
          any("at least one day" in e for e in errors), errors)
    check("a form without the table leaves the hours alone",
          parse_weekly_hours({"business.name": "x"}, f) == (None, []))
    check("the text isn't a field any more; the table is",
          "business.hours" not in EDITABLE_FIELDS and EDITABLE_FIELDS[f]["type"] == "hours")

    heading("on the page")
    import app as app_module
    import vector_boot
    vector_boot.run_in_subprocess = lambda *a, **k: None
    client = app_module.app.test_client()
    page = client.get("/demo").get_data(as_text=True)
    token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    client.post("/demo/start", data={"template": "sunrise_bakery_and_cafe", "_csrf_token": token})
    with client.session_transaction() as s:
        biz = db.get_user_by_email(s["user_email"])["business_id"]
    html = client.get(f"/admin/business/{biz}/settings").get_data(as_text=True)
    check("Settings shows a row per day, Monday unticked, and the text it makes",
          html.count('name="calendar.scheduling.business_hours[') == 21
          and 'name="calendar.scheduling.business_hours[0].open" value="1"\n                   >' in html
          and "Tue-Sun, 7am-3pm (closed Mondays)" in html)
    body = {"_csrf_token": token, f"{f}.posted": "1"}
    for d in range(1, 7):
        body.update({f"{f}[{d}].open": "1", f"{f}[{d}].from": "07:00", f"{f}[{d}].to": "17:00"})
    client.post(f"/admin/business/{biz}/settings", data=body)
    after = load_config(db.get_business_by_id(biz)["config_path"], biz)
    check("saving it changes what customers are told", after["business"].get("hours")
          == "Tue-Sun, 7am-5pm (closed Mondays)", after["business"].get("hours"))
    body.update({f"{f}[{d}].to": "15:00" for d in range(1, 7)})
    client.post(f"/admin/business/{biz}/settings", data=body)
    check("saving the YAML's own hours clears the override (it inherits the file again)",
          f not in db.get_config_overrides(biz))

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

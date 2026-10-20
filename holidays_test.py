#!/usr/bin/env python3
"""holidays_test.py — the holidays an owner ticks are closed days, everywhere.

Run it:  python3 holidays_test.py

holidays.py, 2026-10-20. Before, the assistant knew only the days blocked on
the Calendar page: Ridgeline's "three business days" from the Wednesday
before Thanksgiving was Monday the 30th, and Sunrise took a Thanksgiving
order. Now the owner ticks the holidays they close for ("Holidays we close"
on the Calendar page; Ridgeline's YAML ticks the usual seven) and each
becomes an all-day block, so the notice check, business days and bookings
treat it as closed, and the prompt lists them for a plain "are you open on
Thanksgiving?". Nothing is assumed for a holiday left unticked.

No network: fake keys, no vector build, a throwaway database.
"""

import copy
import logging
import os
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.update(SECRET_KEY="y" * 64, COOKIE_SECURE="false",
                  ANTHROPIC_API_KEY="fake", VOYAGE_API_KEY="fake")
os.environ.pop("ADMIN_EMAIL", None)

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="holidays_test_")) / "test.db"
import vector_boot
vector_boot.run_in_subprocess = lambda *a, **k: None

logging.disable(logging.CRITICAL)
import app as app_module
import holidays
import llm
import policy
import scheduler
from config import load_config
app = app_module.app

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n" + "-" * len(text))


def with_holidays(slug, keys, year=2026):
    """A business's config with these holidays ticked, as load_config makes it."""
    config = load_config(f"config/{slug}.yaml")
    config.setdefault("calendar", {})["holidays"] = keys
    config["calendar"].setdefault("scheduling", {})["blocks"] = holidays.blocks(keys, year)
    return config


def dates():
    heading("the dates")
    on = lambda key, y: holidays.HOLIDAYS[key][1](y)
    for key, want in (("thanksgiving", ["2025-11-27", "2026-11-26", "2027-11-25"]),
                      ("day_after_thanksgiving", ["2025-11-28", "2026-11-27", "2027-11-26"]),
                      ("memorial_day", ["2025-05-26", "2026-05-25", "2027-05-31"]),
                      ("labor_day", ["2025-09-01", "2026-09-07", "2027-09-06"]),
                      ("independence_day", ["2025-07-04", "2026-07-04", "2027-07-04"])):
        got = [str(on(key, y)) for y in (2025, 2026, 2027)]
        check(f"{key}: {', '.join(want)}", got == want, got)
    blocks = holidays.blocks(["thanksgiving"], 2026)
    check("a ticked holiday is an all-day block, the year before through the year after",
          {"start": "2026-11-26 00:00", "end": "2026-11-27 00:00"} in blocks
          and len(blocks) == 3, blocks)
    check("an unknown key is dropped",
          holidays.ticked({"calendar": {"holidays": ["thanksgiving", "x"]}}) == ["thanksgiving"])
    check("next dates roll to next year once a holiday has passed",
          holidays.next_dates(date(2026, 11, 27))["thanksgiving"] == date(2027, 11, 25))


def loading():
    heading("load_config: the YAML's list, or the owner's")
    ridge = db.get_business_by_slug("ridgeline_contracting")
    sunrise = db.get_business_by_slug("sunrise_bakery_and_cafe")
    blocked = lambda cfg: cfg["calendar"]["scheduling"]["blocks"]
    thanksgiving = {"start": "2026-11-26 00:00", "end": "2026-11-27 00:00"}
    config = load_config(ridge["config_path"], ridge["id"])
    check("Ridgeline's YAML ticks Thanksgiving: it's a block", thanksgiving in blocked(config))
    config = load_config(sunrise["config_path"], sunrise["id"])
    check("Sunrise ticks nothing: no holiday blocks", thanksgiving not in blocked(config))
    db.set_config_override(ridge["id"], "holidays", [], updated_by="test")
    config = load_config(ridge["config_path"], ridge["id"])
    check("the owner unticking everything beats the YAML",
          thanksgiving not in blocked(config) and holidays.ticked(config) == [])
    db.set_config_override(sunrise["id"], "holidays", ["thanksgiving"], updated_by="test")
    config = load_config(sunrise["config_path"], sunrise["id"])
    check("the owner ticking one at Sunrise makes it a block", thanksgiving in blocked(config))
    # Back to the files: demos started later copy the templates' settings.
    db.clear_config_override(ridge["id"], "holidays")
    db.clear_config_override(sunrise["id"], "holidays")


def the_rules():
    heading("notice, business days and bookings treat it as closed, and say why")
    sunrise = with_holidays("sunrise_bakery_and_cafe", ["thanksgiving"])
    r = policy.check_notice("2026-11-26T09:00", 24, "a dozen muffins", sunrise,
                            now=datetime(2026, 11, 25, 8, 0))
    check("Thanksgiving morning: refused though the notice is met", r["enough_notice"] is False)
    check("the tool names Thanksgiving", "closed for Thanksgiving" in r["say"], r["say"])
    check("and still says they're normally open Thursdays",
          "never say they're closed on Thursdays" in r["say"], r["say"])
    check("Friday 7am offered", r.get("next_open_after") == "Friday, November 27 at 7:00 AM",
          r.get("next_open_after"))

    plain = with_holidays("sunrise_bakery_and_cafe", [])
    r = policy.check_notice("2026-11-26T09:00", 24, "a dozen muffins", plain,
                            now=datetime(2026, 11, 25, 8, 0))
    check("unticked: Thanksgiving is an ordinary Thursday", r["enough_notice"] is True)

    ridge = with_holidays("ridgeline_contracting", ["thanksgiving", "day_after_thanksgiving"])
    r = policy.add_business_days("2026-11-25", 3, config=ridge)
    check("three business days from Nov 25 is Wednesday, December 2",
          r["date"] == "Wednesday, December 2", r["date"])
    check("both holidays named as the reason",
          "Thursday, November 26 (Thanksgiving)" in r["say"]
          and "Friday, November 27 (the day after Thanksgiving)" in r["say"], r["say"])
    r = policy.add_business_days("2026-11-25", 3,
                                 config=with_holidays("ridgeline_contracting", []))
    check("unticked: Monday, November 30", r["date"] == "Monday, November 30", r["date"])

    lead = scheduler._unavailable_message("2026-11-26 09:00", ["2026-11-27 07:00"], sunrise,
                                          reason="blocked")
    check("the booking flow says 'closed for Thanksgiving on …'",
          lead.startswith("We're closed for Thanksgiving on Thursday, November 26"), lead)
    lead = scheduler._unavailable_message("2026-11-26 09:00", ["2026-11-27 07:00"], plain,
                                          reason="blocked")
    check("an owner's own block: 'not available on …', no reason",
          lead.startswith("We're not available on Thursday, November 26"), lead)


def the_prompt():
    heading("the prompt lists them, for 'are you open on Thanksgiving?'")
    now = datetime(2026, 11, 20, 10, 0)
    ridge = load_config("config/ridgeline_contracting.yaml")
    prompt = llm.build_system_prompt(ridge, now=now)
    check("Ridgeline's prompt lists Thanksgiving with its next date",
          "Closed every year for Thanksgiving (next: Thursday, November 26, 2026)" in prompt)
    sunrise = load_config("config/sunrise_bakery_and_cafe.yaml")
    check("nothing ticked: no holiday lines (that prompt is unchanged)",
          "Closed every year" not in llm.build_system_prompt(sunrise, now=now))


def the_page():
    heading("the Calendar page: tick, save, and it's live")
    client = app.test_client()
    page = client.get("/demo").get_data(as_text=True)
    token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    client.post("/demo/start", data={"template": "ridgeline_contracting", "_csrf_token": token})
    with client.session_transaction() as s:
        business_id = db.get_user_by_email(s["user_email"])["business_id"]
    page = client.get(f"/admin/business/{business_id}/calendar").get_data(as_text=True)
    check("the page lists the holidays", "Holidays we close" in page and "Thanksgiving" in page)
    check("Ridgeline's YAML ticks show as ticked",
          re.search(r'value="thanksgiving"\s+checked', page) is not None)
    check("an unticked one shows unticked",
          re.search(r'value="christmas_eve"\s+checked', page) is None)
    r = client.post(f"/admin/business/{business_id}/calendar/holidays",
                    data={"_csrf_token": token, "holiday": ["christmas_eve", "nonsense"]})
    business = db.get_business_by_id(business_id)
    config = load_config(business["config_path"], business_id)
    check("saving keeps only real holidays", holidays.ticked(config) == ["christmas_eve"],
          holidays.ticked(config))
    check("and it's live: Christmas Eve is a block, Thanksgiving no longer",
          {"start": "2026-12-24 00:00", "end": "2026-12-25 00:00"}
          in config["calendar"]["scheduling"]["blocks"]
          and {"start": "2026-11-26 00:00", "end": "2026-11-27 00:00"}
          not in config["calendar"]["scheduling"]["blocks"])
    check("back to the page's holidays card", r.status_code == 302
          and r.headers["Location"].endswith("#holidays"), r.headers.get("Location"))
    other = app.test_client()
    page = other.get("/demo").get_data(as_text=True)
    t2 = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    other.post("/demo/start", data={"template": "ridgeline_contracting", "_csrf_token": t2})
    r = other.post(f"/admin/business/{business_id}/calendar/holidays",
                   data={"_csrf_token": t2, "holiday": ["thanksgiving"]})
    check("another visitor can't change them", r.status_code == 403, r.status_code)


def main():
    dates()
    loading()
    the_rules()
    the_prompt()
    the_page()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""config_check_test.py — a config mistake is said in words, not on a
customer's screen.

Run it:  python3 config_check_test.py

Measured 2026-10-23 on copies of the real YAMLs, before config_check.py:
of 21 plausible mistakes, half crashed where a customer would hit it (a
typo'd time zone, a menu price "$17", a booking question with no prompt)
and the rest went wrong silently (hours 17:00-09:00 told to customers as
"Mon, 5pm-9am"; services on one line became the services "d", "r", "a";
a 150% cancellation charge quoted). Here each one is made again and must
come back as a problem, at the right place and level; every shipped YAML
must have none; and the operator's Businesses page shows them.
"""

import os
import re
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.update(SECRET_KEY="t" * 64, COOKIE_SECURE="false",
                  ANTHROPIC_API_KEY="fake", VOYAGE_API_KEY="fake")

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="config_check_test_")) / "test.db"
import vector_boot
vector_boot.run_in_subprocess = lambda *a, **k: None

import config
import config_check

PASSED, FAILED = [], []
WORK = Path(tempfile.mkdtemp(prefix="config_check_files_"))


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def seed(slug):
    return yaml.safe_load((ROOT / "config" / f"{slug}.yaml").read_text())


def written(name, data=None, text=None):
    path = WORK / f"{name}.yaml"
    path.write_text(text if text is not None else
                    yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return path


def changed(slug, change):
    data = seed(slug)
    change(data)
    return data


def at(*keys, value=None, drop=False):
    def change(data):
        for key in keys[:-1]:
            data = data[key]
        if drop:
            del data[keys[-1]]
        else:
            data[keys[-1]] = value
    return change


HOURS = ("calendar", "scheduling", "business_hours")
BOB, BELMONT, CROSSTOWN = "bobs_plumbing", "belmont_hair_studio", "crosstown_pizza"

# (what the mistake is, the file, the level and the "where" it must be found at)
MISTAKES = [
    ("a tab indent (YAML can't be read)", None,       # written as text, not data
     lambda: written("tab", text=(ROOT / "config/bobs_plumbing.yaml").read_text()
                     .replace("  phone:", "\tphone:", 1)), "broken", "file"),
    ("no business name", BOB, at("business", "name", drop=True), "broken", "business.name"),
    ("no phone", BOB, at("business", "phone", drop=True), "broken", "business.phone"),
    ("'business' misspelled", BOB, lambda d: d.update(buisness=d.pop("business")),
     "warning", "buisness"),
    ("hours written 9am-5pm", BOB, at(*HOURS, 0, value=["9am", "5pm"]), "broken",
     "calendar.scheduling.business_hours.0"),
    ("hours 17:00-09:00", BOB, at(*HOURS, 0, value=["17:00", "09:00"]), "broken",
     "calendar.scheduling.business_hours.0"),
    ("a day 7", BOB, at(*HOURS, 7, value=["09:00", "17:00"]), "broken",
     "calendar.scheduling.business_hours.7"),
    ("an opening time only", BOB, at(*HOURS, 1, value=["09:00"]), "broken",
     "calendar.scheduling.business_hours.1"),
    ("a lunch break 13:00-12:00", BOB,
     lambda d: d["calendar"]["scheduling"]["blackout"][0].update(start="13:00", end="12:00"),
     "broken", "calendar.scheduling.blackout.0"),
    ("a shared rule misspelled", BOB,
     lambda d: d["bot"]["guardrails"]["universal"].__setitem__(0, {"rule": "facts_onyl"}),
     "broken", "bot.guardrails.universal"),
    ("a persona nobody has", BOB, at("bot", "persona_preset", value="drect"), "warning",
     "bot.persona_preset"),
    ("a shortcut with no reply", BOB, lambda d: d["rules"][0].pop("reply"), "broken",
     "rules.greeting"),
    ("shortcut keywords as one word", BOB, lambda d: d["rules"][0].update(keywords="hi"),
     "broken", "rules.greeting"),
    ("a length 'an hour'", BOB, at("calendar", "default_duration_minutes", value="an hour"),
     "broken", "calendar.default_duration_minutes"),
    ("a buffer '45 min'", BOB, at("calendar", "scheduling", "buffer_minutes", value="45 min"),
     "broken", "calendar.scheduling.buffer_minutes"),
    ("services on one line", BOB, at("services", value="drain cleaning, leak repair"),
     "broken", "services"),
    ("a time zone typo", BOB, at("calendar", "timezone", value="America/New_Yrok"),
     "broken", "calendar.timezone"),
    ("an old faq: block", BOB, at("faq", value=[{"q": "hours?", "a": "9-5"}]),
     "warning", "faq"),
    ("bot: as one word (nothing checks for it; loading does)", BOB, at("bot", value="friendly"),
     "broken", "file"),
    ("a 150% cancellation charge", BELMONT,
     lambda d: d["policies"]["cancellation"]["windows"][1].update(any={"charge_percent": 150}),
     "broken", "policies.cancellation.windows.1"),
    ("a window 'a day' before", BELMONT,
     lambda d: d["policies"]["cancellation"]["windows"][0].update(at_least_hours="a day"),
     "broken", "policies.cancellation.windows.0"),
    ("a booking question with no prompt", BELMONT,
     lambda d: d["booking"]["extra_questions"][0].pop("prompt"), "broken",
     "booking.extra_questions.stylist_preference"),
    ("a question for a service nobody offers", BELMONT,
     lambda d: d["booking"]["extra_questions"][2].update(services=["perm"]), "warning",
     "booking.extra_questions.colour_history"),
    ("a menu price '$17'", CROSSTOWN,
     lambda d: d["menu"][0]["options"].update({"14-inch medium": "$17"}), "broken", "menu.pizza"),
    ("a menu item with no prices", CROSSTOWN, lambda d: d["menu"][0].pop("options"), "broken",
     "menu.pizza"),
    ("a topping price 'two dollars'", CROSSTOWN,
     lambda d: d["menu"][0]["add_ons"]["topping"]["prices"].update({"10-inch personal": "two dollars"}),
     "broken", "menu.pizza"),
]


def main():
    heading("every shipped config: nothing wrong at all")
    paths = config.business_config_paths()
    check("there are business configs to check", len(paths) >= 5, paths)
    for path in paths:
        found = config_check.problems(path)
        check(f"{path.name}: no problems", found == [], found)

    heading("each mistake measured before: said, at the right place")
    for name, slug, make, level, where in MISTAKES:
        path = make() if slug is None else written(re.sub(r"\W+", "_", name),
                                                     changed(slug, make))
        found = config_check.problems(path)
        hit = [p for p in found if p["where"] == where and p["level"] == level]
        check(f"{name}: {level} at {where}", hit and all(p["says"].strip() for p in hit),
              found)
    tab = config_check.problems(WORK / "tab.yaml")
    check("a file that can't be read says which line", tab and "line 9" in tab[0]["says"], tab)
    reversed_ = config_check.problems(WORK / "hours_17_00_09_00.yaml")
    check("hours the wrong way round say what customers would be told",
          reversed_ and "Monday closes (09:00) before it opens (17:00)" in reversed_[0]["says"],
          reversed_)

    heading("odd files don't crash the check")
    for name, text in (("empty", ""), ("a list", "- one\n- two\n"), ("a word", "hello\n")):
        found = config_check.problems(written(f"odd_{name.replace(' ', '_')}", text=text))
        check(f"{name}: one broken problem, in words", len(found) == 1
              and found[0]["level"] == "broken", found)
    found = config_check.problems(WORK / "not_there.yaml")
    check("a missing file: said", found and found[0]["level"] == "broken", found)
    check("summary counts each level", config_check.summary(
        [{"level": "broken"}, {"level": "warning"}, {"level": "warning"}])
        == "1 broken, 2 warnings")

    heading("the operator's Businesses page shows them")
    import app as app_module
    app = app_module.app
    db.init_db()
    db.create_user("operator@example.test", "operator long passphrase", is_operator=True)
    biz = db.get_business_by_slug(BOB)
    client = app.test_client()
    page = client.get("/admin/login").get_data(as_text=True)
    token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    client.post("/admin/login", data={"_csrf_token": token, "email": "operator@example.test",
                                      "password": "operator long passphrase"})
    page = client.get("/admin/").get_data(as_text=True)
    check("clean configs: 'Config OK' for each", page.count("Config OK") >= 5,
          page.count("Config OK"))
    broken = written("dashboard_broken", changed(BOB, at(*HOURS, 0, value=["17:00", "09:00"])))
    conn = db.get_connection()
    conn.execute("UPDATE businesses SET config_path = ? WHERE id = ?", (str(broken), biz["id"]))
    conn.commit()
    conn.close()
    page = client.get("/admin/").get_data(as_text=True)
    check("a broken one: a red chip saying how many", "1 broken" in page and "chip-refused" in page)
    check("…and the sentence, where it is",
          "calendar.scheduling.business_hours.0" in page and "closes (09:00) before it opens" in page)
    check("…in a <details>, so it opens without JavaScript", "<details class=\"config-problems\">" in page)

    heading("startup says them too, and only through the check")
    source = (ROOT / "app.py").read_text()
    check("startup logs config_check.problems for each business",
          "config_check.problems(b[\"config_path\"], b[\"id\"])" in source)
    check("…instead of its own copies of the link checks",
          "unknown_notice_services(" not in source and "still has a faq" not in source)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

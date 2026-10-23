#!/usr/bin/env python3
"""services_test.py — a renamed service keeps what points at it.

Run it:  python3 services_test.py

Notice, length, booking questions and per-service cancellation rules name
a service. A rename on Settings broke all four without a word (2026-10-23):
"custom birthday cakes" renamed "custom cakes" lost its 72 hours' notice
(journey_eval "rename: custom cakes keep their notice", 0/3), and "colour"
renamed "all-over colour" lost its two hours, its colour-history question
and its own cancellation rules. Each service now has a fixed key and
remembers its old names (services.py); load_config points every link at
the current name.
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
db.DB_PATH = Path(tempfile.mkdtemp(prefix="services_test_")) / "test.db"

import services

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


SUN, BEL = "config/sunrise_bakery_and_cafe.yaml", "config/belmont_hair_studio.yaml"
COLOUR_RULES = {"title": "Cancelling", "what": "appointment", "action": "cancelled",
                "acting": "cancelling", "before": "the appointment", "price": "the service",
                "services": ["colour"],
                "windows": [{"at_least_hours": 48, "any": "no_charge", "colour": "no_charge"},
                            {"at_least_hours": 24, "any": "no_charge",
                             "colour": {"charge_percent": 50}},
                            {"at_least_hours": 0, "any": {"charge_percent": 50},
                             "colour": {"charge_percent": 50}}]}


def form_renaming(current, old, new):
    """What the Settings rows post with one name changed."""
    form = {"services.posted": "1"}
    for r in services.rows(current):
        form[f"services[{r['index']}].key"] = r["key"]
        form[f"services[{r['index']}].name"] = new if r["name"] == old else r["name"]
    return form


def main():
    import policy
    import scheduling
    from config import load_config
    db.init_db()
    conn = db.get_connection()
    conn.execute("INSERT INTO businesses (id, name, slug, config_path) VALUES "
                 f"(1, 'S', 's1', '{SUN}'), (2, 'B', 'b2', '{BEL}')")
    conn.commit(); conn.close()

    heading("a key per service, and every name it has had")
    cat = services.catalogue(["colour", {"key": "kids", "name": "kids' cut", "aka": ["kids cut"]},
                              "Colour"])
    check("a YAML name gets a key made from it; a saved row keeps its own",
          [s["key"] for s in cat][:2] == ["colour", "kids"], cat)
    check("two that make the same key don't share it", len({s["key"] for s in cat}) == 3, cat)
    check("resolve: by current name, an old name, or the key; else None",
          services.resolve("Kids Cut", cat) == "kids' cut"
          and services.resolve("kids", cat) == "kids' cut"
          and services.resolve("KIDS' CUT", cat) == "kids' cut"
          and services.resolve("perm", cat) is None)

    heading("a rename on Settings keeps every link")
    db.set_config_override(2, "cancellation", COLOUR_RULES)
    for biz, path, old, new in ((1, SUN, "custom birthday cakes", "custom cakes"),
                                (2, BEL, "colour", "all-over colour")):
        rows, errors = services.parse(form_renaming(load_config(path, biz), old, new),
                                      load_config(path, biz))
        check(f"{old!r} -> {new!r}: read back as the same key, the old name remembered",
              not errors and any(r["name"] == new and old in r["aka"] for r in rows), rows)
        db.set_config_override(biz, "services", rows)
    sun, bel = load_config(SUN, 1), load_config(BEL, 2)
    check("the services list says the new names",
          "custom cakes" in sun["services"] and "all-over colour" in bel["services"]
          and "colour" not in bel["services"])
    check("notice: custom cakes still need 72 hours",
          (policy.notice_for("custom cakes", sun) or {}).get("hours") == 72)
    check("length: all-over colour still takes two hours",
          scheduling.duration_for(bel, "all-over colour") == 120)
    check("questions: the colour-history question is still asked of it",
          any("all-over colour" in (q.get("services") or [])
              for q in bel["booking"]["extra_questions"]))
    rules = policy.cancellation(bel)
    check("cancellation: it keeps its own rules, window by window",
          rules["services"] == ["all-over colour"]
          and all("all-over colour" in w and "colour" not in w for w in rules["windows"]), rules)
    check("no startup warnings about names that match nothing",
          not scheduling.unknown_notice_services(sun) and not scheduling.unknown_duration_services(bel)
          and not scheduling.unknown_condition_services(bel))
    check("a service named by an old name in a message is still found",
          services.resolve("colour", bel["service_catalogue"]) == "all-over colour")

    heading("renamed twice, and drafts saved under an older name")
    rows, _ = services.parse(form_renaming(load_config(BEL, 2), "all-over colour", "full colour"),
                             load_config(BEL, 2))
    db.set_config_override(2, "services", rows)
    bel = load_config(BEL, 2)
    check("both old names are remembered, and the links follow",
          scheduling.duration_for(bel, "full colour") == 120
          and set(next(r for r in rows if r["name"] == "full colour")["aka"])
          == {"colour", "all-over colour"}, rows)
    import notice_form
    import policy_form
    notice_form.save_draft(1, {"custom birthday cakes": {"hours": 96}})
    check("a notice draft saved under the YAML's name applies to the renamed service",
          policy.notice_for("custom cakes", notice_form.with_draft(load_config(SUN, 1), 1))["hours"] == 96)
    policy_form.save_draft(2, COLOUR_RULES)
    check("…and a cancellation draft too",
          policy.cancellation(policy_form.with_draft(load_config(BEL, 2), 2))["services"]
          == ["full colour"])
    notice_form.discard_draft(1); policy_form.discard_draft(2)

    heading("the Settings rows, read back")
    current = load_config(SUN)
    form = {"services.posted": "1", "services[0].key": "custom_birthday_cakes",
            "services[0].name": "custom birthday cakes", "services[1].key": "wedding_cakes",
            "services[1].name": "", "services[7].key": "", "services[7].name": "  gift  boxes "}
    rows, errors = services.parse(form, current)
    check("an emptied row is removed; a new one gets a key from its name",
          not errors and [r["name"] for r in rows] == ["custom birthday cakes", "gift boxes"]
          and rows[1]["key"] == "gift_boxes", rows)
    _, errors = services.parse({"services.posted": "1", "services[0].name": "pie",
                                "services[1].name": "Pie"}, current)
    check("refused: the same service twice", any("twice" in e for e in errors), errors)
    _, errors = services.parse({"services.posted": "1"}, current)
    check("refused: no services at all", any("at least one" in e for e in errors), errors)
    check("a form without the rows leaves the services alone",
          services.parse({"business.name": "x"}, current) == (None, []))
    check("the YAML's own list, saved, clears the override",
          services.same_as_file(services.catalogue(current["services"]), current["services"]))
    check("an override saved before keys (a plain list) still loads",
          services.apply({"services": ["a", "b"]})["services"] == ["a", "b"])

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
    path = db.get_business_by_id(biz)["config_path"]
    html = client.get(f"/admin/business/{biz}/settings").get_data(as_text=True)
    check("Settings shows a row per service with its key, and blank rows to add",
          'name="services[0].key" value="custom_birthday_cakes"' in html
          and 'value="custom birthday cakes"' in html and 'placeholder="Add a service"' in html)
    body = dict(form_renaming(load_config(path, biz), "custom birthday cakes", "custom cakes"),
                _csrf_token=token)
    client.post(f"/admin/business/{biz}/settings", data=body)
    after = load_config(path, biz)
    check("renaming there keeps the 72 hours",
          "custom cakes" in after["services"]
          and (policy.notice_for("custom cakes", after) or {}).get("hours") == 72, after["services"])
    body = dict(form_renaming(after, "custom cakes", "custom birthday cakes"), _csrf_token=token)
    client.post(f"/admin/business/{biz}/settings", data=body)
    check("renaming it back clears nothing it shouldn't (the old name is still remembered)",
          load_config(path, biz)["services"][0] == "custom birthday cakes"
          and (policy.notice_for("custom birthday cakes", load_config(path, biz)) or {}).get("hours") == 72)

    heading("the whole Settings form at once: a rename and the questions' ticks")
    # Browser check, 2026-10-23: renaming "colour" in the same save as the
    # booking questions (ticked "colour", as the page was drawn) was refused
    # with "'Colour history' is set for 'colour', which this business
    # doesn't offer any more".
    from werkzeug.datastructures import MultiDict
    client = app_module.app.test_client()
    page = client.get("/demo").get_data(as_text=True)
    token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    client.post("/demo/start", data={"template": "belmont_hair_studio", "_csrf_token": token})
    with client.session_transaction() as s:
        bel_id = db.get_user_by_email(s["user_email"])["business_id"]
    html = client.get(f"/admin/business/{bel_id}/settings").get_data(as_text=True)
    form = MultiDict()
    # As a browser posts it: a select's selected option, nothing disabled.
    for name, body in re.findall(r'<select\b[^>]*name="([^"]+)"(?![^>]*disabled)[^>]*>(.*?)</select>',
                                 html, re.S):
        chosen = re.search(r'<option value="([^"]*)"\s*selected', body) or \
            re.search(r'<option value="([^"]*)"', body)
        form.add(name, chosen.group(1) if chosen else "")
    for tag in re.findall(r"<(?:input|textarea)\b[^>]*>", html):
        name = re.search(r'name="([^"]+)"', tag)
        if not name or re.search(r"\bdisabled\b", tag):
            continue
        kind = (re.search(r'type="([^"]+)"', tag) or [None, "text"])[1]
        if kind == "checkbox" and "checked" not in tag:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        form.add(name.group(1), value.group(1).replace("&#39;", "'").replace("&amp;", "&")
                 if value else "")
    for k in list(form.keys()):
        if k.startswith("services[") and k.endswith("].name") and form.get(k) == "colour":
            form.setlist(k, ["all-over colour"])
    response = client.post(f"/admin/business/{bel_id}/settings", data=form,
                           follow_redirects=True).get_data(as_text=True)
    after = load_config(db.get_business_by_id(bel_id)["config_path"], bel_id)
    check("no error about the renamed service", "doesn" not in response
          and "flash-error" not in response, re.findall(r'flash-error[^<]*<[^<]*', response)[:2])
    check("the rename is saved and the question still applies to it",
          "all-over colour" in after["services"]
          and any("all-over colour" in (q.get("services") or [])
                  for q in after["booking"]["extra_questions"]))

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

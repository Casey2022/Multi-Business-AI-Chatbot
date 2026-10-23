#!/usr/bin/env python3
"""notice_form_test.py — the owner's booking-notice form (notice_form.py).

Run it:  python3 notice_form_test.py

The notice table decides which times the booking flow refuses and what the
assistant says about notice, so the form must (1) give back exactly the
table it was shown when nothing is edited, words and all, (2) turn an
owner's edit into the table the booking flow reads, (3) refuse anything it
can't use, in words, and (4) keep a change a draft until Publish, which
also rebuilds the generated lines. No server, network or model.
"""

import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.update(SECRET_KEY="t" * 64, COOKIE_SECURE="false",
                  ANTHROPIC_API_KEY="fake", VOYAGE_API_KEY="fake")

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="notice_form_test_")) / "test.db"

import yaml

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def form_from(rows):
    """The form the page would post, unedited, for these rows."""
    form = {}
    for r in rows:
        p = r["slug"]
        form[f"{p}_amount"], form[f"{p}_unit"] = r["amount"], r["unit"]
        form[f"{p}_order_by"], form[f"{p}_section"] = r["order_by"], r["section"]
        for d in r["order_by_days"]:
            form[f"{p}_day_{d}"] = "1"
        for i, v in enumerate(r["variants"]):
            form[f"{p}_v{i}_amount"], form[f"{p}_v{i}_unit"] = v["amount"], v["unit"]
    return form


def yaml_config(slug):
    return yaml.safe_load(open(ROOT / "config" / f"{slug}.yaml"))


def main():
    import policy
    import notice_form as nf
    from config import load_config
    db.init_db()

    sunrise, crosstown = yaml_config("sunrise_bakery_and_cafe"), yaml_config("crosstown_pizza")

    heading("unedited, the form gives back exactly the table it showed")
    for slug, cfg in (("sunrise", sunrise), ("crosstown", crosstown)):
        back, errors = nf.parse(form_from(nf.rows(cfg)), cfg)
        check(f"{slug}: the same table, words and variants and all",
              not errors and back == nf.table(cfg), (errors, back, nf.table(cfg)))
    rows = {r["name"]: r for r in nf.rows(sunrise)}
    check("72 hours shows as 3 days, 24 hours as 1 day, two weeks as 14 days",
          (rows["custom birthday cakes"]["amount"], rows["custom birthday cakes"]["unit"])
          == ("3", "days") and rows["muffins by the dozen"]["amount"] == "1"
          and rows["wedding cakes"]["amount"] == "14")
    check("every service the business offers gets a row, notice or not",
          set(rows) >= {"custom birthday cakes", "pastry trays for events"}
          and [r["name"] for r in nf.rows(crosstown)][:1] != [], list(rows))
    check("a variant shows its name and the words that trigger it",
          rows["muffins by the dozen"]["variants"][0]["name"] == "gluten-free muffins"
          and rows["muffins by the dozen"]["variants"][0]["words"] == "gluten free")
    tray = next(r for r in nf.rows(crosstown) if r["order_by"])
    check("Crosstown's tray row carries its cutoff and days",
          tray["order_by"] == "16:00" and tray["order_by_days"] == ["friday", "saturday"], tray)

    heading("an edit becomes the table the booking flow reads")
    form = form_from(nf.rows(sunrise))
    form.update({"custom_birthday_cakes_amount": "4", "muffins_by_the_dozen_v0_amount": "5"})
    new, errors = nf.parse(form, sunrise)
    after = {"booking": {"notice": new}}
    check("custom cakes: 4 days is 96 hours", not errors
          and policy.notice_for("custom birthday cakes", after)["hours"] == 96, errors)
    check("gluten-free muffins: 5 days, and still triggered by the same words",
          policy.notice_for("muffins by the dozen", after, "gluten free")["hours"] == 120)
    check("the words that name a service in a question are kept",
          new["custom birthday cakes"]["words"] == ["custom cake", "birthday cake"])
    form = form_from(nf.rows(sunrise)); form["pastry_trays_for_events_amount"] = ""
    new, errors = nf.parse(form, sunrise)
    check("a blank is no notice: the service drops out of the table",
          not errors and "pastry trays for events" not in new
          and policy.notice_for("pastry trays for events", {"booking": {"notice": new}}) is None)
    form = form_from(nf.rows(sunrise)); form["muffins_by_the_dozen_v0_amount"] = ""
    new, _ = nf.parse(form, sunrise)
    check("a cleared variant is dropped", "variants" not in new["muffins by the dozen"])
    form = form_from(nf.rows(sunrise))
    form.update({"wedding_cakes_order_by": "12:00", "wedding_cakes_day_saturday": "1"})
    new, errors = nf.parse(form, sunrise)
    check("a cutoff can be added, with its days",
          not errors and new["wedding cakes"]["order_by"] == "12:00"
          and new["wedding cakes"]["order_by_days"] == ["saturday"], (errors, new["wedding cakes"]))
    form["wedding_cakes_day_saturday"] = ""
    new, _ = nf.parse(form, sunrise)
    check("…no days ticked means every day (no day list)",
          "order_by_days" not in new["wedding cakes"])
    form = form_from(nf.rows(crosstown))
    form[f"{tray['slug']}_order_by"] = "9:30"
    new, errors = nf.parse(form, crosstown)
    check("a time without its leading zero is stored as the code reads it",
          not errors and new[tray["name"]]["order_by"] == "09:30", (errors, new))
    form = form_from(nf.rows(sunrise)); form["custom_birthday_cakes_section"] = "Wedding Cakes"
    new, _ = nf.parse(form, sunrise)
    check("the section that shows the line can be moved",
          "custom birthday cakes" in policy.notice_line_for("Wedding Cakes", {"booking": {"notice": new}}))

    heading("anything the booking flow can't use is refused, in words")
    def errors_for(**changes):
        form = form_from(nf.rows(sunrise)); form.update(changes)
        return nf.parse(form, sunrise)[1]
    check("not a number", any("isn't a number" in e for e in errors_for(wedding_cakes_amount="two")))
    check("more than a year", any("365 days" in e for e in errors_for(wedding_cakes_amount="400",
                                                                       wedding_cakes_unit="days")))
    check("negative", any("365 days" in e for e in errors_for(wedding_cakes_amount="-1")))
    check("not a time", any("isn't a time" in e for e in errors_for(wedding_cakes_order_by="noon")))
    errs = errors_for(muffins_by_the_dozen_v0_amount="12", muffins_by_the_dozen_v0_unit="hours")
    check("a variant shorter than the service would never apply: said so",
          any("never apply" in e and "Gluten-free muffins" in e for e in errs), errs)

    heading("a change is a draft until Publish")
    conn = db.get_connection()
    conn.execute("INSERT INTO businesses (id, name, slug, config_path) VALUES "
                 "(7, 'Sunrise', 'sunrise', 'config/sunrise_bakery_and_cafe.yaml')")
    conn.commit(); conn.close()
    path = str(ROOT / "config" / "sunrise_bakery_and_cafe.yaml")
    form = form_from(nf.rows(sunrise)); form["custom_birthday_cakes_amount"] = "4"
    new, _ = nf.parse(form, sunrise)
    nf.save_draft(7, new, updated_by="o@x.com")
    live = load_config(path, 7)
    check("bookings still get the published notice",
          policy.notice_for("custom birthday cakes", live)["hours"] == 72)
    check("…the preview, the Knowledge page and the banner see the draft",
          policy.notice_for("custom birthday cakes", nf.with_draft(live, 7))["hours"] == 96
          and nf.waiting_lines(live, 7) == ["booking notice: changed"]
          and db.get_business_by_id(7)["documents_dirty"] == 1)
    nf.discard_draft(7)
    check("Discard drops it", nf.draft(7) is None and nf.waiting_lines(load_config(path, 7), 7) == [])
    nf.save_draft(7, new); nf.publish_draft(7)
    published = load_config(path, 7)
    check("Publish makes it the notice bookings follow",
          policy.notice_for("custom birthday cakes", published)["hours"] == 96 and nf.draft(7) is None)
    import scheduling
    import clock
    from datetime import timezone
    from zoneinfo import ZoneInfo
    real = clock.utc_now
    clock.utc_now = lambda: datetime(2026, 9, 22, 10, 0, tzinfo=ZoneInfo(
        "America/New_York")).astimezone(timezone.utc)
    try:
        check("…the booking flow now refuses a cake 3 days out, which 72 hours allowed",
              scheduling.slot_rejection_reason(published, "2026-09-25 10:00", [],
                                               "custom birthday cakes") == "notice"
              and scheduling.slot_rejection_reason(sunrise, "2026-09-25 10:00", [],
                                                   "custom birthday cakes") is None)
    finally:
        clock.utc_now = real
    check("…and the generated line says 4 days",
          "4 days (96 hours)" in policy.notice_line_for("Custom Birthday Cakes", published))
    nf.save_draft(7, {}); nf.publish_draft(7)
    check("an empty table published is no notice at all (even though the YAML has some)",
          policy.notice_for("wedding cakes", load_config(path, 7)) is None)

    heading("the page")
    import app as app_module
    import vector_boot
    vector_boot.run_in_subprocess = lambda *a, **k: None
    app = app_module.app
    client = app.test_client()
    page = client.get("/demo").get_data(as_text=True)
    token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    client.post("/demo/start", data={"template": "sunrise_bakery_and_cafe", "_csrf_token": token})
    with client.session_transaction() as s:
        biz = db.get_user_by_email(s["user_email"])["business_id"]
    page = client.get(f"/admin/business/{biz}/notice")
    html = page.get_data(as_text=True)
    check("it opens for the owner", page.status_code == 200, page.status_code)
    check("…with a card per service and what customers are told now",
          "Custom birthday cakes" in html and "3 days (72 hours)" in html
          and 'name="muffins_by_the_dozen_v0_amount"' in html, html[:300])
    check("…and Settings links to it",
          f"/admin/business/{biz}/notice" in client.get(
              f"/admin/business/{biz}/settings").get_data(as_text=True))
    live_cfg = load_config(db.get_business_by_id(biz)["config_path"], biz)
    form = form_from(nf.rows(live_cfg)); form["custom_birthday_cakes_amount"] = "4"
    form["_csrf_token"] = token
    r = client.post(f"/admin/business/{biz}/notice", data=form)
    check("saving redirects back, as a draft", r.status_code == 302
          and nf.draft(biz)["custom birthday cakes"]["hours"] == 96
          and policy.notice_for("custom birthday cakes",
                                load_config(db.get_business_by_id(biz)["config_path"], biz))
          ["hours"] == 72, r.status_code)
    html = client.get(f"/admin/business/{biz}/notice").get_data(as_text=True)
    check("the page shows now and after, and the banner",
          "After you publish" in html and "Custom birthday cakes: at least 4 days (96 hours)" in html
          and "booking notice: changed" in html)
    form["custom_birthday_cakes_amount"] = "soon"
    r = client.post(f"/admin/business/{biz}/notice", data=form)
    check("a bad value: the page again, the reason, nothing saved",
          r.status_code == 200 and "isn&#39;t a number" in r.get_data(as_text=True)
          and nf.draft(biz)["custom birthday cakes"]["hours"] == 96)
    form["custom_birthday_cakes_amount"] = "3"
    client.post(f"/admin/business/{biz}/notice", data=form)
    check("saving what's already live clears the draft", nf.draft(biz) is None)
    del form["_csrf_token"]
    r = client.post(f"/admin/business/{biz}/notice", data=form)
    check("no CSRF token: refused", r.status_code in (400, 403), r.status_code)

    heading("the wiring")
    routes = (ROOT / "admin" / "routes.py").read_text()
    page_src = routes.split("def booking_notice(")[1].split("\n@admin_bp")[0]
    publish = routes.split("def knowledge_publish(")[1].split("\n@admin_bp")[0]
    discard = routes.split("def knowledge_discard(")[1].split("\n@admin_bp")[0]
    check("the page checks business access and saves only drafts",
          "require_business_access(business_id)" in page_src[:400]
          and "notice_form.save_draft(" in page_src
          and "set_config_override" not in page_src and "publish_draft" not in page_src)
    order = [publish.find(s) for s in ("notice_form.with_draft(", "ingest_documents(",
                                        "notice_form.publish_draft(", "set_documents_clean(")]
    check("Publish builds the index with the draft, and promotes it only after that worked",
          -1 not in order and order == sorted(order), order)
    check("Discard drops it with everything else", "notice_form.discard_draft(business_id)" in discard)
    check("the banner lists it",
          "notice_form.waiting_lines(" in routes.split("def pending_changes(")[1][:1500])
    preview = (ROOT / "preview.py").read_text()
    check("Preview answers uses it (answers and the changed sections)",
          preview.count("notice_form.with_draft(") == 2)
    config_src = (ROOT / "config.py").read_text()
    check("load_config applies the published one, never the draft",
          'elif field == "notice":' in config_src and "notice_form.apply_live(" in config_src
          and "notice_form.draft(" not in config_src and "with_draft(" not in config_src)
    template = (ROOT / "admin" / "templates" / "admin" / "notice.html").read_text()
    check("every form on the page has a CSRF token",
          template.count("<form") == template.count("csrf_token()"))

    heading("a section that states notice itself is named")
    check("'72 hours' notice' and 'notice of 2 days' are found; 'more notice' isn't",
          nf.sections_stating_notice([
              {"title": "Cakes (Topics: x)", "body": "We need 72 hours' notice for cakes."},
              {"title": "Trays", "body": "Give us notice of 2 days."},
              {"title": "Cupcakes", "body": "Themed displays need more notice."}])
          == ["Cakes", "Trays"])

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

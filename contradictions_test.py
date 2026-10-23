#!/usr/bin/env python3
"""contradictions_test.py — the contradiction check, offline.

Run it:  python3 contradictions_test.py

The model's half is measured by contradiction_eval.py (it costs money).
Here, with the model faked: what the check reads (sections and settings,
drafts included), that a pair is kept only when both quotes really are in
the sources it names, that a bad answer finds nothing, and the Knowledge
page's button and results.
"""

import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.update(SECRET_KEY="t" * 64, COOKIE_SECURE="false",
                  ANTHROPIC_API_KEY="fake", VOYAGE_API_KEY="fake")

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="contradictions_test_")) / "test.db"

import contradictions
import llm

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def answering(text, seen=None):
    def create(**call):
        if seen is not None:
            seen.append(call)
        return types.SimpleNamespace(usage=None, model=call.get("model"),
                                     content=[types.SimpleNamespace(type="text", text=text)])
    return create


def pair(wa, qa, wb, qb, why="differ"):
    return {"a": {"where": wa, "quote": qa}, "b": {"where": wb, "quote": qb}, "why": why}


def main():
    from config import load_config
    sun = load_config("config/sunrise_bakery_and_cafe.yaml")
    sections = [{"title": "Pickup, Delivery, and Hours (Topics: delivery)",
                 "body": "We deliver within 5 miles for orders over $50."},
                {"title": "Muffins and Daily Pastries", "body": "We're open until 5pm."}]

    heading("what it reads")
    src = dict(contradictions.sources(sun, sections))
    check("each section by its title, without the Topics",
          "Pickup, Delivery, and Hours" in src and "Muffins and Daily Pastries" in src, list(src))
    check("and the settings: hours, menu prices, booking notice, cancellation rules",
          "Tue-Sun, 7am-3pm" in src.get("Business details", "")
          and "$45" in src.get("Menu prices", "")
          and "72 hours" in src.get("Booking notice", "")
          and src.get("Cancellation rules"), {k: v[:40] for k, v in src.items()})

    heading("a pair is kept only when both quotes are where it says")
    real = llm._create
    try:
        good = pair("Muffins and Daily Pastries", "We're open until 5pm.",
                    "Business details", "Hours: Tue-Sun, 7am-3pm (closed Mondays)")
        llm._create = answering(json.dumps({"contradictions": [
            good,
            pair("Muffins and Daily Pastries", "We're open until 9pm.",          # not said
                 "Business details", "Hours: Tue-Sun, 7am-3pm"),
            pair("Nowhere", "We're open until 5pm.", "Business details", "Hours: Tue-Sun"),
            pair("Muffins and Daily Pastries", "We're open until 5pm.",          # same quote twice
                 "Muffins and Daily Pastries", "we're open until 5pm."),
            {"a": "broken"}]}))
        got = contradictions.check(sun, sections)
        check("the real one is kept, with both sides and the reason",
              got == [{"a": good["a"], "b": good["b"], "why": "differ"}], got)
        llm._create = answering('Here you go:\n' + json.dumps({"contradictions": [good]})
                                + '\n{"another": 1}')
        check("an object after a sentence, with another after it, is still read",
              len(contradictions.check(sun, sections)) == 1)
        for bad in ("not json", '{"contradictions": "x"}', "[]"):
            llm._create = answering(bad)
            check(f"an unreadable answer ({bad!r}) finds nothing",
                  contradictions.check(sun, sections) == [])
        llm._create = lambda **c: (_ for _ in ()).throw(RuntimeError("down"))
        check("a failed call finds nothing", contradictions.check(sun, sections) == [])
        seen = []
        llm._create = answering('{"contradictions": []}', seen)
        contradictions.check(sun, sections, "claude-sonnet-5-5")
        check("the model asked for is the one called, and the text goes in as data",
              seen[0]["model"] == "claude-sonnet-5-5"
              and "<business_information>" in seen[0]["messages"][0]["content"])
    finally:
        llm._create = real

    heading("the Knowledge page")
    import app as app_module
    import vector_boot
    from admin import routes
    vector_boot.run_in_subprocess = lambda *a, **k: None
    client = app_module.app.test_client()
    page = client.get("/demo").get_data(as_text=True)
    token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    client.post("/demo/start", data={"template": "sunrise_bakery_and_cafe", "_csrf_token": token})
    with client.session_transaction() as s:
        biz = db.get_user_by_email(s["user_email"])["business_id"]
    html = client.get(f"/admin/business/{biz}/knowledge").get_data(as_text=True)
    check("the page has the button, in a form with a CSRF token",
          f"/admin/business/{biz}/knowledge/contradictions" in html and "Check for contradictions" in html)
    calls = []
    muffins = next(s for s in db.get_documents(biz) if s["title"].startswith("Muffins"))
    db.update_document_section(muffins["id"], muffins["title"],
                               muffins["body"] + " We're open until 5pm.", updated_by="owner")
    import notice_form
    notice_form.save_draft(biz, {"custom birthday cakes": {"hours": 120,
                                                           "section": "Custom Birthday Cakes"}})
    real = llm._create
    llm._create = answering(json.dumps({"contradictions": [pair(
        "Muffins and Daily Pastries", "We're open until 5pm.",
        "Business details", "Hours: Tue-Sun, 7am-3pm (closed Mondays)", "5pm against 3pm")]}), calls)
    try:
        r = client.post(f"/admin/business/{biz}/knowledge/contradictions",
                        data={"_csrf_token": token})
        html = r.get_data(as_text=True)
        check("an unsaved edit and a notice draft are what's checked (as it is now)",
              "We're open until 5pm." in calls[0]["messages"][0]["content"]
              and "5 days (120 hours)" in calls[0]["messages"][0]["content"])
        check("the clash is shown: both sides, where, and why",
              r.status_code == 200 and "Muffins and Daily Pastries:" in html
              and "We&#39;re open until 5pm." in html and "5pm against 3pm" in html, r.status_code)
        check("on the model the route names (Sonnet 5.5 unless set)",
              calls[0]["model"] == routes.CONTRADICTION_MODEL
              and 'os.environ.get("CONTRADICTION_MODEL", "claude-sonnet-5-5")'
              in (ROOT / "admin" / "routes.py").read_text())
        llm._create = answering('{"contradictions": []}')
        html = client.post(f"/admin/business/{biz}/knowledge/contradictions",
                           data={"_csrf_token": token}).get_data(as_text=True)
        check("none found: said so, with how many sections were read",
              "No contradictions found" in html
          and f"across {len(db.get_documents(biz))} sections" in " ".join(html.split()))
        r = client.post(f"/admin/business/{biz}/knowledge/contradictions", data={})
        check("no CSRF token: refused", r.status_code in (400, 403), r.status_code)
    finally:
        llm._create = real
        notice_form.discard_draft(biz)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

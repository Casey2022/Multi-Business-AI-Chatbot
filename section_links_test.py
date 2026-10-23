#!/usr/bin/env python3
"""section_links_test.py — a renamed section keeps its prices, list and notice.

Run it:  python3 section_links_test.py

Menu price lines, a topping list and booking-notice lines are shown under a
knowledge-base section chosen by its title. Renaming "Wings", "Toppings" and
"Custom Birthday Cakes" on the Knowledge page left the chunks Publish builds
with none of them (2026-10-23). section_links.py points each link at the
title its section has now, from the section's History. No network.
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
db.DB_PATH = Path(tempfile.mkdtemp(prefix="section_links_test_")) / "test.db"

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def section(biz, core):
    return next(s for s in db.get_documents(biz) if s["title"].split(" (")[0] == core)


def carried(biz, path):
    """{section core: what its chunk carries}, as Publish would build it."""
    import rag
    from config import load_config
    chunks, _, _ = rag.db_chunks(biz, load_config(path, biz))
    return {c.splitlines()[0][3:].split(" (")[0]: c for c in chunks}


def main():
    import section_links
    from import_documents import import_for_business
    db.init_db()
    CROSS, SUN = "config/crosstown_pizza.yaml", "config/sunrise_bakery_and_cafe.yaml"
    cross = db.add_business("Crosstown", "crosstown_pizza", CROSS)
    sun = db.add_business("Sunrise", "sunrise_bakery_and_cafe", SUN)
    for b in (cross, sun):
        import_for_business(db.get_business_by_id(b))

    heading("through the Knowledge page itself (a demo of the untouched seed)")
    import app as app_module
    import vector_boot
    vector_boot.run_in_subprocess = lambda *a, **k: None
    client = app_module.app.test_client()
    page = client.get("/demo").get_data(as_text=True)
    token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    client.post("/demo/start", data={"template": "crosstown_pizza", "_csrf_token": token})
    with client.session_transaction() as sess:
        demo = db.get_user_by_email(sess["user_email"])["business_id"]
    wings = next(s for s in db.get_documents(demo) if "Bone-in or boneless" in s["body"])
    client.post(f"/admin/knowledge/{wings['id']}/update",
                data={"_csrf_token": token, "title": "Wings Galore", "body": wings["body"]})
    html = client.get(f"/admin/business/{demo}/knowledge").get_data(as_text=True)
    block = html.split("Wings Galore", 1)[1].split('class="section-foot"', 1)[0] \
        if "Wings Galore" in html else ""
    check("renamed there, the page shows the wing prices under the new title",
          "Wings prices:" in block, block[:300])

    heading("nothing renamed, nothing moves")
    check("no renames", section_links.renames(cross) == {})
    before = carried(cross, CROSS)
    check("Wings carries its prices, Toppings its list",
          "Wings prices:" in before["Wings"] and "Toppings: meats" in before["Toppings"])

    heading("renamed on the Knowledge page")
    for biz, core, new in ((cross, "Wings", "Chicken Wings (Topics: wings, sauce)"),
                           (cross, "Toppings", "Pizza Toppings"),
                           (sun, "Custom Birthday Cakes", "Birthday Cakes (Topics: cake)")):
        s = section(biz, core)
        db.update_document_section(s["id"], new, s["body"], updated_by="owner")
    after = carried(cross, CROSS)
    check("the wing prices follow 'Wings' to 'Chicken Wings'",
          "Wings prices:" in after.get("Chicken Wings", ""), list(after))
    check("the topping list follows 'Toppings' to 'Pizza Toppings'",
          "Toppings: meats" in after.get("Pizza Toppings", ""))
    cakes = carried(sun, SUN).get("Birthday Cakes", "")
    check("the cake notice and the cake prices follow 'Custom Birthday Cakes'",
          "Notice for custom birthday cakes" in cakes and "Birthday cake prices" in cakes, cakes)
    check("…and nothing is left pointing at the old titles",
          "Wings prices:" not in "".join(v for k, v in after.items() if k != "Chicken Wings"))

    heading("renamed twice, and a title another section has")
    s = section(cross, "Chicken Wings")
    db.update_document_section(s["id"], "Wings and Things", s["body"], updated_by="owner")
    moved = section_links.renames(cross)
    key = section_links._core
    check("both earlier titles point at the newest",
          moved.get(key("Wings")) == "Wings and Things"
          and moved.get(key("Chicken Wings")) == "Wings and Things", moved)
    hours = section(cross, "Hours and Busy Times")
    db.update_document_section(hours["id"], "Wings", hours["body"], updated_by="owner")
    check("a title another section has now isn't borrowed ('Wings' is now the hours)",
          key("Wings") not in section_links.renames(cross))
    db.update_document_section(hours["id"], hours["title"], hours["body"], updated_by="owner")
    check("…and once the hours give it back, it's the section that had it first",
          section_links.renames(cross).get(key("Wings")) == "Wings and Things",
          section_links.renames(cross))

    heading("drafts saved under an older title follow too")
    import menu
    import notice_form
    from config import load_config
    draft = [dict(e) for e in load_config(CROSS)["menu"]]
    menu.save_draft(cross, draft)
    preview = menu.with_draft(load_config(CROSS, cross), cross)
    check("a menu draft that still says 'Wings'",
          "Wings prices:" in menu.price_lines_for("Wings and Things", preview))
    notice_form.save_draft(sun, {"custom birthday cakes": {"hours": 96,
                                                           "section": "Custom Birthday Cakes"}})
    import policy
    check("a notice draft that still says 'Custom Birthday Cakes'",
          "96 hours" in policy.notice_line_for(
              "Birthday Cakes", notice_form.with_draft(load_config(SUN, sun), sun)))
    menu.discard_draft(cross); notice_form.discard_draft(sun)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

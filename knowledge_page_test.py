#!/usr/bin/env python3
"""knowledge_page_test.py: the knowledge page says which sections differ
from what customers are answered from, and Save means something.

Run it:  python3 knowledge_page_test.py

Three things an owner leans on while editing:
- a "Changed since publish" / "New since publish" chip on each section that
  differs from the published snapshot, and the titles of deleted sections
  in the banner (db.compare_to_snapshot / unpublished_changes);
- a Save that stays grey until the section really differs and is complete,
  and a warning before leaving with unsaved typing (static/unsaved-edits.js,
  an enhancement — checked in a real browser when it was written);
- a server that refuses a blank section and treats a save that changes
  nothing (CRLF line endings, trailing spaces) as "Nothing changed" rather
  than filing a version and marking the knowledge unpublished.
No network, no browser.
"""

import sys
import tempfile
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="knowledge_page_test_")) / "test.db"

ROOT = Path(__file__).resolve().parent
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def business(biz):
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM businesses WHERE id = ?", (biz,)).fetchone()
    conn.close()
    return dict(row) if row else {"id": biz, "documents_dirty": 0}


def main():
    db.init_db()
    conn = db.get_connection()
    conn.execute("INSERT INTO businesses (id, name, slug, config_path) VALUES (7, 'Test', 'test', 'x.yaml')")
    conn.commit(); conn.close()

    heading("comparing with the published snapshot")
    snap = [{"id": 1, "position": 0, "title": "Sizes", "body": "Large $22."},
            {"id": 2, "position": 1, "title": "Delivery", "body": "6 miles."},
            {"id": 3, "position": 2, "title": "Hours", "body": "9 to 5."}]
    now = [{"id": 1, "title": "Sizes", "body": "Large $25."},
           {"id": 2, "title": "Delivery ", "body": "6 miles.\r\n"},
           {"id": 9, "title": "Calzones", "body": "$14."}]
    c = db.compare_to_snapshot(now, snap)
    check("an edited body is 'edited'", c["edited"] == {1}, c)
    check("line endings and surrounding spaces aren't an edit", 2 not in c["edited"], c)
    check("a section added since is 'new'", c["new"] == {9}, c)
    check("a deleted section is listed by title", c["deleted"] == ["Hours"], c)
    check("no snapshot reports nothing (not every section as new)",
          db.compare_to_snapshot(now, None) == {"edited": set(), "new": set(), "deleted": []})
    check("a changed title alone is an edit",
          db.compare_to_snapshot([{"id": 3, "title": "Opening hours", "body": "9 to 5."}],
                                 snap[2:])["edited"] == {3})

    heading("through the database")
    a = db.add_document_section(7, "Sizes", "Large $22.")
    b = db.add_document_section(7, "Delivery", "6 miles.")
    db.set_documents_clean(7)
    check("a clean business shows no chips",
          db.unpublished_changes(business(7)) == {"edited": set(), "new": set(), "deleted": []})
    ids = [s["id"] for s in db.get_documents(7)]
    db.update_document_section(ids[0], "Sizes", "Large $25.")
    added = db.add_document_section(7, "Calzones", "$14.")
    db.delete_document_section(ids[1])
    ch = db.unpublished_changes(business(7))
    new_ids = {s["id"] for s in db.get_documents(7)} - set(ids)
    check("after edit/add/delete: edited, new and deleted are reported",
          ch["edited"] == {ids[0]} and ch["new"] == new_ids and ch["deleted"] == ["Delivery"], ch)
    db.discard_document_changes(7)
    check("after Discard nothing is reported",
          db.unpublished_changes(business(7)) == {"edited": set(), "new": set(), "deleted": []})
    check("same_text ignores CRLF and surrounding whitespace",
          db.same_text("a\r\nb ", "a\nb") and not db.same_text("a b", "ab"))

    heading("the routes")
    routes = (ROOT / "admin" / "routes.py").read_text()
    renders = routes.count('"admin/knowledge.html"')
    check("every render of the page passes the changes (the template reads changes.deleted)",
          renders >= 2 and routes.count("changes = unpublished_changes(business)") == renders,
          renders)
    check("Save refuses a blank section (update and add)",
          routes.count('flash("A section needs both a title and content.", "error")') == 2)
    check("a save that changes nothing isn't filed",
          'flash("Nothing changed in that section.", "info")' in routes
          and "same_text(title, current[\"title\"]) and same_text(body, current[\"body\"])" in routes)
    check("submitted text is stored with plain newlines",
          routes.count("clean_section_text(request.form.get(") >= 4
          and 'replace("\\r\\n", "\\n")' in routes)

    heading("the page")
    page = (ROOT / "admin" / "templates" / "admin" / "knowledge.html").read_text()
    base = (ROOT / "admin" / "templates" / "admin" / "base.html").read_text()
    js = (ROOT / "static" / "unsaved-edits.js").read_text()
    check("each section has the publish chips",
          "Changed since publish" in page and "New since publish" in page)
    check("deleted sections are named in the banner", "changes.deleted" in page)
    check("section and add forms are tracked, with their buttons marked",
          page.count("data-track-edits") == 2 and page.count("data-save-button") == 2)
    check("the Unsaved chip starts hidden (the page is right without the script)",
          "data-unsaved-flag hidden" in page)
    check("Save is rendered enabled; only the script greys it",
          'type="submit" data-save-button disabled' not in page)
    check("base.html loads the script", "unsaved-edits.js" in base)
    check("the script compares against what the server rendered",
          "defaultValue" in js and "beforeunload" in js)
    check("the script ignores CRLF and surrounding whitespace like the server",
          "replace(/\\r\\n?/g, \"\\n\").trim()" in js)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

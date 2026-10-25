#!/usr/bin/env python3
"""guide_test.py — the owner guide as a page.

Run it:  python3 guide_test.py

admin/owner_guide.md was written for owners, but only the owner assistant
read it (2026-10-24): there was no page. /admin/guide shows the same text
the assistant gets (assistant.guide_text), rendered by admin/guide.py, a
small renderer for the part of Markdown the guide uses. Held here: the
renderer (headings, lists, bold, escaping), and the page (who sees it,
that it is the assistant's text, the links to it).
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
db.DB_PATH = Path(tempfile.mkdtemp(prefix="guide_test_")) / "test.db"
import vector_boot
vector_boot.run_in_subprocess = lambda *a, **k: None

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def signed_in(app, email, password):
    client = app.test_client()
    page = client.get("/admin/login").get_data(as_text=True)
    token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    client.post("/admin/login", data={"_csrf_token": token, "email": email,
                                      "password": password})
    return client


def main():
    from admin import guide as g
    import assistant

    heading("the renderer")
    html, contents = g.render(
        "## The pages\n\nSome **Save** text with `code`.\nSame paragraph.\n\n"
        "- First item\n  continued here.\n- Second **Publish changes**\n\nAfter.\n\n"
        "### A part\n\n<script>alert(1)</script> & **<b>x</b>**\n")
    check("## sections give the contents, with anchors", contents == [("the-pages", "The pages")],
          contents)
    check("headings have ids to jump to", '<h2 id="the-pages">' in html and '<h3 id="a-part">' in html)
    check("a paragraph's lines join", "<p>Some <strong>Save</strong> text with <code>code</code>. "
          "Same paragraph.</p>" in html, html)
    check("list items, an indented line continuing one",
          "<ul><li>First item continued here.</li><li>Second <strong>Publish changes</strong>"
          "</li></ul>" in html, html)
    check("text after a list is its own paragraph", "<p>After.</p>" in html)
    check("anything that looks like markup is escaped, bold or not",
          "<script>" not in html and "&lt;script&gt;" in html and "<b>" not in html
          and "&amp;" in html, html[-200:])

    heading("the page")
    import app as app_module
    app = app_module.app
    db.init_db()
    biz = db.get_business_by_slug("bobs_plumbing")
    db.create_user("owner@example.test", "owner long passphrase", business_id=biz["id"])
    db.create_user("operator@example.test", "operator long passphrase", is_operator=True)
    r = app.test_client().get("/admin/guide")
    check("signed out: sent to sign in", r.status_code == 302 and "/admin/login" in r.headers["Location"])
    owner = signed_in(app, "owner@example.test", "owner long passphrase")
    r = owner.get("/admin/guide")
    page = r.get_data(as_text=True)
    check("an owner can read it", r.status_code == 200 and "<h1>Owner guide</h1>" in page, r.status_code)
    _, expected = g.render(assistant.guide_text())
    check("…every section of the guide, in the contents and on the page",
          expected and all(f'href="#{a}"' in page and f'id="{a}"' in page for a, _ in expected),
          [t for a, t in expected if f'id="{a}"' not in page])
    check("…the text the assistant reads: its new sections are here",
          all(t in [title for _, title in expected] for t in
              ("Your password", "Things only your operator can change"))
          and 'id="booking-notice"' in page and 'id="holidays"' in page
          and 'id="check-for-contradictions"' in page)
    check("…not the maintainer note at the top of the file",
          "assistant_test.py" not in page and "Use bold only for" not in page)
    check("the nav links to it", 'href="/admin/guide"' in owner.get(
        f"/admin/business/{biz['id']}/settings").get_data(as_text=True))
    check("the Help page links to it", 'href="/admin/guide"' in owner.get("/admin/help")
          .get_data(as_text=True))
    operator = signed_in(app, "operator@example.test", "operator long passphrase")
    check("the operator can read it too", operator.get("/admin/guide").status_code == 200)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""tour_test.py — the demo tour shows for demos, and only for demos.

Run it:  python3 tour_test.py

static/demo-tour.js shows a demo visitor a few pop-up tips, each pointing at
one feature. The tips are attributes on the element they point at (the
ui.tip macro), so these checks render the real pages from a real app on a
throwaway database:

  * a demo's chat page and portal pages load the script and carry their
    tips; a real owner's same pages carry neither
  * every tip expected on a page is there (its target exists), with words
  * a tip shared by two pages (the feedback link) says the same on both
  * the script comes from /static, the pages add no inline handlers, and
    the script itself stays inside the CSP (no innerHTML, no style="")
    and survives blocked storage (every localStorage call inside try)

No network: fake keys, no vector build. No browser: placement and focus
were checked by hand in one (desktop and 375px).
"""

import logging
import os
import re
import sys
import tempfile
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.update(SECRET_KEY="t" * 64, COOKIE_SECURE="false",
                  ANTHROPIC_API_KEY="fake", VOYAGE_API_KEY="fake")
os.environ.pop("ADMIN_EMAIL", None)

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="tour_test_")) / "test.db"
import vector_boot
vector_boot.run_in_subprocess = lambda *a, **k: None

logging.disable(logging.CRITICAL)
import app as app_module
app = app_module.app

PASSED, FAILED = [], []
SLUG = "sunrise_bakery_and_cafe"       # has a menu, notice rules and cancellation rules
SCRIPT = (ROOT / "static" / "demo-tour.js").read_text()

# Which tips each page points at. The nav's two (3: Chat as a customer,
# 10: Help & feedback) are on every portal page.
EXPECTED = {
    "chat":      {1, 2, 10},
    "knowledge": {3, 4, 5, 10},
    "menu":      {3, 6, 10},
    "settings":  {3, 7, 10},
    "notice":    {3, 8, 10},
    "calendar":  {3, 9, 10},
}
TIP = re.compile(r'data-tour-step="(\d+)" data-tour-title="([^"]*)" data-tour-text="([^"]*)"')


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n" + "-" * len(text))


def demo_visitor():
    """A browser that started a demo, as /demo does. (client, business_id)."""
    client = app.test_client()
    page = client.get("/demo").get_data(as_text=True)
    token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    client.post("/demo/start", data={"template": SLUG, "_csrf_token": token})
    with client.session_transaction() as s:
        user = db.get_user_by_email(s["user_email"])
    return client, user["business_id"]


def real_owner():
    """The owner of the real Sunrise Bakery, signed in."""
    business = db.get_business_by_slug(SLUG)
    db.create_user("owner@sunrise.example", "a long test password", business["id"])
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_email"] = "owner@sunrise.example"
    return client, business["id"]


def pages(client, business_id, slug):
    base = f"/admin/business/{business_id}"
    paths = {"chat": f"/chat/{slug}", "knowledge": f"{base}/knowledge", "menu": f"{base}/menu",
             "settings": f"{base}/settings", "notice": f"{base}/notice",
             "calendar": f"{base}/calendar"}
    return {name: client.get(path) for name, path in paths.items()}


def main():
    demo, demo_id = demo_visitor()
    demo_slug = db.get_business_by_id(demo_id)["slug"]
    check("the demo is a demo (the fixture)", db.get_business_by_id(demo_id)["is_demo"])
    shown = pages(demo, demo_id, demo_slug)

    heading("a demo's pages carry the tour")
    words = {}
    for name, response in shown.items():
        html = response.get_data(as_text=True)
        tips = TIP.findall(html)
        steps = {int(n) for n, _, _ in tips}
        check(f"{name}: loads demo-tour.js from /static",
              response.status_code == 200
              and '<script src="/static/demo-tour.js" defer></script>' in html, response.status_code)
        check(f"{name}: every tip it should point at is on the page {sorted(EXPECTED[name])}",
              steps == EXPECTED[name], sorted(steps))
        check(f"{name}: a way to see the tour again (hidden until the script runs)",
              re.search(r'<button type="button" class="tour-again" data-tour-restart hidden>', html))
        check(f"{name}: each tip appears once", len(tips) == len(steps), [n for n, _, _ in tips])
        for n, title, text in tips:
            words.setdefault(int(n), set()).add((unescape(title), unescape(text)))
        check(f"{name}: no tip attribute is left without its words",
              html.count("data-tour-step=") == len(tips))

    heading("the tips themselves")
    every = set().union(*EXPECTED.values())
    check("6 to 10 tips in all, every one found on some page",
          6 <= len(every) <= 10 and set(words) == every, sorted(words))
    check("a tip on two pages says the same on both",
          all(len(v) == 1 for v in words.values()),
          {n: v for n, v in words.items() if len(v) > 1})
    for n in sorted(words):
        title, text = next(iter(words[n]))
        sentences = len(re.findall(r"[.!?](?:\s|$)", text))
        check(f"tip {n} ({title}): a title, one or two sentences, short",
              title and 1 <= sentences <= 2 and len(text) <= 160, (sentences, len(text), text))

    heading("a real owner never sees it")
    owner, real_id = real_owner()
    for name, response in pages(owner, real_id, SLUG).items():
        html = response.get_data(as_text=True)
        check(f"{name}: no script, no tips, no restart button",
              response.status_code == 200 and "demo-tour.js" not in html
              and "data-tour-" not in html, response.status_code)

    heading("the script is a /static file the CSP allows")
    served = demo.get("/static/demo-tour.js")
    check("served from /static as JavaScript",
          served.status_code == 200 and "javascript" in served.headers.get("Content-Type", ""),
          (served.status_code, served.headers.get("Content-Type")))
    served.close()
    for name, response in shown.items():
        html = response.get_data(as_text=True)
        inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
        check(f"{name}: the tour isn't in an inline script, and no inline handlers",
              not any("tour" in body for body in inline)
              and not re.search(r"\son[a-z]+\s*=\s*[\"']", html, re.I))
    check("the script builds with createElement/textContent, never innerHTML",
          "innerHTML" not in SCRIPT and "insertAdjacentHTML" not in SCRIPT)
    check("no style attribute strings (CSP refuses them); element.style only",
          'setAttribute("style"' not in SCRIPT and "cssText" not in SCRIPT)
    lines = SCRIPT.splitlines()
    unguarded = [i + 1 for i, line in enumerate(lines)
                 if "localStorage." in line and not line.lstrip().startswith("//")
                 and not any("try" in lines[j] for j in range(max(0, i - 1), i + 1))]
    check("every localStorage call is inside try (private windows)", not unguarded, unguarded)
    check("Esc closes, and reduced motion is respected",
          '"Escape"' in SCRIPT and "prefers-reduced-motion" in SCRIPT)
    check("the tip is a dialog whose words are announced",
          '"role", "dialog"' in SCRIPT and '"aria-live", "polite"' in SCRIPT)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

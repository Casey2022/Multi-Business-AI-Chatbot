#!/usr/bin/env python3
"""theme_test.py — Night glass and Daylight, and the switch between them.

Run it:  python3 theme_test.py

The site was dark only (Night glass, 2026-10-12). Daylight (2026-10-25) is
a second set of colour tokens on <html data-theme="light">, chosen with a
switch in every top bar and kept in the browser; Night glass stays the
default. Before it could work, 65 colours typed straight into app.css's
rules became tokens: a second theme would have missed every one of them.
Held here:
  - no colour is typed into a rule outside the token blocks (the drawn
    phone on the text-message page excepted: it looks like a phone in both);
  - every token a rule uses exists, and Daylight only restates real ones;
  - every text colour reaches 4.5:1 on what it's drawn over, in both
    themes (theme_contrast.py);
  - every page applies the kept choice before it draws, with the page's
    script nonce, and the switch is there, hidden until its script runs.
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
db.DB_PATH = Path(tempfile.mkdtemp(prefix="theme_test_")) / "test.db"
import vector_boot
vector_boot.run_in_subprocess = lambda *a, **k: None

import theme_contrast

PASSED, FAILED = [], []
CSS = (ROOT / "static" / "app.css").read_text()
PAGES = ("admin/templates/admin/base.html", "admin/templates/admin/error.html",
         "admin/templates/admin/config_unreadable.html", "templates/demo.html",
         "templates/demo_picker.html", "templates/text.html")
WITH_SWITCH = ("admin/templates/admin/base.html", "templates/demo.html",
               "templates/demo_picker.html", "templates/text.html")


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def outside_tokens(css):
    """The stylesheet without its two token blocks and the drawn phone."""
    for opener in (":root {", ':root[data-theme="light"] {'):
        start = css.index(opener)
        css = css[:start] + css[css.index("\n}", start) + 2:]
    return re.sub(r"theme: fixed.*?theme: end fixed", "", css, flags=re.S)


def main():
    heading("every colour is a token")
    stray = re.findall(r"#[0-9A-Fa-f]{3,8}\b|rgba?\([^)]*\)", outside_tokens(CSS))
    check("no colour typed into a rule (outside the tokens and the drawn phone)",
          stray == [], stray[:8])
    themes = theme_contrast.tokens(CSS)
    used = set(re.findall(r"var\(--([\w-]+)\)", CSS))
    check("every token a rule uses is defined", used <= set(themes["dark"]),
          sorted(used - set(themes["dark"])))
    light_own = set(re.findall(r"--([\w-]+):", theme_contrast._block(
        CSS, ':root[data-theme="light"] {')))
    check("Daylight restates only tokens Night glass has (no misspelt one)",
          light_own <= set(re.findall(r"--([\w-]+):", theme_contrast._block(CSS, ":root {"))),
          sorted(light_own - set(themes["dark"])))
    check("Night glass is the default (dark, on :root), Daylight only with data-theme",
          "color-scheme: dark" in theme_contrast._block(CSS, ":root {")
          and "color-scheme: light" in theme_contrast._block(CSS, ':root[data-theme="light"] {')
          and "prefers-color-scheme" not in CSS)

    heading("every text colour reads, in both themes (4.5:1)")
    for name, tokens in themes.items():
        low = [(what, round(r, 2)) for what, r in theme_contrast.pairs(tokens)
               if r < theme_contrast.BAR]
        check(f"{name}: {len(theme_contrast.pairs(tokens))} pairs, none below 4.5", not low, low)
    check("Daylight's text really is darker than Night glass's (not a copy)",
          themes["light"]["ink"] != themes["dark"]["ink"]
          and themes["light"]["paper"] != themes["dark"]["paper"])

    heading("every page applies the choice, and has the switch")
    head = (ROOT / "templates" / "_theme_head.html").read_text()
    script = (ROOT / "static" / "theme.js").read_text()
    check("the head script and theme.js keep the choice under the same name",
          '"simon-theme"' in head and '"simon-theme"' in script)
    for path in PAGES:
        src = (ROOT / path).read_text()
        include = src.find('{% include "_theme_head.html" %}')
        sheet = src.find("filename='app.css'")
        check(f"{path}: the choice is applied before the stylesheet", -1 < include < sheet,
              (include, sheet))
    for path in WITH_SWITCH:
        check(f"{path}: has the switch", '{% include "_theme_toggle.html" %}'
              in (ROOT / path).read_text())
    base = (ROOT / "admin/templates/admin/base.html").read_text()
    check("the portal's switch is outside the block the sign-in page empties",
          base.index('{% include "_theme_toggle.html" %}')
          > base.index("{% endblock %}", base.index("{% block navlinks %}")))

    import app as app_module
    client = app_module.app.test_client()
    for url in ("/demo", "/admin/login", "/chat/bobs_plumbing", "/chat/bobs_plumbing/text"):
        r = client.get(url)
        page = r.get_data(as_text=True)
        nonce = re.search(r"'nonce-([^']+)'", r.headers["Content-Security-Policy"]).group(1)
        check(f"{url}: the head script carries this page's nonce, before the stylesheet",
              f'<script nonce="{nonce}">' in page
              and -1 < page.find('localStorage.getItem("simon-theme")') < page.find("app.css"))
        check(f"{url}: the switch, hidden until theme.js runs",
              re.search(r'<button[^>]*data-theme-toggle[^>]*\bhidden\b', page) is not None
              and "/static/theme.js" in page)
        check(f"{url}: no theme chosen in the markup (Night glass for everyone)",
              'data-theme="light"' not in page.split("<body")[0].split("<script")[0])

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

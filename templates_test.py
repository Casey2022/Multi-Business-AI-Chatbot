#!/usr/bin/env python3
"""templates_test.py — every template compiles.

Run it:  python3 templates_test.py

A template with a stray {% endif %} is a 500 on the one page that uses it,
and nothing else notices: routes_test reads url_for() calls, styles_test
reads class names, and the page tests read strings. It happened on
2026-10-12: swapping the knowledge page's banner for the shared macro cut at
the wrong {% endif %} and left half the old banner behind, caught only when
the page was rendered. This compiles every template with Jinja (the same
parser Flask uses). No Flask, no server.
"""

import sys
from pathlib import Path

import jinja2

ROOT = Path(__file__).resolve().parent
DIRS = [ROOT / "admin" / "templates", ROOT / "templates"]


def main():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader([str(d) for d in DIRS if d.exists()]))
    passed, failed = 0, []
    for d in DIRS:
        for path in sorted(d.rglob("*.html")):
            name = str(path.relative_to(d))
            try:
                env.parse(path.read_text(encoding="utf-8"), name=name)
                passed += 1
            except jinja2.TemplateSyntaxError as e:
                failed.append(f"{name}:{e.lineno}: {e.message}")
    # A macro file is imported without the page's context, so anything its
    # macros call must be a template global, not a context-processor value.
    macros = (ROOT / "admin" / "templates" / "admin" / "_macros.html").read_text()
    csrf = (ROOT / "admin" / "csrf.py").read_text()
    routes = (ROOT / "admin" / "routes.py").read_text()
    if "csrf_token()" in macros and 'add_app_template_global(token, "csrf_token")' not in csrf:
        failed.append("_macros.html calls csrf_token(), which isn't a template global")
    else:
        passed += 1
    if "pending_changes(" in macros and "@admin_bp.app_template_global()\ndef pending_changes" not in routes:
        failed.append("_macros.html calls pending_changes(), which isn't a template global")
    else:
        passed += 1
    for f in failed:
        print(f"  [FAIL] {f}")
    print(f"\n{passed} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

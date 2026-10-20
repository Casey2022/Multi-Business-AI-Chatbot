#!/usr/bin/env python3
"""headers_test.py — every response tells the browser our rules.

Run it:  python3 headers_test.py

admin/headers.py adds a Content-Security-Policy (scripts only from /static,
or inline with this response's nonce), refuses framing, and sends the
smaller headers (nosniff, Referrer-Policy, HSTS). Before 2026-10-20 none
were sent.

Two halves:
  * served responses, from a real app on a throwaway database: public pages,
    the portal, JSON, a 404. Each carries the headers, and every inline
    <script> on the page carries the nonce in the header (a new one each
    response).
  * the templates, read as files: nothing the policy would block, so a page
    doesn't quietly lose a button. No onsubmit="" or other inline handlers
    (one put a menu item's name inside JavaScript, where escaping doesn't
    help), no style="", no javascript: links, no inline <script> without
    the nonce.

No network: fake keys, no vector build.
"""

import logging
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.update(SECRET_KEY="h" * 64, COOKIE_SECURE="false",
                  ANTHROPIC_API_KEY="fake", VOYAGE_API_KEY="fake")
os.environ.pop("ADMIN_EMAIL", None)

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="headers_test_")) / "test.db"
import vector_boot
vector_boot.run_in_subprocess = lambda *a, **k: None

logging.disable(logging.CRITICAL)
import app as app_module
app = app_module.app

PASSED, FAILED = [], []
TEMPLATES = [p for d in ("templates", "admin/templates") for p in (ROOT / d).rglob("*.html")]


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n" + "-" * len(text))


def nonce_in(policy):
    found = re.search(r"'nonce-([^']+)'", policy or "")
    return found.group(1) if found else None


def served():
    heading("every kind of response carries the headers")
    client = app.test_client()
    page = client.get("/demo").get_data(as_text=True)
    token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    owner = app.test_client()
    owner.get("/demo")
    with owner.session_transaction() as s:
        s["csrf_token"] = token
    owner.post("/demo/start", data={"template": "sunrise_bakery_and_cafe",
                                    "_csrf_token": token})
    with owner.session_transaction() as s:
        business_id = db.get_user_by_email(s["user_email"])["business_id"]

    slug = "sunrise_bakery_and_cafe"
    responses = {
        "the demo picker": client.get("/demo"),
        "the chat page": client.get(f"/chat/{slug}"),
        "the text simulator": client.get(f"/chat/{slug}/text"),
        "the login page": client.get("/admin/login"),
        "a portal page": owner.get(f"/admin/business/{business_id}/calendar"),
        "JSON": client.get(f"/webchat/{slug}/history?session_id=x"),
        "a 404": client.get("/no-such-page"),
    }
    for what, response in responses.items():
        policy = response.headers.get("Content-Security-Policy", "")
        problems = [h for h, want in (
            ("frame-ancestors", "frame-ancestors 'none'" in policy),
            ("script nonce", nonce_in(policy) is not None),
            ("no unsafe-inline", "unsafe-inline" not in policy),
            ("X-Frame-Options", response.headers.get("X-Frame-Options") == "DENY"),
            ("nosniff", response.headers.get("X-Content-Type-Options") == "nosniff"),
            ("Referrer-Policy", bool(response.headers.get("Referrer-Policy"))),
            ("HSTS", "max-age=" in response.headers.get("Strict-Transport-Security", "")),
        ) if not want]
        check(f"{what} ({response.status_code})", not problems, f"missing: {problems}")

    heading("inline scripts carry this response's nonce")
    for what in ("the chat page", "the text simulator"):
        response = responses[what]
        html = response.get_data(as_text=True)
        nonce = nonce_in(response.headers["Content-Security-Policy"])
        inline = re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>", html)
        check(f"{what}: every inline script has the header's nonce",
              inline and all(f'nonce="{nonce}"' in attrs for attrs in inline), inline)
    again = client.get(f"/chat/{slug}").headers["Content-Security-Policy"]
    check("a new nonce for each response",
          nonce_in(again) != nonce_in(responses["the chat page"].headers["Content-Security-Policy"]))
    html = responses["a portal page"].get_data(as_text=True)
    check("the portal loads confirm-submit.js", "confirm-submit.js" in html)


def templates():
    heading("templates: nothing the policy would block")
    for name, pattern in (
            ("inline event handlers (onsubmit=\"…\")", r"\son[a-z]+\s*=\s*[\"']"),
            ("style=\"…\" attributes", r"\sstyle\s*=\s*[\"']"),
            ("javascript: links", r"javascript:"),
            ("inline <script> without the nonce",
             r"<script(?![^>]*\b(?:src|nonce)=)[^>]*>")):
        hits = [f"{p.relative_to(ROOT)}:{n}" for p in TEMPLATES
                for n, line in enumerate(p.read_text().splitlines(), 1)
                if re.search(pattern, line, re.I)]
        check(f"none: {name}", not hits, ", ".join(hits[:5]))
    check("the templates were found (not an empty loop)", len(TEMPLATES) >= 15, len(TEMPLATES))


def main():
    served()
    templates()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

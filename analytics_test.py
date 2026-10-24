#!/usr/bin/env python3
"""analytics_test.py — the visitor count is on the demo sandbox, and only there.

Run it:  python3 analytics_test.py

Cloudflare Web Analytics (analytics.py, 2026-10-24): a beacon script that
reports page views, switched on by CF_WEB_ANALYTICS_TOKEN. Held here:
without the token, nothing on any page and nothing outside in the security
policy; with it, the beacon on the demo picker, a demo's chat and text
pages and a demo visitor's portal, and never on a real business's chat, a
real owner's or the operator's portal, or a signed-out portal page (the
reset page's address carries its one-time token).
"""

import html
import json
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
os.environ.pop("CF_WEB_ANALYTICS_TOKEN", None)

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="analytics_test_")) / "test.db"
import vector_boot
vector_boot.run_in_subprocess = lambda *a, **k: None

PASSED, FAILED = [], []
TOKEN = "0123456789abcdef0123456789abcdef"
BEACON = "static.cloudflareinsights.com/beacon.min.js"


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def token_of(client, path):
    page = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)


def beacon_token(page):
    """The token in the page's beacon, or None when there's no beacon."""
    found = re.search(r"<script[^>]*beacon\.min\.js[^>]*data-cf-beacon='([^']*)'", page)
    return json.loads(html.unescape(found.group(1)))["token"] if found else None


def main():
    import app as app_module
    import ratelimit
    ratelimit.DEMO_PER_MINUTE = ratelimit.DEMO_PER_HOUR = 1000
    app = app_module.app
    db.init_db()
    biz = db.get_business_by_slug("bobs_plumbing")
    db.create_user("operator@example.test", "operator long passphrase", is_operator=True)
    db.create_user("owner@example.test", "owner long passphrase", business_id=biz["id"])
    reset = db.create_password_reset(db.get_user_by_email("owner@example.test")["id"])

    def signed_in(email, password):
        client = app.test_client()
        client.post("/admin/login", data={"_csrf_token": token_of(client, "/admin/login"),
                                          "email": email, "password": password})
        return client

    visitor = app.test_client()
    r = visitor.post("/demo/start", data={"_csrf_token": token_of(visitor, "/demo"),
                                          "template": "bobs_plumbing"})
    demo_slug = r.headers["Location"].rsplit("/", 1)[-1]
    demo = db.get_business_by_slug(demo_slug)
    operator = signed_in("operator@example.test", "operator long passphrase")
    owner = signed_in("owner@example.test", "owner long passphrase")
    anyone = app.test_client()

    counted = [("the demo picker", anyone, "/demo"),
               ("a demo's chat page", visitor, f"/chat/{demo_slug}"),
               ("a demo's text page", visitor, f"/chat/{demo_slug}/text"),
               ("a demo visitor's portal", visitor, f"/admin/business/{demo['id']}/settings")]
    never = [("a real business's customer chat", anyone, "/chat/bobs_plumbing"),
             ("a real business's text page", anyone, "/chat/bobs_plumbing/text"),
             ("the operator's portal", operator, "/admin/"),
             ("a real owner's portal", owner, f"/admin/business/{biz['id']}/settings"),
             ("the sign-in page", anyone, "/admin/login"),
             ("the forgot-password page", anyone, "/admin/forgot"),
             ("the reset page (its address is the token)", anyone, f"/admin/reset/{reset}")]

    heading("without a token: off everywhere")
    for name, client, path in counted + never:
        r = client.get(path)
        check(f"{name}: no beacon", r.status_code == 200 and BEACON not in r.get_data(as_text=True),
              r.status_code)
    policy = anyone.get("/demo").headers["Content-Security-Policy"]
    check("the security policy names nothing outside", "cloudflareinsights" not in policy, policy)

    heading("with a token: the sandbox is counted")
    os.environ["CF_WEB_ANALYTICS_TOKEN"] = TOKEN
    for name, client, path in counted:
        r = client.get(path)
        check(f"{name}: the beacon, with the token", r.status_code == 200
              and beacon_token(r.get_data(as_text=True)) == TOKEN, r.status_code)
    r = anyone.get("/demo")
    policy = r.headers["Content-Security-Policy"]
    check("the policy lets the script load, and only from Cloudflare's script host",
          "script-src 'self' 'nonce-" in policy
          and "https://static.cloudflareinsights.com" in policy.split("script-src")[1].split(";")[0],
          policy)
    check("…and lets it report to Cloudflare",
          "https://cloudflareinsights.com" in policy.split("connect-src")[1].split(";")[0], policy)
    nonce = re.search(r"'nonce-([^']+)'", policy).group(1)
    check("the beacon tag carries this page's nonce",
          re.search(r'<script[^>]*beacon\.min\.js[^>]*nonce="' + re.escape(nonce) + '"',
                    r.get_data(as_text=True)) is not None)

    heading("with a token: never these")
    for name, client, path in never:
        r = client.get(path)
        check(f"{name}: no beacon", r.status_code == 200 and BEACON not in r.get_data(as_text=True),
              r.status_code)

    heading("a token that isn't Cloudflare's shape is ignored")
    for bad in ("x' onerror='alert(1)", TOKEN.upper(), TOKEN[:-1], ""):
        os.environ["CF_WEB_ANALYTICS_TOKEN"] = bad
        r = anyone.get("/demo")
        check(f"{bad[:20]!r}: off", BEACON not in r.get_data(as_text=True)
              and "cloudflareinsights" not in r.headers["Content-Security-Policy"])
    os.environ.pop("CF_WEB_ANALYTICS_TOKEN", None)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

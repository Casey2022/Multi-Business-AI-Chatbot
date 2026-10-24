#!/usr/bin/env python3
"""demo_leave_test.py — starting a demo while signed in, and the way back.

Run it:  python3 demo_leave_test.py

Starting a demo while signed in sets that account aside ("displaced_user")
and the nav offers "Back to your account" (/demo/leave). Nothing tested it,
and measured 2026-10-24 (the operator nav showed the link on the live site):
  - a demo swept by a redeploy, then signing in again: the operator's own
    nav still offered "Back to your account";
  - starting a demo, then logging out: /demo/leave signed the next person
    at that browser in as the operator, with no password;
  - an operator who had ever changed their password started a demo: the
    demo was signed out at once (the operator's session version carried
    over), and its portal sent them to sign in.
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
db.DB_PATH = Path(tempfile.mkdtemp(prefix="demo_leave_test_")) / "test.db"
import vector_boot
vector_boot.run_in_subprocess = lambda *a, **k: None

PASSED, FAILED = [], []
OPERATOR = "op@example.test"
PASSWORD = "operator long passphrase"


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def token(client, path):
    page = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)


def sign_in(client, password=PASSWORD):
    client.post("/admin/login", data={"_csrf_token": token(client, "/admin/login"),
                                      "email": OPERATOR, "password": password})


def start_demo(client):
    r = client.post("/demo/start", data={"_csrf_token": token(client, "/demo"),
                                         "template": "bobs_plumbing"})
    slug = r.headers["Location"].rsplit("/", 1)[-1]
    return db.get_business_by_slug(slug)


def dashboard(client):
    """("operator" | "demo" | "signed out", offers the way back?)"""
    r = client.get("/admin/")
    if r.status_code == 302:
        where = r.headers["Location"]
        if "/admin/login" in where:
            return "signed out", False
        r = client.get(where)
    page = r.get_data(as_text=True)
    who = "operator" if "<h1>Businesses</h1>" in page else "demo"
    return who, "Back to your account" in page


def sweep_demos():
    """What a redeploy (or the idle sweep) does to a demo's account."""
    conn = db.get_connection()
    conn.execute("UPDATE users SET active = 0 WHERE email LIKE '%@demo.invalid'")
    conn.commit()
    conn.close()


def main():
    import app as app_module
    import ratelimit
    ratelimit.DEMO_PER_MINUTE = ratelimit.DEMO_PER_HOUR = 1000
    ratelimit.LOGIN_PER_MINUTE = ratelimit.LOGIN_PER_HOUR = 1000
    app = app_module.app
    db.init_db()
    db.create_user(OPERATOR, PASSWORD, is_operator=True)

    heading("the way back, as meant")
    client = app.test_client()
    sign_in(client)
    demo = start_demo(client)
    check("in the demo, its portal opens",
          client.get(f"/admin/business/{demo['id']}/settings").status_code == 200)
    check("…and offers the way back", dashboard(client) == ("demo", True), dashboard(client))
    r = client.get("/demo/leave")
    check("'Back to your account' returns to the operator's dashboard",
          r.status_code == 302 and r.headers["Location"].endswith("/admin/")
          and dashboard(client) == ("operator", False), dashboard(client))
    check("…once: it's gone from the nav after", dashboard(client)[1] is False)

    heading("the demo is swept, then they sign in again (the live site's nav)")
    client = app.test_client()
    sign_in(client)
    start_demo(client)
    sweep_demos()
    check("with the demo gone, they're signed out", dashboard(client)[0] == "signed out")
    sign_in(client)
    check("signing in again: their dashboard, with no 'Back to your account'",
          dashboard(client) == ("operator", False), dashboard(client))

    heading("logging out during a demo")
    client = app.test_client()
    sign_in(client)
    start_demo(client)
    client.get("/admin/logout")
    r = client.get("/demo/leave")
    check("/demo/leave afterwards goes to the demo picker", r.status_code == 302
          and r.headers["Location"].endswith("/demo"), r.headers.get("Location"))
    check("…and signs nobody in", dashboard(client) == ("signed out", False), dashboard(client))

    heading("a password that has changed")
    client = app.test_client()
    sign_in(client)
    db.change_password(OPERATOR, PASSWORD, "operator newer passphrase")
    # Straight to a demo, and without budget.scope_request, which reads the
    # session first for its own reasons and would end it before demo_start
    # sees it: demo_start mustn't depend on that.
    import budget
    hooks = app.before_request_funcs[None]
    at = hooks.index(budget.scope_request)
    hooks.remove(budget.scope_request)
    try:
        demo = start_demo(client)
    finally:
        hooks.insert(at, budget.scope_request)
    check("a demo started from an ended session works",
          client.get(f"/admin/business/{demo['id']}/settings").status_code == 200)
    check("…and sets nothing aside to go back to", dashboard(client) == ("demo", False),
          dashboard(client))

    client = app.test_client()
    sign_in(client, "operator newer passphrase")
    demo = start_demo(client)
    check("an operator who changed their password can use a demo (its portal opens)",
          client.get(f"/admin/business/{demo['id']}/settings").status_code == 200)
    client.get("/demo/leave")
    check("…and gets back to their dashboard", dashboard(client) == ("operator", False),
          dashboard(client))

    client = app.test_client()
    sign_in(client, "operator newer passphrase")
    start_demo(client)
    db.change_password(OPERATOR, "operator newer passphrase", "operator newest passphrase")
    client.get("/demo/leave")
    check("the password changed during the demo: the way back asks them to sign in",
          dashboard(client) == ("signed out", False), dashboard(client))

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

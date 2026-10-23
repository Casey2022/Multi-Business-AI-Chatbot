#!/usr/bin/env python3
"""reset_test.py — password reset by email, and sessions that end.

Run it:  python3 reset_test.py

Before 2026-10-23 an owner who forgot their password had no way back in,
and a session lived until its idle timeout whatever happened to the
password: the cookie held only an email. Now:
  - "Forgot your password?" emails a one-time link (30 minutes, only its
    hash stored), and the page says the same thing whether or not the
    address has an account;
  - setting a new password through it ends every other session (a
    per-user session version, checked on each request);
  - requests are rate-limited, and demo accounts never get mail.
No network: the SMTP class is faked.
"""

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.update(SECRET_KEY="t" * 64, COOKIE_SECURE="false",
                  ANTHROPIC_API_KEY="fake", VOYAGE_API_KEY="fake",
                  SMTP_HOST="smtp.example.test", SMTP_USER="robot@example.test",
                  SMTP_PASSWORD="x")
os.environ.pop("SUPPORT_ALERT_EMAIL", None)       # reset mail mustn't need it

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="reset_test_")) / "test.db"
import vector_boot
vector_boot.run_in_subprocess = lambda *a, **k: None

PASSED, FAILED = [], []
SENT = []


class FakeSMTP:
    def __init__(self, host, port, timeout=None):
        self.host = host
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def starttls(self):
        pass
    def login(self, user, password):
        pass
    def send_message(self, msg):
        SENT.append(msg)


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def token_of(client, path):
    page = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)


def link_in(msg):
    body = msg.get_content()
    m = re.search(r"https?://\S+/admin/reset/(\S+)", body)
    return m.group(0) if m else None, m.group(1) if m else None


def main():
    import app as app_module
    import support
    support.SMTP = FakeSMTP
    app = app_module.app
    db.init_db()
    biz = db.get_business_by_slug("sunrise_bakery_and_cafe")["id"]
    db.create_user("owner@example.test", "correct horse battery", business_id=biz)

    heading("a session ends when the password changes")
    other = app.test_client()
    t = token_of(other, "/admin/login")
    other.post("/admin/login", data={"_csrf_token": t, "email": "owner@example.test",
                                     "password": "correct horse battery"})
    check("signed in elsewhere (another browser)",
          other.get(f"/admin/business/{biz}/settings").status_code == 200)

    heading("asking for a link")
    client = app.test_client()
    page = client.get("/admin/login").get_data(as_text=True)
    check("the sign-in page links to it", "/admin/forgot" in page and "Forgot your password?" in page)
    t = token_of(client, "/admin/forgot")
    r = client.post("/admin/forgot", data={"_csrf_token": t, "email": "Owner@Example.test "})
    known = r.get_data(as_text=True)
    check("a reply that doesn't say whether the account exists",
          r.status_code == 200 and "If an account exists for that address" in known, r.status_code)
    check("one email, to that address, with a link", len(SENT) == 1
          and SENT[0]["To"] == "owner@example.test" and link_in(SENT[0])[0], SENT)
    url, token = link_in(SENT[0])
    r = client.post("/admin/forgot", data={"_csrf_token": t, "email": "nobody@example.test"})
    check("an unknown address: the same words, and no email",
          len(SENT) == 1 and "If an account exists for that address" in r.get_data(as_text=True))
    conn = db.get_connection()
    stored = [dict(row) for row in conn.execute("SELECT * FROM password_resets")]
    conn.close()
    check("only the link's hash is stored, never the link",
          stored and all(token not in str(v) for row in stored for v in row.values()), stored)

    heading("using it")
    page = client.get(f"/admin/reset/{token}")
    check("the link opens a form for a new password",
          page.status_code == 200 and 'name="password"' in page.get_data(as_text=True))
    t = token_of(client, f"/admin/reset/{token}")
    for name, pw, again in (("too short", "short", "short"),
                            ("the two don't match", "a long new passphrase", "something else here"),
                            ("one anybody would try", "password123", "password123")):
        r = client.post(f"/admin/reset/{token}", data={"_csrf_token": t, "password": pw,
                                                         "confirm": again})
        check(f"refused, in words: {name}", r.status_code == 400, r.status_code)
    r = client.post(f"/admin/reset/{token}", data={"_csrf_token": t,
                                                     "password": "a long new passphrase",
                                                     "confirm": "a long new passphrase"})
    check("a good one is saved, and it says to sign in", r.status_code == 302
          and "/admin/login" in r.headers["Location"], r.status_code)
    check("the new password works, the old one doesn't",
          db.verify_password("owner@example.test", "a long new passphrase")
          and not db.verify_password("owner@example.test", "correct horse battery"))
    check("…and the other browser is signed out",
          other.get(f"/admin/business/{biz}/settings").status_code == 302)
    r = client.get(f"/admin/reset/{token}")
    check("the link can't be used twice", "expired or was already used" in r.get_data(as_text=True))

    heading("a link that's too old, or replaced")
    t = token_of(client, "/admin/forgot")
    client.post("/admin/forgot", data={"_csrf_token": t, "email": "owner@example.test"})
    _, first = link_in(SENT[-1])
    client.post("/admin/forgot", data={"_csrf_token": t, "email": "owner@example.test"})
    _, second = link_in(SENT[-1])
    check("asking again cancels the earlier link",
          "expired or was already used" in client.get(f"/admin/reset/{first}").get_data(as_text=True)
          and 'name="password"' in client.get(f"/admin/reset/{second}").get_data(as_text=True))
    conn = db.get_connection()
    conn.execute("UPDATE password_resets SET expires_at = ?",
                 ((datetime.utcnow() - timedelta(minutes=1)).isoformat(),))
    conn.commit(); conn.close()
    check("after 30 minutes it's expired",
          "expired or was already used" in client.get(f"/admin/reset/{second}").get_data(as_text=True))
    check("a made-up link is the same as an expired one",
          "expired or was already used" in client.get("/admin/reset/not-a-real-token").get_data(as_text=True))

    heading("limits, demos, and the forms")
    db.create_user("demo-x@demo.invalid", "a long demo passphrase", business_id=biz)
    before = len(SENT)
    t = token_of(client, "/admin/forgot")
    client.post("/admin/forgot", data={"_csrf_token": t, "email": "demo-x@demo.invalid"})
    check("a demo account never gets mail", len(SENT) == before)
    import ratelimit
    hits = [client.post("/admin/forgot", data={"_csrf_token": t, "email": "owner@example.test"})
            for _ in range(8)]
    check("asking over and over is slowed down (429)", any(h.status_code == 429 for h in hits))
    check("…and doesn't send a flood", len(SENT) - before <= ratelimit.RESET_PER_HOUR)
    r = app.test_client().post("/admin/forgot", data={"email": "owner@example.test"})
    check("no CSRF token: refused", r.status_code in (400, 403), r.status_code)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

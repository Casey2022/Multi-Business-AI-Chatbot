#!/usr/bin/env python3
"""links_test.py — links in emails are https on Render.

Run it:  python3 links_test.py

Measured 2026-10-24, a request reaching the app the way Render hands it
over (plain http from its balancer; the visitor's https only in
X-Forwarded-Proto): the reset email's link was
http://simonai-q92o.onrender.com/admin/reset/<token>, and the support
alert's http://.../admin/support. Live, Render answers the http link with
a 301 to https, after the token has crossed the network unencrypted.
links.public_url makes them https, except on this machine (localhost).
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
                  ANTHROPIC_API_KEY="fake", VOYAGE_API_KEY="fake",
                  SMTP_HOST="smtp.example.test", SMTP_USER="robot@example.test",
                  SMTP_PASSWORD="x", SUPPORT_ALERT_EMAIL="op@example.test")

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="links_test_")) / "test.db"
import vector_boot
vector_boot.run_in_subprocess = lambda *a, **k: None

PASSED, FAILED = [], []
SENT, ALERTS = [], []
RENDER = dict(base_url="http://simonai-q92o.onrender.com",
              headers={"X-Forwarded-Proto": "https",
                       "X-Forwarded-For": "50.48.130.141, 162.159.114.27, 10.24.90.146"})
LOCAL = dict(base_url="http://127.0.0.1:5001")


class FakeSMTP:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg):
        SENT.append(msg)


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def token(client, path, where):
    page = client.get(path, **where).get_data(as_text=True)
    return re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)


def reset_link(app, where):
    client = app.test_client()
    client.post("/admin/forgot", data={"_csrf_token": token(client, "/admin/forgot", where),
                                       "email": "owner@example.test"}, **where)
    found = re.search(r"\S+/admin/reset/\S+", SENT[-1].get_content())
    return found.group(0) if found else ""


def alert_link(app, where):
    client = app.test_client()
    client.post("/admin/login", data={"_csrf_token": token(client, "/admin/login", where),
                                      "email": "owner@example.test",
                                      "password": "owner long passphrase"}, **where)
    client.post("/admin/help", data={"_csrf_token": token(client, "/admin/help", where),
                                     "message": "The calendar page won't load for me today."},
                **where)
    return ALERTS[-1] if ALERTS else ""


def main():
    import app as app_module
    import links
    import ratelimit
    import support
    support.SMTP = FakeSMTP
    # The alert goes out on a thread; record the link it was given instead.
    support.alert_operator = lambda request_id, inbox_url=None: ALERTS.append(inbox_url)
    ratelimit.RESET_PER_HOUR = ratelimit.RESET_IP_PER_HOUR = 1000
    app = app_module.app
    db.init_db()
    biz = db.get_business_by_slug("bobs_plumbing")
    db.create_user("owner@example.test", "owner long passphrase", business_id=biz["id"])

    heading("a request the way Render hands it over")
    link = reset_link(app, RENDER)
    check("the reset email's link is https", link.startswith(
        "https://simonai-q92o.onrender.com/admin/reset/"), link[:60])
    link = alert_link(app, RENDER)
    check("the support alert's link is https",
          link == "https://simonai-q92o.onrender.com/admin/support", link)

    heading("on this machine")
    link = reset_link(app, LOCAL)
    check("the reset link stays http (no certificate here)",
          link.startswith("http://127.0.0.1:5001/admin/reset/"), link[:60])

    heading("what counts as this machine")
    for host, local in (("localhost", True), ("localhost:5001", True),
                        ("127.0.0.1:5001", True), ("[::1]:5001", True),
                        ("app.localhost:5001", True),
                        ("simonai-q92o.onrender.com", False),
                        ("localhost.evil.example", False),
                        ("127.0.0.1.evil.example", False), ("", False)):
        check(f"{host or '(none)'}: {'local' if local else 'not local'}",
              links.is_local(host) is local)

    heading("every emailed link goes through it")
    for path in ("admin/auth.py", "admin/support_routes.py"):
        source = (ROOT / path).read_text()
        check(f"{path}: no url_for(..., _external=True)", "_external=True" not in source)
    check("both use public_url", "public_url(\"admin.reset_password_page\"" in
          (ROOT / "admin/auth.py").read_text() and "public_url(\"admin.support_inbox\")" in
          (ROOT / "admin/support_routes.py").read_text())

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

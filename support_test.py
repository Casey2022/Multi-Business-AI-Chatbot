#!/usr/bin/env python3
"""support_test.py — the Help page's back end (support.py, db support_requests).

Run it:  python3 support_test.py

What has to hold (2026-10-12):
- the context the app attaches is right, and only what the Help page shows:
  business, page, error reference, version; never customer data;
- the page the owner came from is untrusted (it's in the query string), so
  only a bare path on this site survives, which also makes the "Back to
  where you were" link safe;
- the operator's email alert can fail without the request being lost, and
  a reply goes to a real address (never a demo's made-up login);
- a demo visitor's report outlives their sandbox; resolved requests are
  pruned, open ones never;
- the log says a request was filed, not what it said or who sent it.
No network: the mail server is a fake.
"""

import io
import logging
import os
import sys
import tempfile
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="support_test_")) / "test.db"

ROOT = Path(__file__).resolve().parent
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


class FakeSMTP:
    sent, calls, fail = [], [], None

    def __init__(self, host, port, timeout=None):
        if FakeSMTP.fail:
            raise FakeSMTP.fail
        FakeSMTP.calls.append(("connect", host, port))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        FakeSMTP.calls.append(("starttls",))

    def login(self, user, password):
        FakeSMTP.calls.append(("login", user))

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def main():
    db.init_db()
    import support
    import ratelimit

    heading("what survives from the query string")
    check("a page path is kept, its query dropped",
          support.clean_page("/admin/business/3/menu?x=1#top") == "/admin/business/3/menu")
    for bad in ("//evil.com/x", "https://evil.com", "javascript:alert(1)", "/admin/../x",
                "/admin/<script>", "admin/no-slash", "/" + "a" * 300, None, ""):
        check(f"{bad!r:.40} is refused", support.clean_page(bad) is None)
    check("an error reference is 8 hex characters",
          support.clean_error_id("A3F9C2D1") == "a3f9c2d1"
          and support.clean_error_id("a3f9") is None
          and support.clean_error_id("zzzzzzzz") is None)
    check("new error references are well formed",
          all(support.clean_error_id(support.new_error_id()) for _ in range(20)))

    heading("the form")
    m, c, e = support.check_form("  It broke\r\nagain  ", "x@y.com", needs_contact_field=False)
    check("message trimmed, line endings normalised; contact ignored for an owner",
          m == "It broke\nagain" and c is None and e == [], (m, c, e))
    check("an empty message is refused", support.check_form("   ", "", True)[2])
    check("a very long message is refused",
          support.check_form("x" * (support.MAX_MESSAGE + 1), "", True)[2])
    check("a demo visitor's email is kept when it looks right",
          support.check_form("help", "me@example.com", True)[1] == "me@example.com")
    check("…and refused when it doesn't",
          support.check_form("help", "not an email", True)[2])
    check("an address with a line break can't become an email header",
          not support.looks_like_email("me@example.com\nBcc: everyone@example.com"))

    heading("filing a request (no email set up)")
    for k in ("SUPPORT_ALERT_EMAIL", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"):
        os.environ.pop(k, None)
    bid = db.add_business("Crosstown Pizza", "crosstown_pizza", "config/crosstown_pizza.yaml")
    biz = db.get_business_by_id(bid)
    owner = {"email": "owner@crosstown.example"}
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logging.getLogger("support").addHandler(handler)
    logging.getLogger("support").setLevel(logging.INFO)
    rid = support.file_request(owner, biz, "The menu page won't save my wings", None,
                               "/admin/business/1/menu", "a3f9c2d1", background=False)
    row = db.get_support_request(rid)
    check("filed with the business, page, error reference and version",
          row["business_name"] == "Crosstown Pizza" and row["context"]["page"] == "/admin/business/1/menu"
          and row["context"]["error_id"] == "a3f9c2d1" and row["context"]["version"]
          and row["status"] == "open", row)
    check("the context is exactly what the Help page shows (no customer data)",
          set(row["context"]) == {"business", "business_id", "page", "error_id", "version"})
    check("with no email set up, the request still lands and says so",
          row["alert"] == "email not set up", row["alert"])
    logged = stream.getvalue()
    check("the log says it was filed, not what it said or who sent it",
          f"#{rid}" in logged and "wings" not in logged and "owner@" not in logged, logged)
    check("a reply goes to the owner's login email",
          support.reply_address(row) == "owner@crosstown.example")

    heading("the email alert")
    os.environ.update(SUPPORT_ALERT_EMAIL="casey@example.com", SMTP_HOST="smtp.example.com",
                      SMTP_USER="alerts@example.com", SMTP_PASSWORD="app-password")
    support.SMTP = FakeSMTP
    status = support.alert_operator(rid, inbox_url="https://site/admin/support")
    msg = FakeSMTP.sent[-1] if FakeSMTP.sent else None
    check("sent over TLS, logged in, recorded as emailed",
          status == "emailed" and ("starttls",) in FakeSMTP.calls
          and ("login", "alerts@example.com") in FakeSMTP.calls
          and db.get_support_request(rid)["alert"] == "emailed", (status, FakeSMTP.calls))
    body = msg.get_content() if msg else ""
    check("the email has the message, page, error ref, version and inbox link",
          all(x in body for x in ("won't save my wings", "/admin/business/1/menu", "a3f9c2d1",
                                  "https://site/admin/support")), body)
    check("Reply-To is the owner, so answering is one click",
          msg and msg["Reply-To"] == "owner@crosstown.example" and msg["To"] == "casey@example.com")

    demo_id = db.add_business("Crosstown (demo)", "crosstown-demo-1", "config/crosstown_pizza.yaml",
                              is_demo=True, demo_of=bid)
    demo = db.get_business_by_id(demo_id)
    visitor = {"email": "crosstown-demo-1@demo.invalid"}
    d1 = support.file_request(visitor, demo, "Love it. How do I add a topping?", "me@example.com",
                              "/admin/business/2/menu", None, background=False)
    m = FakeSMTP.sent[-1]
    check("a demo visitor's reply goes to the address they left",
          m["Reply-To"] == "me@example.com" and db.get_support_request(d1)["contact"] == "me@example.com")
    d2 = support.file_request(visitor, demo, "Just saying hi", None, None, None, background=False)
    check("…and with none left, there's no Reply-To (never the made-up login)",
          FakeSMTP.sent[-1]["Reply-To"] is None and support.reply_address(db.get_support_request(d2)) is None)
    o2 = support.file_request(owner, biz, "x", "sneaky@example.com", None, None, background=False)
    check("an owner can't set a different reply address",
          db.get_support_request(o2)["contact"] is None)

    FakeSMTP.fail = ConnectionRefusedError("nope")
    bad = support.file_request(owner, biz, "Second problem", None, None, None, background=False)
    check("a mail server that's down: request kept, failure recorded, nothing raised",
          db.get_support_request(bad)["alert"] == "email failed: ConnectionRefusedError")
    FakeSMTP.fail = None
    os.environ["SMTP_HOST"] = ""
    check("SMTP_HOST unset means alerts are off", support.email_settings() is None)

    heading("the inbox")
    check("an owner sees only their own requests",
          {r["id"] for r in db.list_support_requests(user_email="owner@crosstown.example")}
          == {rid, o2, bad})
    before = db.count_open_support_requests()
    db.resolve_support_request(rid, "casey@example.com", reply="  Fixed in the next version.  ")
    r = db.get_support_request(rid)
    check("resolving records who, when and the note (trimmed)",
          r["status"] == "resolved" and r["resolved_by"] == "casey@example.com"
          and r["reply"] == "Fixed in the next version." and r["resolved_at"])
    check("…and the open count drops", db.count_open_support_requests() == before - 1)
    check("open requests are listed before resolved ones",
          db.list_support_requests()[-1]["id"] == rid)
    db.reopen_support_request(rid)
    check("reopening puts it back", db.get_support_request(rid)["status"] == "open")
    db.resolve_support_request(rid, "casey@example.com")

    heading("what's kept")
    db.delete_business(demo_id)
    kept = db.get_support_request(d1)
    check("a demo visitor's report outlives their sandbox, under the name it was filed with",
          kept and kept["business_id"] is None and kept["business_name"] == "Crosstown (demo)")
    conn = db.get_connection()
    conn.execute("UPDATE support_requests SET resolved_at = '2020-01-01T00:00:00' WHERE id = ?", (rid,))
    conn.execute("UPDATE support_requests SET created_at = '2020-01-01T00:00:00' WHERE id = ?", (bad,))
    conn.commit(); conn.close()
    removed = db.prune_personal_data()
    check("an old resolved request is pruned",
          db.get_support_request(rid) is None and removed.get("support_requests") == 1, removed)
    check("an old open one is not: someone still owes a reply", db.get_support_request(bad) is not None)

    heading("the rate limit")
    results = [ratelimit.support("flood@example.com", "203.0.113.9")[0] for _ in range(6)]
    check("five an hour per person, then refused", results == [True] * 5 + [False], results)
    results = [ratelimit.support(f"demo{i}@demo.invalid", "198.51.100.7")[0] for i in range(11)]
    check("ten an hour per address, however many demo logins", results[:10] == [True] * 10
          and results[10] is False, results)

    heading("the routes")
    routes = (ROOT / "admin" / "support_routes.py").read_text()
    base = (ROOT / "admin" / "templates" / "admin" / "base.html").read_text()
    check("the Help page needs a login; the inbox and its buttons need an operator",
          '@admin_bp.route("/help", methods=["GET", "POST"])\n@login_required' in routes
          and routes.count("@operator_required") == 3)
    check("the support routes are registered",
          "support_routes" in (ROOT / "admin" / "__init__.py").read_text())
    check("the error page writes its reference beside the traceback",
          'log.error("Error %s on %s %s", error_id' in routes and "exc_info=" in routes)
    check("the nav offers Help to owners and the inbox to operators",
          "admin.help_page" in base and "admin.support_inbox" in base and "open_support_count()" in base)

    heading("the demo feedback round (2026-10-19)")
    settings = {"to": "ops@example.com", "from": "ops@example.com"}
    demo_row = {"id": 12, "is_demo": True, "business_name": "Belmont Hair Studio",
                "message": "The colour answer confused me.", "context": {}, "contact": None}
    owner_row = {"id": 13, "is_demo": False, "business_name": "Belmont Hair Studio",
                 "user_email": "owner@belmont.example", "message": "x", "context": {}}
    check("a demo visitor's message is emailed as feedback, with its own subject",
          support.alert_email(demo_row, settings)["Subject"] == "Demo feedback #12 (Belmont Hair Studio)")
    check("an owner's request keeps its subject",
          support.alert_email(owner_row, settings)["Subject"].startswith("Help request #13"))
    chat = (ROOT / "templates" / "demo.html").read_text()
    check("the demo chat page links to it, inside the demo-only part of the nav",
          "Send feedback</a>" in chat
          and chat.index("{% if is_demo %}") < chat.index("Send feedback</a>") < chat.index("{% endif %}", chat.index("{% if is_demo %}"))
          and "'/chat/' ~ business.slug" in chat and "#support" in chat)
    check("…and its 'from' page is one the Help page accepts", support.clean_page("/chat/demo-belmont-0fe37a") == "/chat/demo-belmont-0fe37a")
    picker = (ROOT / "templates" / "demo_picker.html").read_text()
    help_page = (ROOT / "admin" / "templates" / "admin" / "help.html").read_text()
    check("the picker invites feedback; the portal nav says 'Help & feedback' to demo visitors only",
          "Send feedback" in picker and "'Help & feedback' if business is defined and business and business.is_demo" in base)
    check("the Help page asks a demo visitor for feedback, and an owner as before",
          "'Send feedback, or ask a person' if needs_contact else 'Ask a person'" in help_page
          and "\"What worked, what didn't, what was confusing?\" if needs_contact" in help_page)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

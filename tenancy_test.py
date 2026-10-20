#!/usr/bin/env python3
"""tenancy_test.py — one demo visitor can't reach another's business.

Run it:  python3 tenancy_test.py

Two visitors start demos the way the public site does (/demo, then
/demo/start), each in their own browser session. Visitor B's business gets
one of everything: an appointment, a knowledge section, a calendar block
with a private note, a customer message, a support request. Then visitor A
tries every admin route with B's ids, every method it accepts, with a valid
CSRF token and a form full of plausible fields, so a missing check would
actually write. Each attempt must be refused, show none of B's text, and
leave every one of B's rows exactly as it was.

The routes are read from Flask's url_map, not listed by hand: a route added
next month is swept without anyone remembering to add it. A route that takes
an id this file doesn't know fails, so the sweep can't quietly skip it.

The ids are also tried mixed: A's own business with B's block, document or
appointment (/business/<A>/calendar/block/<B's block>/remove). Passing the
business check with your own id must not reach somebody else's row.

No network: the model and embedding keys are fake, the vector build is
skipped, the database is a throwaway file.
"""

import logging
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
                  ANTHROPIC_API_KEY="fake", VOYAGE_API_KEY="fake")
os.environ.pop("ADMIN_EMAIL", None)

# Point the database at a temp file BEFORE anything reads db.DB_PATH.
import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="tenancy_test_")) / "test.db"
import vector_boot
vector_boot.run_in_subprocess = lambda *a, **k: None

logging.disable(logging.CRITICAL)
import app as app_module
app = app_module.app

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n" + "-" * len(text))


def visitor(template):
    """A browser that opened /demo and started a demo. (client, token, user)."""
    client = app.test_client()
    page = client.get("/demo").get_data(as_text=True)
    token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    client.post("/demo/start", data={"template": template, "_csrf_token": token})
    with client.session_transaction() as s:
        email = s.get("user_email")
    return client, token, db.get_user_by_email(email)


def snapshot(business_id):
    """Every row that belongs to one business, as plain tuples."""
    conn = db.get_connection()
    rows = {}
    for table, column in db._TENANT_TABLES + (("documents", "business_id"),
                                              ("support_requests", "business_id")):
        rows[table] = [tuple(r) for r in conn.execute(
            f"SELECT * FROM {table} WHERE {column} = ? ORDER BY rowid", (business_id,))]
    rows["document_versions"] = [tuple(r) for r in conn.execute(
        "SELECT v.* FROM document_versions v JOIN documents d ON d.id = v.document_id "
        "WHERE d.business_id = ? ORDER BY v.rowid", (business_id,))]
    conn.close()
    return rows


def main():
    heading("two visitors, two businesses")
    a_client, a_token, a_user = visitor("sunrise_bakery_and_cafe")
    b_client, _, b_user = visitor("sunrise_bakery_and_cafe")
    A, B = a_user["business_id"], b_user["business_id"]
    check("each visitor is signed in as the owner of their own demo",
          A and B and A != B, (A, B))

    db.save_appointment("web-b", "custom cake", "2026-12-01T10:00", B,
                        customer_name="Zanzibar Customer")
    conn = db.get_connection()
    appointment = conn.execute("SELECT id FROM appointments WHERE business_id = ?",
                               (B,)).fetchone()["id"]
    conn.close()
    document = db.add_document_section(B, "Zanzibar section", "Zanzibar body.")
    # In the app's own format, and inside the weeks the calendar page shows.
    day = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    block = db.add_time_block(B, f"{day} 09:00", f"{day} 17:00", note="Zanzibar dentist")
    request_id = db.add_support_request(
        business_id=B, business_name="B", user_email=b_user["email"], contact="b@example.com",
        is_demo=1, message="Zanzibar help", context="{}")
    db.save_message("web-b", "user", "Zanzibar message", B, source="webchat")
    # One plain word in all of B's text: HTML escaping can't hide it (an
    # apostrophe comes out as &#39; and a pattern with one would miss it).
    secret = re.compile(r"Zanzibar")

    ids = {"business_id": B, "appointment_id": appointment, "document_id": document,
           "block_id": block, "index": 0, "request_id": request_id}
    form = {"_csrf_token": a_token, "title": "A was here", "body": "A was here",
            "content": "A was here", "name": "A was here", "price": "1", "note": "A",
            "date": "2026-12-05", "time": "10:00", "starts_at": "2026-12-05T10:00",
            "ends_at": "2026-12-05T11:00", "when": "2026-12-05T10:00",
            "url": "http://127.0.0.1/", "question": "hi", "message": "hi",
            "confirm": "1", "action": "save"}
    # Pages with no id that only the operator may see.
    operator_only = {"admin.support_inbox"}

    heading("visitor A, with B's ids, on every admin route")
    original = snapshot(B)
    swept = 0
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        if not rule.endpoint.startswith("admin."):
            continue
        unknown = set(rule.arguments) - set(ids)
        if unknown:
            check(f"{rule.rule}: the sweep knows its ids", False,
                  f"add {sorted(unknown)} to ids in tenancy_test.py")
            continue
        if not rule.arguments and rule.endpoint not in operator_only:
            continue
        mixes = [False] + ([True] if "business_id" in rule.arguments
                           and len(rule.arguments) > 1 else [])
        for own in mixes:
            url = rule.rule
            for name in rule.arguments:
                value = A if (own and name == "business_id") else ids[name]
                url = url.replace(f"<int:{name}>", str(value))
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                before = snapshot(B)
                response = a_client.open(url, method=method,
                                         data=form if method == "POST" else None)
                body = response.get_data(as_text=True)
                problems = []
                if snapshot(B) != before:
                    problems.append("changed B's rows")
                if secret.search(body):
                    problems.append("showed B's text")
                # With B's business id the answer is a refusal. With A's own
                # (mixed), A may get a redirect back to their own page; what
                # matters is that B's row wasn't touched or shown.
                if not own and response.status_code not in (401, 403, 404):
                    problems.append(f"answered {response.status_code}")
                swept += 1
                check(f"{method} {url}{' (A own business, B item)' if own else ''}",
                      not problems, "; ".join(problems))
    check("the sweep covered the admin routes (not an empty loop)", swept >= 30, swept)
    check("B's business is exactly as it was", snapshot(B) == original)

    heading("and B still has their own business")
    response = b_client.get(f"/admin/business/{B}/calendar")
    check("B's own calendar opens for B, with the private note",
          response.status_code == 200 and "Zanzibar dentist" in response.get_data(as_text=True),
          response.status_code)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

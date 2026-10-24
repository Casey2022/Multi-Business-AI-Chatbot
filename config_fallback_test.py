#!/usr/bin/env python3
"""config_fallback_test.py — a config that won't load: customers are asked
to call, not shown a server error.

Run it:  python3 config_fallback_test.py

Measured 2026-10-23 with Bob's config broken so it can't load: the chat
page was a bare "500 Internal Server Error", every message a 500 that the
chat showed as "couldn't reach the assistant, try again?", the chat's
update poll a 500 every few seconds, the text page a 500, and a real text
crashed, so Twilio sent the customer nothing. With a config that loads but
names a time zone that isn't one, the update poll was a 500 every time.
config_check.py says what's wrong on the operator's Businesses page; this
is what the customer gets meanwhile.
"""

import logging
import os
import re
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.update(SECRET_KEY="t" * 64, COOKIE_SECURE="false",
                  ANTHROPIC_API_KEY="fake", VOYAGE_API_KEY="fake")

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="config_fallback_test_")) / "test.db"
import vector_boot
vector_boot.run_in_subprocess = lambda *a, **k: None

PASSED, FAILED = [], []
WORK = Path(tempfile.mkdtemp(prefix="config_fallback_files_"))
SESSION = "web_" + "q" * 24
ASKED = "is anyone free on thursday for my leaky sink"


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


class Kept(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def use(business_id, data=None, text=None, name="config"):
    path = WORK / f"{name}.yaml"
    path.write_text(text if text is not None else
                    yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    conn = db.get_connection()
    conn.execute("UPDATE businesses SET config_path = ? WHERE id = ?", (str(path), business_id))
    conn.commit()
    conn.close()
    return path


def main():
    import app as app_module
    from llm import offline_reply
    app = app_module.app
    db.init_db()
    biz = db.get_business_by_slug("bobs_plumbing")
    seed = yaml.safe_load((ROOT / "config" / "bobs_plumbing.yaml").read_text())
    kept = Kept()
    logging.getLogger().addHandler(kept)

    heading("a config that won't load")
    wont_load = dict(seed, bot="friendly")     # nothing checks for it; loading fails
    use(biz["id"], wont_load, name="wont_load")
    call_us = offline_reply("(585) 555-0123")
    check("the reply asks them to call, with the number",
          call_us == "Sorry, I can't answer messages right now. Please call us at (585) 555-0123.",
          call_us)
    client = app.test_client()
    page = client.get("/chat/bobs_plumbing")
    body = page.get_data(as_text=True)
    check("the chat page opens", page.status_code == 200, page.status_code)
    check("…and says it straight away, instead of 'how can I help?'",
          "Please call us at (585) 555-0123" in body and "How can I help?" not in body)
    r = client.post("/webchat/bobs_plumbing", json={"message": ASKED, "session_id": SESSION})
    check("a message gets the reply, not a 500", r.status_code == 200
          and r.get_json() == {"reply": call_us}, (r.status_code, r.get_data(as_text=True)[:120]))
    r = client.get(f"/webchat/bobs_plumbing/updates?session_id={SESSION}")
    check("the chat's update poll: no news, not a 500", r.status_code == 200
          and r.get_json() == {"messages": []}, r.status_code)
    page = client.get("/chat/bobs_plumbing/text")
    body = page.get_data(as_text=True)
    check("the text page opens, and says it", page.status_code == 200
          and "Please call us at (585) 555-0123" in body, page.status_code)
    token = re.search(r'const token = "([^"]+)"', body).group(1)
    r = client.post("/textsim/bobs_plumbing", json={"message": ASKED, "token": token})
    check("a text in the simulator gets it", r.status_code == 200
          and r.get_json()["reply"] == call_us, r.get_data(as_text=True)[:120])
    r = client.get(f"/textsim/bobs_plumbing/updates?token={token}")
    check("the simulator's update poll: no news, not a 500", r.status_code == 200
          and r.get_json() == {"messages": []}, r.status_code)
    with app.test_request_context():
        reply = app_module.sms_turn(db.get_business_by_slug("bobs_plumbing"),
                                    "+15855550199", ASKED)
    check("a real text gets it too (Twilio had nothing to send)", reply == call_us, reply)
    said = [line for line in kept.lines if "won't load" in line]
    check("the log says why, once a request, naming the business", said
          and "Bob's Plumbing" in said[0] and "AttributeError" in said[0], said[:1])
    check("…and never what the customer wrote",
          not any("leaky sink" in line for line in said))

    heading("which number")
    db.set_config_override(biz["id"], "business.phone", "(585) 555-0999")
    r = client.post("/webchat/bobs_plumbing", json={"message": ASKED, "session_id": SESSION})
    check("the owner's saved number, over the file's", "(585) 555-0999" in r.get_json()["reply"],
          r.get_json())
    conn = db.get_connection()
    conn.execute("DELETE FROM config_overrides WHERE business_id = ?", (biz["id"],))
    conn.commit()
    conn.close()
    use(biz["id"], text=(ROOT / "config/bobs_plumbing.yaml").read_text()
        .replace("  phone:", "\tphone:", 1), name="unreadable")
    r = client.post("/webchat/bobs_plumbing", json={"message": ASKED, "session_id": SESSION})
    check("no number to be found: 'try again later', not a made-up one",
          r.get_json() == {"reply": offline_reply()}
          and r.get_json()["reply"].endswith("Please try again later."), r.get_json())

    heading("a config that loads, with a time zone that isn't one")
    bad_zone = yaml.safe_load((ROOT / "config" / "bobs_plumbing.yaml").read_text())
    bad_zone["calendar"]["timezone"] = "America/New_Yrok"
    use(biz["id"], bad_zone, name="bad_zone")
    db.save_appointment(SESSION, "leak repair", "2026-10-29 15:00", biz["id"])
    conn = db.get_connection()
    booked = conn.execute("SELECT MAX(id) FROM appointments").fetchone()[0]
    conn.close()
    db.add_notification(biz["id"], booked, SESSION, "webchat", "moved",
                        "Your appointment moved to 3pm.", "pending", "2026-10-23T09:00:00")
    r = client.get(f"/webchat/bobs_plumbing/updates?session_id={SESSION}")
    check("the update poll: no news, not a 500", r.status_code == 200
          and r.get_json() == {"messages": []}, r.status_code)
    use(biz["id"], seed, name="fixed")
    r = client.get(f"/webchat/bobs_plumbing/updates?session_id={SESSION}")
    check("…and the notice waiting is delivered once it's fixed, not lost",
          r.get_json() == {"messages": ["Your appointment moved to 3pm."]}, r.get_json())

    heading("a config that loads: nothing changes")
    import html
    page = html.unescape(client.get("/chat/bobs_plumbing").get_data(as_text=True))
    check("the usual greeting", "Hi! I'm the Bob's Plumbing assistant. How can I help?" in page)

    # The owner's portal (2026-10-24). Measured before: six of the owner's
    # eight pages were the general "Something went wrong on our side" 500,
    # Appointments (where they land) among them; Conversations and Knowledge
    # worked.
    heading("the owner's portal, with a config that won't load")
    import config
    try:
        config.load_config(str(use(biz["id"], wont_load, name="wont_load")), biz["id"])
        raised = None
    except Exception as e:
        raised = e
    check("load_config says ConfigUnreadable, with the cause kept",
          isinstance(raised, config.ConfigUnreadable)
          and isinstance(raised.cause, AttributeError), repr(raised))
    db.create_user("owner@example.test", "owner long passphrase", business_id=biz["id"])
    db.create_user("operator@example.test", "operator long passphrase", is_operator=True)

    def signed_in(email, password):
        c = app.test_client()
        t = re.search(r'name="_csrf_token" value="([^"]+)"',
                      c.get("/admin/login").get_data(as_text=True)).group(1)
        c.post("/admin/login", data={"_csrf_token": t, "email": email, "password": password})
        return c
    owner = signed_in("owner@example.test", "owner long passphrase")
    operator = signed_in("operator@example.test", "operator long passphrase")
    needs_config = ["appointments", "calendar", "settings", "cancellation", "notice", "menu"]
    for name in needs_config:
        r = owner.get(f"/admin/business/{biz['id']}/{name}")
        body = html.unescape(r.get_data(as_text=True))
        check(f"{name}: says the settings can't be read, not 'something went wrong'",
              r.status_code == 503 and "Bob's Plumbing's settings can't be read" in body
              and "Something went wrong on our side" not in body, r.status_code)
    r = owner.get("/admin/", follow_redirects=True)
    body = html.unescape(r.get_data(as_text=True))
    check("signing in lands there too (the dashboard sends owners to Appointments)",
          "settings can't be read" in body)
    check("…says what customers are told, with the number",
          "call you at (585) 555-0123" in " ".join(re.sub(r"<[^>]+>", "", body).split()))
    check("…links to the pages that still work",
          f"/admin/business/{biz['id']}/conversations" in body
          and f"/admin/business/{biz['id']}/knowledge" in body)
    check("…and to telling us, with the page filled in",
          "/admin/help?from=/admin/business/" in body)
    check("an owner isn't shown the file or the error", "wont_load.yaml" not in body
          and "AttributeError" not in body)
    for name in ("conversations", "knowledge"):
        check(f"{name} still opens", owner.get(f"/admin/business/{biz['id']}/{name}").status_code
              == 200)
    body = html.unescape(operator.get(f"/admin/business/{biz['id']}/settings").get_data(as_text=True))
    check("the operator sees what's wrong (config_check) and which file",
          "What's wrong (" in body and "wont_load.yaml" in body and "Loading it failed" in body)
    check("…with a link back to Businesses, not 'tell us'", 'href="/admin/"' in body
          and "Tell us about it" not in body)
    said = [line for line in kept.lines if "can't be loaded" in line]
    check("the log says which business and which page, once a request",
          said and "Bob's Plumbing" in said[-1] and "/admin/business/" in said[-1], said[-1:])

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

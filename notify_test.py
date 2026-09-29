#!/usr/bin/env python3
"""notify_test.py: a customer hears when their appointment moves or is cancelled.

Run it:  python3 notify_test.py

Before 2026-09-28 an owner could move or cancel a booking, in the portal or
in Google Calendar, and the customer was told nothing; reconcile.py even
said so on purpose, because there was no way to send anything. notify.py is
that way: one step every change path calls, an outbox the chat window
collects from, and a record the owner can read.

No server, network or model: the database is a throwaway file, the
calendar is faked, and the Flask endpoint runs against a fake request.
"""

import ast
import logging
import sys
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="notify_test_")) / "test.db"

import booking_state_test as helpers      # installs stubs for google/anthropic/chromadb

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def future(days, hour):
    return (datetime.now() + timedelta(days=days)).replace(
        hour=hour, minute=0).strftime("%Y-%m-%d %H:%M")


def book(phone, when, name="Casey", event=None):
    db.save_appointment(phone, "custom cake", when, 1, customer_name=name,
                        external_event_id=event)
    return [a for a in db.get_appointments(1) if a["phone"] == phone][-1]


CONFIG = {"business": {"id": 1, "name": "Sunrise Bakery & Café",
                       "phone": "(585) 555-0188"},
          "booking": {"noun": "order"},
          "calendar": {"timezone": "America/New_York"}}


def test_words():
    import notify
    heading("who can be reached, and how")
    check("a web chat customer: web chat", notify.channel_for("web_ab12cd34") == ("webchat", None))
    check("a phone number: SMS, not set up yet",
          notify.channel_for("+15855550123") == ("sms", "SMS isn't set up yet"))
    check("anything else: no way to reach them",
          notify.channel_for("eval_123")[0] == "none")

    heading("what the customer reads")
    appt = {"customer_name": "Casey", "service": "custom cake",
            "datetime": "2026-10-02 09:00"}
    moved = notify.message_for(appt, CONFIG, "moved", "2026-10-03 10:30")
    check("a move names them, the business, both times, and how to reply",
          moved == "Hi Casey, it's Sunrise Bakery & Café. We've moved your custom "
                   "cake order from Friday, October 2 at 9:00 AM to Saturday, "
                   "October 3 at 10:30 AM. If that time doesn't work, reply here "
                   "or call us at (585) 555-0188.", moved)
    cancelled = notify.message_for(appt, CONFIG, "cancelled")
    check("a cancellation says what, when, and how to rebook",
          cancelled == "Hi Casey, it's Sunrise Bakery & Café. Your custom cake order "
                       "on Friday, October 2 at 9:00 AM has been cancelled. To "
                       "rebook or ask about it, reply here or call us at "
                       "(585) 555-0188.", cancelled)
    anon = notify.message_for(dict(appt, customer_name=None), CONFIG, "cancelled")
    check("no name on the booking: a plain greeting", anon.startswith("Hi, it's Sunrise"))


def test_outbox():
    import notify
    db.init_db()
    heading("web chat: the notice waits, is delivered once, and joins the conversation")
    appt = book("web_casey01", future(4, 9))
    status, reason = notify.notify_change(appt, CONFIG, "moved", future(5, 10))
    check("recorded as pending for the chat to collect", status == "pending" and reason is None)
    got = db.collect_notifications(1, "web_casey01", "2026-09-28T21:30:00")
    check("the chat collects it", len(got) == 1 and "We've moved" in got[0]["body"])
    check("only once", db.collect_notifications(1, "web_casey01", "x") == [])
    notes = db.notifications_for_appointment(appt["id"])
    check("marked delivered, with when", notes[0]["status"] == "delivered"
          and notes[0]["delivered_at"] == "2026-09-28T21:30:00")
    convo = db.get_recent_messages("web_casey01", 1, limit=5)
    check("and added to the conversation log",
          any("We've moved" in m["content"] for m in convo), str(convo))
    check("nobody else's chat gets it", db.collect_notifications(1, "web_other", "x") == [])

    heading("two quick moves send one message, with the latest time")
    appt = book("web_casey02", future(4, 11))
    notify.notify_change(appt, CONFIG, "moved", future(5, 9))
    notify.notify_change(appt, CONFIG, "moved", future(6, 14))
    got = db.collect_notifications(1, "web_casey02", "now")
    check("one notice delivered", len(got) == 1)
    later = datetime.strptime(future(6, 14), "%Y-%m-%d %H:%M").strftime("%A, %B %-d at %-I:%M %p")
    check("and it has the latest time", later in got[0]["body"], got[0]["body"])
    statuses = [n["status"] for n in db.notifications_for_appointment(appt["id"])]
    check("the first is recorded as replaced", statuses == ["delivered", "superseded"], str(statuses))

    heading("not sent, and why")
    appt = book("+15855550123", future(3, 10))
    status, reason = notify.notify_change(appt, CONFIG, "cancelled")
    check("a phone booking: not sent until Twilio", status == "not_sent"
          and reason == "SMS isn't set up yet")
    past = book("web_casey03", (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M"))
    status, reason = notify.notify_change(past, CONFIG, "cancelled")
    check("an appointment already behind them: not sent", status == "not_sent"
          and "passed" in reason)
    check("and the chat doesn't get it", db.collect_notifications(1, "web_casey03", "x") == [])

    heading("a notification problem never blocks the owner's change")
    logging.disable(logging.CRITICAL)
    try:
        status, reason = notify.notify_change({"phone": "web_x"}, CONFIG, "moved", "bad")
    finally:
        logging.disable(logging.NOTSET)
    check("a broken record returns 'not sent' instead of raising", status == "not_sent")


def test_calendar_path():
    heading("a move or deletion in Google Calendar tells the customer too")
    import reconcile
    # The same event id on ANOTHER business's appointment, saved first so an
    # unscoped lookup would find it first. It must be left alone.
    db.save_appointment("web_other", "custom cake", future(4, 9), 2,
                        external_event_id="evt-move")
    moved_appt = book("web_cal01", future(4, 9), event="evt-move")
    gone_appt = book("web_cal02", future(4, 13), event="evt-gone")
    new_start = datetime.strptime(future(7, 15), "%Y-%m-%d %H:%M")
    events = [{"id": "evt-move", "status": "confirmed",
               "start": {"dateTime": new_start.strftime("%Y-%m-%dT%H:%M:00-04:00")}},
              {"id": "evt-gone", "status": "cancelled"}]
    fake_cal = types.SimpleNamespace(is_enabled=lambda cfg: True,
                                     fetch_changes=lambda cfg, tok: (events, "tok2"))
    loaded_with = []
    real = (reconcile.calendar_sync, reconcile.load_config)
    reconcile.calendar_sync = fake_cal
    reconcile.load_config = lambda path, *a: loaded_with.append(a) or CONFIG
    try:
        reconcile.reconcile_business({"id": 1, "name": "Sunrise", "config_path": "x"})
    finally:
        reconcile.calendar_sync, reconcile.load_config = real
    check("reconcile loads the business's own settings (a demo stays on its "
          "simulated calendar)", loaded_with == [(1,)], str(loaded_with))
    other = [a for a in db.get_appointments(2) if a["phone"] == "web_other"][0]
    check("another business's appointment with the same event id is untouched",
          other["datetime"] == future(4, 9) and
          not db.notifications_for_appointment(other["id"]))
    moved = db.notifications_for_appointment(moved_appt["id"])
    gone = db.notifications_for_appointment(gone_appt["id"])
    check("the calendar move produced a 'moved' notice",
          len(moved) == 1 and moved[0]["kind"] == "moved", str(moved))
    check("the calendar deletion produced a 'cancelled' notice",
          len(gone) == 1 and gone[0]["kind"] == "cancelled", str(gone))


def test_reconcile_runs_often_enough():
    heading("Google Calendar changes are pulled in wherever they'd be seen")
    import reconcile
    calls = []
    real = reconcile.reconcile_business
    reconcile.reconcile_business = lambda b: calls.append(b["id"]) or ["x"]
    reconcile._last_run.clear()
    try:
        biz = {"id": 7, "name": "Bob's"}
        reconcile.maybe_reconcile(biz, min_interval=60)
        reconcile.maybe_reconcile(biz, min_interval=60)
        check("throttled: twice within a minute is one call", calls == [7], str(calls))
        reconcile.maybe_reconcile(biz, min_interval=0)
        check("min_interval=0 always runs (the appointments list)", calls == [7, 7])
        reconcile.maybe_reconcile({"id": 8, "name": "Sunrise"}, min_interval=60)
        check("per business", calls == [7, 7, 8])
        def boom(b): raise RuntimeError("Google is down")
        reconcile.reconcile_business = boom
        logging.disable(logging.CRITICAL)
        try:
            out = reconcile.maybe_reconcile({"id": 9, "name": "X"}, min_interval=0)
        finally:
            logging.disable(logging.NOTSET)
        check("a Google failure doesn't break the page", out == [])
    finally:
        reconcile.reconcile_business = real
        reconcile._last_run.clear()

    routes = ast.parse(Path("admin/routes.py").read_text())
    funcs = {n.name: n for n in routes.body if isinstance(n, ast.FunctionDef)}
    for page in ("appointments", "calendar_view", "appointment_detail"):
        calls = {getattr(c.func, "id", None) for c in ast.walk(funcs[page])
                 if isinstance(c, ast.Call)}
        check(f"portal page {page} pulls calendar changes", "maybe_reconcile" in calls)
    app_tree = ast.parse(Path("app.py").read_text())
    upd = next(n for n in app_tree.body if isinstance(n, ast.FunctionDef)
               and n.name == "webchat_updates")
    check("the customer's open chat pulls them too (throttled)",
          "maybe_reconcile" in {getattr(c.func, "id", None) for c in ast.walk(upd)
                                if isinstance(c, ast.Call)})

    heading("every page of Google's results is read")
    import calendar_google
    pages = [{"items": [{"id": "a"}], "nextPageToken": "p2"},
             {"items": [{"id": "b"}], "nextSyncToken": "sync-final"}]
    asked = []
    class Events:
        def list(self, **params):
            asked.append(dict(params))
            return type("R", (), {"execute": lambda self: pages[len(asked) - 1]})()
    real_service = calendar_google._get_service
    calendar_google._get_service = lambda: type("S", (), {"events": lambda self: Events()})()
    try:
        items, token = calendar_google.fetch_changes(
            {"calendar": {"calendar_id": "c", "timezone": "America/New_York"}}, "tok")
    finally:
        calendar_google._get_service = real_service
    check("events from both pages", [e["id"] for e in items] == ["a", "b"], str(items))
    check("and the sync token from the last page", token == "sync-final")
    check("the second request asked for page 2", asked[1].get("pageToken") == "p2")


def test_wiring():
    heading("every change path calls the same step")
    routes = ast.parse(Path("admin/routes.py").read_text())
    funcs = {n.name: n for n in routes.body if isinstance(n, ast.FunctionDef)}
    for name in ("cancel", "reschedule"):
        calls = {getattr(c.func, "id", None) for c in ast.walk(funcs[name])
                 if isinstance(c, ast.Call)}
        check(f"portal {name} calls notify_change", "notify_change" in calls)
    rec = Path("reconcile.py").read_text()
    check("reconcile calls notify_change for moves and cancellations",
          rec.count("notify_change(appt, config,") == 2)

    heading("the chat collects notices; the owner can read them")
    page = Path("templates/demo.html").read_text()
    check("the chat page checks for notices", "/updates?session_id=" in page
          and "setInterval(checkForUpdates" in page)
    detail = Path("admin/templates/admin/appointment_detail.html").read_text()
    check("the appointment page lists what the customer was told",
          "What we told the customer" in detail and "n.reason" in detail)

    heading("the updates endpoint")
    app_tree = ast.parse(Path("app.py").read_text())
    fn = next(n for n in app_tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "webchat_updates")
    fn.decorator_list = []
    import re
    ns = {"re": re, "load_config": lambda *a: CONFIG,
          "get_business_by_slug": lambda slug: {"id": 1, "config_path": "x"}
          if slug == "sunrise" else None,
          "log_web": logging.getLogger("t")}
    exec(compile(ast.Module([fn], []), "app.py", "exec"), ns)
    def call(slug, session):
        ns["request"] = types.SimpleNamespace(args={"session_id": session})
        return ns["webchat_updates"](slug)
    appt = book("web_ep01", future(4, 9))
    import notify
    notify.notify_change(appt, CONFIG, "cancelled")
    out = call("sunrise", "web_ep01")
    check("hands the notice to its chat", isinstance(out, dict)
          and len(out["messages"]) == 1 and "cancelled" in out["messages"][0], str(out))
    check("once", call("sunrise", "web_ep01") == {"messages": []})
    check("rejects a malformed session id", call("sunrise", "../etc")[1] == 400)
    check("unknown business is a 404", call("nope", "web_ep01")[1] == 404)


def test_retention():
    heading("notices are pruned with the conversation log")
    conn = db.get_connection()
    conn.execute("UPDATE notifications SET created_at = '2000-01-01T00:00:00'")
    conn.commit(); conn.close()
    removed = db.prune_personal_data()
    check("old notices removed", removed.get("notifications", 0) > 0, str(removed))


def main():
    helpers._load_scheduler()           # installs the integration stubs
    test_words()
    test_outbox()
    test_calendar_path()
    test_reconcile_runs_often_enough()
    test_wiring()
    test_retention()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

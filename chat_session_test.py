#!/usr/bin/env python3
"""chat_session_test.py — the web chat keeps its conversation across a reload.

Run it:  python3 chat_session_test.py

Until 2026-10-17 the chat page made a new session id on every load, so a
reload started over: the customer lost the conversation on screen, and a
notice the owner sent later (a moved appointment) went to a session no
page would ever ask for again. Now the page keeps a long random id in the
browser for 7 days and re-shows the chat from /webchat/<slug>/history.

Holding an id means reading that chat back, so this checks that only the
long, unguessable ids can, that the endpoint is rate limited, and that it
reads one chat of one business. The route runs from app.py's own source
against stand-ins (as reply_safety_test does), with a throwaway database.
No server, network or model.
"""

import ast
import re
import sys
import tempfile
import types
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="chat_session_test_")) / "test.db"

ROOT = Path(__file__).resolve().parent
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def route_from_app(name, **stand_ins):
    """A route function from app.py, without its decorator, run against
    stand-ins for the Flask and app globals it uses."""
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    fn.decorator_list = []
    consts = [n for n in tree.body if isinstance(n, ast.Assign)
              and any(getattr(t, "id", "") in ("_KEPT_SESSION", "HISTORY_MESSAGES") for t in n.targets)]
    namespace = {"re": re, **stand_ins}
    exec(compile(ast.Module(body=consts + [fn], type_ignores=[]), "app.py", "exec"), namespace)
    return namespace[name]


def main():
    db.init_db()
    conn = db.get_connection()
    conn.execute("INSERT INTO businesses (id, name, slug, config_path) VALUES (1, 'Bob', 'bob', 'x')")
    conn.execute("INSERT INTO businesses (id, name, slug, config_path) VALUES (2, 'Sue', 'sue', 'x')")
    conn.commit(); conn.close()

    kept = "web_" + "k" * 24
    db.save_message(kept, "user", "Can I book a drain cleaning?", 1, source="customer")
    db.save_message(kept, "assistant", "Sure. What day suits you?", 1, source="llm")
    db.save_message(kept, "user", "Tuesday", 1, source="customer")
    db.save_message(kept, "assistant", "Booked for Tuesday at 10am.", 1, source="scheduler")
    db.save_message("web_" + "z" * 24, "user", "Someone else's chat", 1, source="customer")
    db.save_message(kept, "user", "Same id, another business", 2, source="customer")
    conn = db.get_connection()
    conn.execute("INSERT INTO notifications (business_id, appointment_id, recipient, channel, kind, "
                 "body, status, created_at) VALUES (1, 1, ?, 'webchat', 'moved', "
                 "'Your appointment moved to 11am.', 'pending', '2026-10-17')", (kept,))
    conn.commit(); conn.close()
    db.collect_notifications(1, kept, "2026-10-17T10:00:00")

    heading("the transcript")
    turns = db.chat_transcript(kept, 1)
    check("one chat, one business, oldest first",
          [t["content"] for t in turns] == ["Can I book a drain cleaning?", "Sure. What day suits you?",
                                            "Tuesday", "Booked for Tuesday at 10am.",
                                            "Your appointment moved to 11am."], turns)
    check("a notice the business sent is marked as one",
          [t["notice"] for t in turns] == [False, False, False, False, True])
    check("the last N only", [t["content"] for t in db.chat_transcript(kept, 1, limit=2)]
          == ["Booked for Tuesday at 10am.", "Your appointment moved to 11am."])

    heading("the history endpoint")
    import ratelimit
    state = {"args": {}, "ip": "203.0.113.9"}
    businesses = {"bob": {"id": 1, "name": "Bob"}, "sue": {"id": 2, "name": "Sue"}}
    history = route_from_app(
        "webchat_history",
        get_business_by_slug=businesses.get,
        request=types.SimpleNamespace(args=types.SimpleNamespace(get=lambda k, d=None: state["args"].get(k, d))),
        _client_ip=lambda: state["ip"])

    def ask(slug, session_id, ip="203.0.113.9"):
        state["args"], state["ip"] = {"session_id": session_id}, ip
        out = history(slug)
        return out if isinstance(out, tuple) else (out, 200)

    body, status = ask("bob", kept)
    check("a kept session gets its chat back, customer and bot, notices marked",
          status == 200 and [m["from"] for m in body["messages"]] == ["customer", "bot", "customer", "bot", "bot"]
          and body["messages"][-1] == {"from": "bot", "text": "Your appointment moved to 11am.", "notice": True},
          body)
    body, status = ask("sue", kept)
    check("the same id at another business reads only that business's chat",
          [m["text"] for m in body["messages"]] == ["Same id, another business"])
    body, status = ask("bob", "web_" + "q" * 24)
    check("an id with no chat gets an empty list, not an error", status == 200 and body["messages"] == [])
    for bad, why in (("web_abc12345", "an old 8-character id (Math.random, too short to be a key)"),
                     ("", "no id"), ("web_" + "K" * 24, "capitals (not what the page makes)"),
                     ("web_" + "k" * 24 + "'; DROP", "anything else in it"),
                     ("+15555550123", "a phone number")):
        body, status = ask("bob", bad)
        check(f"refused: {why}", status == 400, (status, body))
    body, status = ask("nobody", kept)
    check("an unknown business is a 404 (the page shows 'this chat has ended')", status == 404)

    real_minute = ratelimit.WEBCHAT_HISTORY_IP_PER_MINUTE
    ratelimit.WEBCHAT_HISTORY_IP_PER_MINUTE = 3
    try:
        codes = [ask("bob", "web_" + c * 24, ip="198.51.100.7")[1] for c in "abcde"]
    finally:
        ratelimit.WEBCHAT_HISTORY_IP_PER_MINUTE = real_minute
    check("trying ids by the dozen from one address is cut off", codes[:3] == [200] * 3 and 429 in codes[3:],
          codes)

    heading("the page")
    page = (ROOT / "templates" / "demo.html").read_text(encoding="utf-8")
    check("the id comes from crypto.getRandomValues, 24 characters, never Math.random",
          "crypto.getRandomValues" in page and "new Uint8Array(24)" in page
          and "Math.random" not in re.sub(r"//[^\n]*", "", page))
    check("it's kept per business for 7 days, and storage being blocked doesn't break the page",
          'const STORE_KEY = "simon-chat:" + slug;' in page and "KEEP_DAYS = 7" in page
          and page.count("try {") >= 3)
    check("a reloaded page asks for the history, then for notices that arrived meanwhile",
          "if (resumed) showHistory().then(checkForUpdates);" in page and "/history?session_id=" in page)
    check("history is shown as text, never as markup", "innerHTML" not in page.replace(
        "textContent, not innerHTML", ""))
    check("there's a way to start over (a shared computer), which forgets the id",
          'id="newChat"' in page and "localStorage.removeItem(STORE_KEY)" in page)
    app_src = (ROOT / "app.py").read_text(encoding="utf-8")
    check("the id the page makes is one the server accepts as kept",
          '_KEPT_SESSION = re.compile(r"web_[a-z0-9]{24,32}")' in app_src
          and "/^web_[a-z0-9]{24,32}$/" in page)
    check("the posting and updates endpoints still accept the id (and old ones)",
          're.fullmatch(r"web_[a-z0-9]{1,32}", session_id)' in app_src)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

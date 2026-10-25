#!/usr/bin/env python3
"""budget_test.py — a demo's model spend is capped per day; a business's isn't.

Run it:  python3 budget_test.py

budget.py: each demo may spend DEMO_DAILY_BUDGET ($0.50) a day on the model,
all demos together DEMOS_DAILY_BUDGET ($5). Real businesses are never capped.
Two layers: each route that uses the model asks budget.refusal() first and
answers with a friendly note; llm._create refuses any call past the budget
(budget.Spent), so a route that forgot to ask still can't spend.

The model is a fake that reports token usage, so this runs offline and the
costs are exact: one fake call costs $0.20 at the configured prices.

The sweep at the end posts to every route with a demo that's over budget and
fails if any route got as far as asking the model: that route is missing its
door check, and its visitor would see a generic error, not the note.
"""

import logging
import os
import re
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.update(SECRET_KEY="b" * 64, COOKIE_SECURE="false",
                  ANTHROPIC_API_KEY="fake", VOYAGE_API_KEY="fake")
os.environ.pop("ADMIN_EMAIL", None)

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="budget_test_")) / "test.db"
import vector_boot
vector_boot.run_in_subprocess = lambda *a, **k: None

logging.disable(logging.CRITICAL)
import app as app_module
import budget
import llm
app = app_module.app

PASSED, FAILED = [], []
SENT = []        # every request the fake model received


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n" + "-" * len(text))


class FakeMessages:
    def create(self, **call):
        SENT.append(call)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="a fake reply")],
            usage=types.SimpleNamespace(input_tokens=FAKE_IN, output_tokens=FAKE_OUT),
            stop_reason="end_turn")


# Sized from the prices, so one fake call costs $0.20 whatever the model:
# at Haiku 4.5's prices a fixed 100K/20K was $0.20, at Haiku 5.5's $0.02, and
# crossing $0.50 then took 26 messages, past the chat rate limit (2026-10-22).
FAKE_IN = round(0.10 / llm.PRICE_IN_PER_MTOK * 1e6)
FAKE_OUT = round(0.10 / llm.PRICE_OUT_PER_MTOK * 1e6)
llm.client = types.SimpleNamespace(messages=FakeMessages())
CALL = FAKE_IN / 1e6 * llm.PRICE_IN_PER_MTOK + FAKE_OUT / 1e6 * llm.PRICE_OUT_PER_MTOK


def one_call_per_message(message, sender_id, business_id, config, channel):
    """The chat's turn, reduced to one model call: what's under test is who
    pays for it, not the conversation. Errors become an apology, as in the
    real safe_process_message."""
    try:
        return llm.text_of(llm._create(messages=[{"role": "user", "content": message}],
                                       max_tokens=10))
    except Exception:
        return "an apology"


app_module.safe_process_message = one_call_per_message
app_module.sms_turn = lambda business, phone, message: one_call_per_message(
    message, phone, business["id"], None, "sms")


_ADDRESSES = iter(f"198.18.{n // 250}.{n % 250 + 1}" for n in range(10_000))


def visitor(template="sunrise_bakery_and_cafe"):
    """A new demo, from a visitor's own address: each its own visitor, as
    real ones are (the per-visitor budget would otherwise pool them all)."""
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = next(_ADDRESSES)
    page = client.get("/demo").get_data(as_text=True)
    token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    client.post("/demo/start", data={"template": template, "_csrf_token": token})
    with client.session_transaction() as s:
        user = db.get_user_by_email(s["user_email"])
    return client, token, db.get_business_by_id(user["business_id"])


def chat(client, slug, text="hi"):
    return client.post(f"/webchat/{slug}", json={"message": text, "session_id": "s1"})


def spent(key):
    return db.get_model_spend(key, budget._day()) / 1e6


def test_counting_and_caps():
    heading("spend is counted per demo, and the demo cap holds")
    check("one fake call costs a known amount (the arithmetic below relies on it)",
          0 < CALL < budget.DEMO_DAILY, CALL)
    client, _, demo = visitor()
    other_client, _, other = visitor()
    calls = int(budget.DEMO_DAILY // CALL) + 1          # enough to cross $0.50
    for _ in range(calls):
        chat(client, demo["slug"])
    check(f"{calls} messages are counted against the demo",
          abs(spent(budget._key(demo["slug"])) - calls * CALL) < 1e-6,
          spent(budget._key(demo["slug"])))
    check("and against all demos", abs(spent(budget.ALL_DEMOS) - calls * CALL) < 1e-6)

    before = len(SENT)
    reply = chat(client, demo["slug"]).get_json()["reply"]
    check("past $0.50 the visitor gets the note", reply == budget.NOTE, reply)
    check("and the model isn't asked", len(SENT) == before)

    text_page = client.get(f"/chat/{demo['slug']}/text").get_data(as_text=True)
    token = re.search(r'const token = "([^"]+)"', text_page).group(1)
    sim = client.post(f"/textsim/{demo['slug']}", json={"message": "hi", "token": token})
    check("the text simulator gives the same note", sim.get_json()["reply"] == budget.NOTE,
          sim.get_json())

    reply = chat(other_client, other["slug"]).get_json()["reply"]
    check("another demo still answers", reply == "a fake reply", reply)

    heading("a real business is never capped or counted")
    template = db.get_business_by_slug("sunrise_bakery_and_cafe")
    total = spent(budget.ALL_DEMOS)
    reply = chat(app.test_client(), template["slug"]).get_json()["reply"]
    check("the real Sunrise answers", reply == "a fake reply", reply)
    check("and its call isn't counted as demo spend", spent(budget.ALL_DEMOS) == total)

    heading("all demos together: the $5 cap")
    db.add_model_spend(budget.ALL_DEMOS, budget._day(), budget.DEMOS_DAILY * 1e6)
    reply = chat(other_client, other["slug"]).get_json()["reply"]
    check("a demo under its own cap gets the all-demos note", reply == budget.ALL_NOTE, reply)
    reply = chat(app.test_client(), template["slug"]).get_json()["reply"]
    check("the real business still answers", reply == "a fake reply", reply)

    heading("a new day starts at zero")
    real_day = budget._day
    budget._day = lambda: "2099-01-01"
    try:
        reply = chat(client, demo["slug"]).get_json()["reply"]
        check("tomorrow the capped demo answers again", reply == "a fake reply", reply)
    finally:
        budget._day = real_day
    return client, demo


def test_crossing_message():
    """The message that crosses the cap is answered in full.

    A real turn makes several model calls (understand, then reply). Checked
    before every call, the budget ran out between them: in the browser, with
    a tiny budget, the message that crossed it got "Sorry, I'm having trouble
    right now" (2026-10-20). The budget is decided once, as the request
    starts; a request that starts under it finishes."""
    heading("the message that crosses the cap still gets its answer")
    client, _, demo = visitor()
    db.add_model_spend(budget._key(demo["slug"]), budget._day(),
                       (budget.DEMO_DAILY - CALL / 2) * 1e6)    # half a call left

    def two_calls(message, sender_id, business_id, config, channel):
        try:
            for _ in range(2):
                reply = llm.text_of(llm._create(
                    messages=[{"role": "user", "content": message}], max_tokens=10))
            return reply
        except Exception:
            return "an apology"
    real, app_module.safe_process_message = app_module.safe_process_message, two_calls
    real_all = db.get_model_spend
    # This demo alone: the all-demos total from earlier checks is left out.
    db.get_model_spend = lambda key, day: 0 if key == budget.ALL_DEMOS else real_all(key, day)
    try:
        reply = chat(client, demo["slug"]).get_json()["reply"]
        check("both calls of the crossing message ran", reply == "a fake reply", reply)
        reply = chat(client, demo["slug"]).get_json()["reply"]
        check("the next message gets the note", reply == budget.NOTE, reply)
    finally:
        app_module.safe_process_message = real
        db.get_model_spend = real_all


def test_the_call_itself(demo):
    heading("the model call refuses on its own (the guarantee)")
    with app.test_request_context(f"/chat/{demo['slug']}"):
        app.preprocess_request()
        before = len(SENT)
        try:
            llm._create(messages=[{"role": "user", "content": "x"}], max_tokens=5)
            refused = False
        except budget.Spent:
            refused = True
        check("_create raises budget.Spent for a demo over budget", refused)
        check("before anything is sent", len(SENT) == before)
    check("Spent is an ordinary Exception (callers that catch errors stay safe)",
          issubclass(budget.Spent, Exception))

    heading("it fails open")
    real = db.get_model_spend

    def broken(*a, **k):
        raise RuntimeError("disk I/O error")
    db.get_model_spend = broken
    try:
        with app.test_request_context(f"/chat/{demo['slug']}"):
            app.preprocess_request()
            check("an unreadable table lets the request through", budget.refusal() is None)
    finally:
        db.get_model_spend = real
    check("outside a request (evals, scripts), nothing is capped", budget.refusal() is None)


def test_every_route(client, demo):
    """Every POST route, with a demo over budget: none may reach the model.

    A route that does was missing its door check. The guarantee in _create
    stopped the spend, but the visitor got whatever error the route makes
    of budget.Spent instead of the note."""
    heading("every route checks the budget before the model")
    reached = []
    real_check = budget.check

    def watching():
        try:
            real_check()
        except budget.Spent:
            from flask import request
            reached.append(request.endpoint)
            raise
    budget.check = watching
    with client.session_transaction() as s:
        csrf = s.get("csrf_token")
    form = {"_csrf_token": csrf, "question": "Do you do gluten free?", "message": "hi",
            "url": "https://example.com/menu", "text": "Muffins $3", "title": "t",
            "body": "b", "from": "", "action": "save"}
    swept = 0
    try:
        for rule in app.url_map.iter_rules():
            if "POST" not in rule.methods or rule.endpoint in ("admin.login", "demo_start"):
                continue
            url = rule.rule
            for name in rule.arguments:
                value = {"business_id": demo["id"], "slug": demo["slug"]}.get(name, 1)
                url = re.sub(rf"<(?:\w+:)?{name}>", str(value), url)
            client.post(url, data=form)
            client.post(url, json={"message": "hi", "session_id": "s1", **form})
            swept += 1
    finally:
        budget.check = real_check
    check("routes were swept (not an empty loop)", swept >= 25, swept)
    check("no route reached the model past the budget", not reached, sorted(set(reached)))


def test_door_routes(client, demo):
    heading("the portal's model features say why")
    # By now all demos are over $5 too, so either note is the right one.
    said = lambda r: any(n in r.get_data(as_text=True) for n in (budget.NOTE, budget.ALL_NOTE))
    with client.session_transaction() as s:
        csrf = s.get("csrf_token")
    before = len(SENT)
    r = client.post("/admin/help/ask", data={"_csrf_token": csrf,
                                             "question": "How do I add a menu item?"},
                    follow_redirects=True)
    check("the owner assistant shows the note on the Help page", said(r), r.status_code)
    r = client.post(f"/admin/business/{demo['id']}/knowledge/preview",
                    data={"_csrf_token": csrf, "questions": "Do you do gluten free?"},
                    follow_redirects=True)
    check("Preview answers flashes the note", said(r), r.status_code)
    check("neither asked the model", len(SENT) == before)


def test_priced_by_model(demo):
    """A call is charged at the prices of the model it used. Before
    (2026-10-22), every call was priced as the receptionist's model, so a
    Sonnet call (ASSISTANT_MODEL, UPLOAD_MODEL, Preview's grounding check)
    counted a twentieth of its cost."""
    heading("each call is charged at its own model's prices")
    real_day = budget._day
    budget._day = lambda: "2099-02-02"                 # a fresh day, nothing spent
    try:
        key = budget._key(demo["slug"])
        for model in (llm.MODEL, "claude-sonnet-5-5", "a-model-nobody-priced"):
            with app.test_request_context(f"/chat/{demo['slug']}",
                                          environ_base={"REMOTE_ADDR": "198.18.99.1"}):
                app.preprocess_request()
                before = spent(key)
                llm._create(messages=[{"role": "user", "content": "x"}], max_tokens=5,
                            model=model)
                charged = spent(key) - before
            want = llm.cost_of(model, FAKE_IN, FAKE_OUT)
            visitor_key = budget._visitor_key("198.18.99.1")
            check(f"{model}: charged ${want:.2f}, to the demo and to its visitor",
                  abs(charged - want) < 1e-6 and abs(spent(visitor_key) - want) < 1e-6,
                  (charged, spent(visitor_key), want))
            for undo in (key, budget.ALL_DEMOS, visitor_key):
                db.add_model_spend(undo, budget._day(), -charged * 1e6)
        check("Sonnet 5.5 costs 20 times Haiku 5.5's",
              abs(llm.cost_of("claude-sonnet-5-5", FAKE_IN, FAKE_OUT)
                  - 20 * llm.cost_of("claude-haiku-5-5", FAKE_IN, FAKE_OUT)) < 1e-9)
        check("a model the table doesn't know is priced as the dearest it does",
              llm.prices_for("a-model-nobody-priced") == max(llm._PRICES.values()))
    finally:
        budget._day = real_day


def fresh_day(limits=False):
    """Today's spend back to zero (the tests above leave all demos over);
    with limits, the rate-limit counters too, so the tests after start
    with their allowance."""
    conn = db.get_connection()
    conn.execute("DELETE FROM model_spend")
    if limits:
        conn.execute("DELETE FROM rate_limits")
    conn.commit()
    conn.close()


def from_address(address, xff=None):
    """A visitor's demo, as if from this address. With xff, the request
    carries that X-Forwarded-For (Render's shape: what the visitor sent,
    then the real address, then two proxies)."""
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = address
    if xff:
        client.environ_base["HTTP_X_FORWARDED_FOR"] = xff
    page = client.get("/demo").get_data(as_text=True)
    token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    r = client.post("/demo/start", data={"template": "sunrise_bakery_and_cafe",
                                         "_csrf_token": token})
    return client, token, r.headers["Location"].rsplit("/", 1)[-1]


def test_one_visitor_many_demos():
    """Measured 2026-10-24 (offline, $0.20 a call): one address starting
    demo after demo spent the whole all-demos day with 8 demos, and every
    other visitor got ALL_NOTE. Now each visitor has a day's budget of its
    own, across every demo it starts."""
    heading("one visitor starting demo after demo can't spend everyone's day")
    import ratelimit
    fresh_day()
    saved = (ratelimit.DEMO_PER_MINUTE, ratelimit.DEMO_PER_HOUR)
    ratelimit.DEMO_PER_MINUTE = ratelimit.DEMO_PER_HOUR = 1000
    try:
        replies = []
        for n in range(12):
            client, _, slug = from_address("198.51.100.7")
            for i in range(2):
                replies.append(chat(client, slug, f"hi {n} {i}").get_json()["reply"])
        check("the visitor is stopped at their own day's budget, with their own note",
              budget.VISITOR_NOTE in replies, replies[-3:])
        check("…having spent about $1, not $5",
              budget.VISITOR_DAILY <= spent(budget._visitor_key("198.51.100.7"))
              <= budget.VISITOR_DAILY + CALL * 2
              and spent(budget.ALL_DEMOS) < budget.DEMOS_DAILY,
              (spent(budget._visitor_key("198.51.100.7")), spent(budget.ALL_DEMOS)))
        other, _, slug = from_address("203.0.113.50")
        check("another visitor's demo still answers",
              chat(other, slug).get_json()["reply"] == "a fake reply")
        # Render's X-Forwarded-For: whatever the visitor wrote comes first.
        # A new made-up first entry each time is still the same visitor.
        faked = [chat(*from_address("10.24.90.146",
                                    xff=f"192.0.2.{n}, 198.51.100.7, 162.159.114.27, 10.24.90.146")[0::2]
                      ).get_json()["reply"] for n in range(3)]
        check("a made-up X-Forwarded-For doesn't make a new visitor",
              all(r == budget.VISITOR_NOTE for r in faked), faked)
        check("a real business is never counted against a visitor",
              chat(app.test_client(), "bobs_plumbing").get_json()["reply"] == "a fake reply")
    finally:
        ratelimit.DEMO_PER_MINUTE, ratelimit.DEMO_PER_HOUR = saved
        fresh_day(limits=True)


def test_upload_doors():
    """The sweep's form has no file and no fetchable page, so the upload
    routes stopped before their budget check and it was never exercised
    (backlog, 2026-10-23). With real inputs: under budget each reaches the
    model; over it, none does, and each says why."""
    heading("the upload routes check the budget, with real uploads")
    import io
    import ratelimit
    import web_import
    from PIL import Image
    fresh_day()
    saved = (ratelimit.UPLOAD_PER_MINUTE, ratelimit.UPLOAD_PER_HOUR,
             ratelimit.DEMO_PER_MINUTE, ratelimit.DEMO_PER_HOUR, web_import.read_page)
    ratelimit.UPLOAD_PER_MINUTE = ratelimit.UPLOAD_PER_HOUR = 1000
    ratelimit.DEMO_PER_MINUTE = ratelimit.DEMO_PER_HOUR = 1000
    web_import.read_page = lambda config, url: ("Blueberry muffins $3.50", "web page", [], url)
    image = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(image, "PNG")

    def attempts(over):
        out = {}
        for n, route in enumerate(("photo", "web page", "re-suggest")):
            client, token, demo = visitor()
            fresh_day()                      # each route on its own day's budget
            if over:
                db.add_model_spend(budget._key(demo["slug"]), budget._day(), 10_000_000)
            base = f"/admin/business/{demo['id']}/knowledge/upload"
            before = len(SENT)
            if route == "photo":
                r = client.post(base, data={"_csrf_token": token,
                                            "photo": (io.BytesIO(image.getvalue()), "menu.png")},
                                content_type="multipart/form-data", follow_redirects=True)
            elif route == "web page":
                r = client.post(base + "/url", data={"_csrf_token": token,
                                                     "url": "https://example.com/menu"},
                                follow_redirects=True)
            else:
                db.save_upload_proposal(demo["id"], "Muffins $3", [], created_by="x",
                                        source="photo", hidden=[])
                r = client.post(base + "/resuggest", data={"_csrf_token": token,
                                                           "transcription": "Muffins $3.25"},
                                follow_redirects=True)
            out[route] = (len(SENT) - before, budget.NOTE in r.get_data(as_text=True))
        return out
    try:
        under, over = attempts(False), attempts(True)
        for route in under:
            check(f"{route}: under budget it reaches the model (the test has teeth)",
                  under[route][0] > 0, under[route])
            check(f"{route}: over budget, no model call, and the note",
                  over[route] == (0, True), over[route])
    finally:
        (ratelimit.UPLOAD_PER_MINUTE, ratelimit.UPLOAD_PER_HOUR, ratelimit.DEMO_PER_MINUTE,
         ratelimit.DEMO_PER_HOUR, web_import.read_page) = saved
        fresh_day(limits=True)


def main():
    test_upload_doors()
    test_one_visitor_many_demos()
    client, demo = test_counting_and_caps()
    test_crossing_message()
    test_the_call_itself(demo)
    test_door_routes(client, demo)
    test_every_route(client, demo)
    test_priced_by_model(demo)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""usage_test.py — every model call is in the ledger; a business over its
month's budget still gives customers its phone number.

Run it:  python3 usage_test.py

usage.py sends and records every call (one row each in usage_ledger);
budget.py holds each business to budget.monthly_usd. Offline: the model is
a fake that reports token usage and counts the calls it receives, so costs
are exact and "no model call" can be counted. Design and results:
notes/spend_caps.md.
"""

import ast
import html
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
os.environ.update(SECRET_KEY="u" * 64, COOKIE_SECURE="false",
                  ANTHROPIC_API_KEY="fake", VOYAGE_API_KEY="fake")
os.environ.pop("ADMIN_EMAIL", None)
for name in ("SMTP_HOST", "SUPPORT_ALERT_EMAIL", "USAGE_EVAL"):
    os.environ.pop(name, None)

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="usage_test_")) / "test.db"
import vector_boot
vector_boot.run_in_subprocess = lambda *a, **k: None

logging.disable(logging.CRITICAL)
import app as app_module
import budget
import llm
import usage
from config import load_config
from conversation import process_message
app = app_module.app

PASSED, FAILED = [], []
SENT = []          # every request the fake model received


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n" + "-" * len(text))


# ---------------------------------------------------------------------------
# The fake model
# ---------------------------------------------------------------------------

FAKE = {"in": 1000, "out": 200, "cache_read": 0, "cache_write": 0, "raise": None,
        "text": "a fake reply"}


class FakeMessages:
    def create(self, **call):
        SENT.append(call)
        if FAKE["raise"]:
            raise FAKE["raise"]
        return types.SimpleNamespace(
            model=call.get("model"),
            content=[types.SimpleNamespace(type="text", text=FAKE["text"])],
            usage=types.SimpleNamespace(
                input_tokens=FAKE["in"], output_tokens=FAKE["out"],
                cache_read_input_tokens=FAKE["cache_read"],
                cache_creation_input_tokens=FAKE["cache_write"]),
            stop_reason="end_turn")


llm.client = types.SimpleNamespace(messages=FakeMessages())
llm.retrieve = lambda message, config: []        # no vector store offline


def out_of_credit():
    import httpx
    from anthropic import APIStatusError
    response = httpx.Response(402, request=httpx.Request(
        "POST", "https://api.anthropic.com/v1/messages"))
    return APIStatusError("Your credit balance is too low", response=response,
                          body={"type": "error", "error": {
                              "type": "billing_error",
                              "message": "Your credit balance is too low"}})


def business(slug):
    row = db.get_business_by_slug(slug)
    return row, load_config(row["config_path"], row["id"])


def fill(business_id, dollars, month=None, billable=1):
    """Spend already in the ledger this month, as earlier calls left it."""
    db.add_usage(business_id=business_id, month=month or usage.month_for(business_id),
                 created_at="2026-11-01T00:00:00", purpose="customer_reply",
                 label="customer_reply", billable=billable, model="claude-haiku-5-5",
                 input_tokens=0, output_tokens=0, cache_read_tokens=0,
                 cache_write_tokens=0, micro_dollars=round(dollars * 1e6))


def empty_ledger():
    conn = db.get_connection()
    conn.execute("DELETE FROM usage_ledger")
    conn.execute("DELETE FROM usage_alerts")
    conn.execute("DELETE FROM support_requests")
    conn.execute("DELETE FROM conversation_state")
    conn.execute("DELETE FROM messages")
    conn.commit()
    conn.close()


def requests_filed():
    return db.list_support_requests()


# ---------------------------------------------------------------------------
# 1. No model call outside the wrapper
# ---------------------------------------------------------------------------

def app_sources():
    """The app's own Python: not tests, venvs, worktrees or scratch."""
    skip = {"venv", ".venv", ".claude", "backups", "node_modules", ".git"}
    for path in sorted(ROOT.rglob("*.py")):
        if skip & set(path.relative_to(ROOT).parts):
            continue
        if path.name.endswith("_test.py"):
            continue
        yield path


def scan_problems():
    """Every place the code reaches the model some other way than
    usage.send, or calls _create without saying what for."""
    found = []
    for path in app_sources():
        text = path.read_text()
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if (name == "create" and isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Attribute)
                    and func.value.attr == "messages" and path.name != "usage.py"):
                found.append(f"{path.name}:{node.lineno} calls messages.create")
            if name == "_create" and path.name not in ("assistant_eval.py",):
                if not any(k.arg == "purpose" for k in node.keywords):
                    found.append(f"{path.name}:{node.lineno} _create without purpose=")
        if "/v1/messages" in text and path.name != "usage.py":
            found.append(f"{path.name} names the API's URL")
        if path.name.endswith("_eval.py") and "usage.mark_eval_run()" not in text:
            found.append(f"{path.name} is an eval that doesn't call usage.mark_eval_run()")
    return found


def test_scan():
    heading("no model call outside the wrapper")
    found = scan_problems()
    check("every model call goes through usage.send, with a purpose", not found, found)
    check("the scan reads the call sites (not an empty loop)",
          sum(1 for p in app_sources() if "_create(purpose=" in p.read_text()) >= 6)
    # The scan has teeth: a file that calls the SDK directly is caught.
    probe = ROOT / "zz_probe_usage.py"
    probe.write_text("from anthropic import Anthropic\n"
                     "Anthropic().messages.create(model='x', max_tokens=1, messages=[])\n"
                     "from llm import _create\n_create(max_tokens=1, messages=[])\n")
    try:
        caught = scan_problems()
    finally:
        probe.unlink()
    check("…a direct messages.create and a _create without purpose are caught",
          sum("zz_probe_usage" in p for p in caught) == 2, caught)


# ---------------------------------------------------------------------------
# 2. Cost arithmetic
# ---------------------------------------------------------------------------

def test_arithmetic():
    heading("what a call costs, in micro-dollars")
    M = 1_000_000
    check("Haiku 5.5: a million input tokens is $0.10",
          usage.micro_dollars("claude-haiku-5-5", input_tokens=M) == 100_000)
    check("…a million output tokens $0.50",
          usage.micro_dollars("claude-haiku-5-5", output_tokens=M) == 500_000)
    check("…cache reads at a tenth of input, writes at 1.25x",
          usage.micro_dollars("claude-haiku-5-5", cache_read_tokens=M) == 10_000
          and usage.micro_dollars("claude-haiku-5-5", cache_write_tokens=M) == 125_000)
    check("Sonnet 5.5: 3,000 in, 500 out, 2,000 read, 1,000 written = $0.01390",
          usage.micro_dollars("claude-sonnet-5-5", 3000, 500, 2000, 1000)
          == 3000 * 2 + 500 * 10 + 2000 * 0.2 + 1000 * 2.5 == 13_900)
    check("a typical receptionist call (3,000 in, 150 out) is 375 micro-dollars",
          usage.micro_dollars("claude-haiku-5-5", 3000, 150) == 375)
    check("money is an integer, never a float",
          isinstance(usage.micro_dollars("claude-haiku-5-5", 7, 3), int))
    check("a dated snapshot finds its model",
          usage.prices_for("claude-sonnet-5-5-20261001") == usage.PRICES["claude-sonnet-5-5"])
    dearest = max(usage.PRICES.values())
    check("a model nobody priced is charged at the dearest known rate",
          usage.prices_for("claude-unknown-9") == dearest
          and usage.micro_dollars("claude-unknown-9", M) == round(dearest[0] * M))
    usage.PRICE_OVERRIDES["claude-test-model"] = (3.0, 15.0)
    try:
        check("LLM_PRICE_IN / LLM_PRICE_OUT override a model (cache follows input)",
              tuple(round(x, 6) for x in usage.prices_for("claude-test-model"))
              == (3.0, 15.0, 3.75, 0.3))
    finally:
        usage.PRICE_OVERRIDES.pop("claude-test-model")
    check("token counts come from the response's usage, cache fields included",
          usage.tokens_of(types.SimpleNamespace(
              input_tokens=5, output_tokens=6, cache_read_input_tokens=7,
              cache_creation_input_tokens=8)) == {
              "input_tokens": 5, "output_tokens": 6, "cache_read_tokens": 7,
              "cache_write_tokens": 8}
          and usage.tokens_of({"input_tokens": 1, "output_tokens": 2})["output_tokens"] == 2)


# ---------------------------------------------------------------------------
# 3. The ledger
# ---------------------------------------------------------------------------

def test_ledger():
    heading("each call is one ledger row, for the business served")
    empty_ledger()
    bob, config = business("bobs_plumbing")
    FAKE.update(cache_read=2000, cache_write=500)
    try:
        usage.serving(bob["id"], config)
        llm._create(purpose="classifier", max_tokens=10,
                    messages=[{"role": "user", "content": "my name is Pat, 555-0100"}])
    finally:
        usage.clear()
        FAKE.update(cache_read=0, cache_write=0)
    rows = db.usage_rows()
    row = rows[-1] if rows else {}
    check("one row", len(rows) == 1, rows)
    check("for Bob's, this business-local month, labelled",
          row.get("business_id") == bob["id"] and row.get("purpose") == "classifier"
          and row.get("month") == usage.month_for(bob["id"], config), row)
    check("with the API's token counts, cache reads and writes included",
          (row.get("input_tokens"), row.get("output_tokens"), row.get("cache_read_tokens"),
           row.get("cache_write_tokens")) == (1000, 200, 2000, 500), row)
    check("and their cost", row.get("micro_dollars") == usage.micro_dollars(
        llm.MODEL, 1000, 200, 2000, 500), row)
    check("no message text in it", "Pat" not in str(row) and "555" not in str(row))
    columns = {r[1] for r in db.get_connection().execute("PRAGMA table_info(usage_ledger)")}
    check("and no column that could hold any",
          not columns & {"message", "content", "text", "phone", "name", "sender"}, columns)


# ---------------------------------------------------------------------------
# 4. Over budget: the canned reply, no model call
# ---------------------------------------------------------------------------

def test_canned_reply_every_config():
    heading("the over-budget reply has a phone number for every business")
    for path in sorted((ROOT / "config").glob("*.yaml")):
        if path.stem in ("defaults", "personas"):
            continue
        config = load_config(str(path))
        reply = budget.over_budget_reply(config)
        phone = config["business"]["phone"]
        check(f"{path.stem}: '{reply}'", phone in reply and reply.strip())
    reply = budget.over_budget_reply({"business": {"name": "Pat's Diner"}})
    check("no phone: 'contact {name} directly'", "Pat's Diner directly" in reply, reply)
    check("no config at all: still a sentence", budget.over_budget_reply(None).strip())
    _, miller = business("miller_funeral")
    check("Miller has no hours: none made up",
          budget.over_budget_reply(miller).endswith(f"{miller['business']['phone']}."),
          budget.over_budget_reply(miller))


def test_over_budget_turns():
    heading("a business at its cap: customers get the phone, the model isn't asked")
    empty_ledger()
    # The door decides before any model code runs: the guarantee in
    # budget.check would also stop the call, but only after the turn had
    # started classifying and booking (and a reply built around the error).
    reached = []
    real_check = budget.check

    def watching(business_id=None):
        reached.append(business_id)
        return real_check(business_id)
    budget.check = watching
    for slug in ("bobs_plumbing", "belmont_hair_studio", "crosstown_pizza",
                 "ridgeline_contracting", "sunrise_bakery_and_cafe", "miller_funeral"):
        row, config = business(slug)
        hard, _ = budget.limits(row["id"], config)
        fill(row["id"], hard)
        before = len(SENT)
        reply = process_message("Do you have anything open Friday?", f"cust-{slug}",
                                row["id"], config, channel="webchat")
        check(f"{slug}: the phone number, no model call",
              config["business"]["phone"] in reply and len(SENT) == before, reply)

    row, config = business("miller_funeral")
    before = len(SENT)
    reply = process_message("My mother just passed away", "cust-urgent", row["id"],
                            config, channel="webchat")
    check("Miller's urgent rule still answers first, both numbers, no call",
          "924-5730" in reply and len(SENT) == before, reply)

    row, config = business("bobs_plumbing")
    db.set_state("cust-midbook", row["id"], "collecting",
                 pending={"service": "Drain cleaning"})
    before = len(SENT)
    reply = process_message("Tuesday at 10", "cust-midbook", row["id"], config,
                            channel="webchat")
    check("mid-booking: the phone, no booking question, no call",
          config["business"]["phone"] in reply and "?" not in reply
          and len(SENT) == before, reply)

    budget.check = real_check
    check("no turn got as far as the call's own check (the door stopped them)",
          not reached, reached)

    heading("under budget, the same turn reaches the model (the test has teeth)")
    empty_ledger()
    before = len(SENT)
    process_message("Do you have anything open Friday?", "cust-under", row["id"],
                    config, channel="webchat")
    check("the model was asked", len(SENT) > before)

    heading("through the web chat and the text simulator")
    fill(row["id"], budget.limits(row["id"])[0])
    client = app.test_client()
    before = len(SENT)
    r = client.post(f"/webchat/{row['slug']}", json={"message": "hi there", "session_id": "w1"})
    check("web chat: 200 and the phone", r.status_code == 200
          and config["business"]["phone"] in r.get_json()["reply"], (r.status_code, r.data))
    check("…not the portal's note", budget.MONTH_NOTE not in r.get_json()["reply"])
    check("…and no model call", len(SENT) == before)
    empty_ledger()


def test_crossing_turn():
    heading("the turn that crosses the cap finishes; the next gets the phone")
    empty_ledger()
    row, config = business("bobs_plumbing")
    hard, _ = budget.limits(row["id"], config)
    one_call = usage.micro_dollars(llm.MODEL, FAKE["in"], FAKE["out"]) / 1e6
    fill(row["id"], hard - one_call / 2)          # half a call left
    usage.serving(row["id"], config)
    check("admitted while under", budget.admit_turn(row["id"], config))
    try:
        for _ in range(3):
            llm._create(purpose="customer_reply", max_tokens=5,
                        messages=[{"role": "user", "content": "x"}])
        crossed = True
    except budget.Spent:
        crossed = False
    finally:
        usage.clear()
    check("three calls in the admitted turn all ran", crossed)
    check("the next turn is refused", not budget.admit_turn(row["id"], config))
    usage.clear()

    heading("the guarantee: a call nobody admitted is checked at the call")
    before = len(SENT)
    try:
        llm._create(purpose="classifier", business_id=row["id"], max_tokens=5,
                    messages=[{"role": "user", "content": "x"}])
        refused = False
    except budget.Spent as e:
        refused = str(e) == budget.MONTH_NOTE
    check("_create raises budget.Spent for a business over its month", refused)
    check("before anything is sent", len(SENT) == before)
    empty_ledger()


def test_portal():
    heading("the owner's AI features say why; the rest of the portal works")
    empty_ledger()
    row, config = business("sunrise_bakery_and_cafe")
    db.create_user("owner@sunrise.test", "pw-for-tests-123", business_id=row["id"])
    client = app.test_client()
    page = client.get("/admin/login").get_data(as_text=True)
    token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    client.post("/admin/login", data={"_csrf_token": token, "email": "owner@sunrise.test",
                                      "password": "pw-for-tests-123"})
    with client.session_transaction() as s:
        csrf = s.get("csrf_token")
    settings = client.get(f"/admin/business/{row['id']}/settings").get_data(as_text=True)
    check("Settings shows this month's use", "AI use this month" in settings
          and "$0.00 of your $10.00" in settings, settings[:200])
    fill(row["id"], budget.limits(row["id"])[0] + 1)
    before = len(SENT)
    r = client.post("/admin/help/ask", data={"_csrf_token": csrf,
                                             "question": "How do I add a menu item?"},
                    follow_redirects=True)
    check("the owner assistant shows the monthly limit", budget.MONTH_NOTE in
          html.unescape(r.get_data(as_text=True)), r.status_code)
    r = client.post(f"/admin/business/{row['id']}/knowledge/preview",
                    data={"_csrf_token": csrf, "questions": "Gluten free?"},
                    follow_redirects=True)
    check("Preview answers shows it too", budget.MONTH_NOTE in
          html.unescape(r.get_data(as_text=True)), r.status_code)
    check("neither asked the model", len(SENT) == before)
    for page in ("settings", "knowledge", "menu", "appointments"):
        r = client.get(f"/admin/business/{row['id']}/{page}")
        check(f"the {page} page still opens", r.status_code == 200, r.status_code)
    settings = client.get(f"/admin/business/{row['id']}/settings").get_data(as_text=True)
    check("and Settings says the limit is reached", "limit is reached" in settings)
    empty_ledger()


def test_operator_page():
    heading("the operator's AI spend page: every business, and budgets set there")
    empty_ledger()
    db.create_user("op@simon.test", "pw-for-tests-123", is_operator=True)
    client = app.test_client()
    page = client.get("/admin/login").get_data(as_text=True)
    token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    client.post("/admin/login", data={"_csrf_token": token, "email": "op@simon.test",
                                      "password": "pw-for-tests-123"})
    with client.session_transaction() as s:
        csrf = s.get("csrf_token")
    bob, _ = business("bobs_plumbing")
    fill(bob["id"], 2.5)
    page = html.unescape(client.get("/admin/usage").get_data(as_text=True))
    names = [b["name"] for b in db.get_all_businesses()]
    check("every business is listed", all(n in page for n in names), names)
    check("with this month's spend and share", "$2.5000" in page and "25%" in page)
    r = client.post(f"/admin/usage/{bob['id']}/budget",
                    data={"_csrf_token": csrf, "monthly_usd": "2", "alert_at": "1.5"})
    check("a budget is saved", budget.limits(bob["id"]) == (2.0, 1.5), budget.limits(bob["id"]))
    check("…and Bob's is now over it", budget.over_month(bob["id"]))
    client.post(f"/admin/usage/{bob['id']}/budget",
                data={"_csrf_token": csrf, "monthly_usd": "", "alert_at": ""})
    check("blank puts the default back",
          budget.limits(bob["id"]) == (budget.DEFAULT_MONTHLY,
                                       budget.DEFAULT_MONTHLY * budget.ALERT_SHARE))
    client.post(f"/admin/usage/{bob['id']}/budget",
                data={"_csrf_token": csrf, "monthly_usd": "lots"})
    check("nonsense isn't saved", budget.limits(bob["id"])[0] == budget.DEFAULT_MONTHLY)
    owner = app.test_client()
    check("an owner can't open it", owner.get("/admin/usage").status_code in (302, 403))
    empty_ledger()


# ---------------------------------------------------------------------------
# 5. Alerts
# ---------------------------------------------------------------------------

def test_soft_alert_once():
    heading("the soft limit alerts the operator once a month, not per message")
    empty_ledger()
    row, config = business("crosstown_pizza")
    _, soft = budget.limits(row["id"], config)
    fill(row["id"], soft - 0.0001)
    usage.serving(row["id"], config)
    try:
        for _ in range(5):
            llm._create(purpose="customer_reply", max_tokens=5,
                        messages=[{"role": "user", "content": "x"}])
    finally:
        usage.clear()
    filed = [r for r in requests_filed() if r["business_id"] == row["id"]]
    check("five calls past the alert: one request in the Support inbox", len(filed) == 1,
          [r["message"] for r in filed])
    check("…saying how much of what", filed and "monthly AI alert" in filed[0]["message"]
          and "$10.00" in filed[0]["message"], filed and filed[0]["message"])
    next_month = "2099-01"
    fill(row["id"], soft + 1, month=next_month)
    for _ in range(3):
        budget.after_spend(row["id"], next_month)
    filed = [r for r in requests_filed() if r["business_id"] == row["id"]]
    check("a new month alerts again, once", len(filed) == 2
          and next_month in filed[0]["message"], [r["message"] for r in filed])
    empty_ledger()


# ---------------------------------------------------------------------------
# 6. Evals never count
# ---------------------------------------------------------------------------

def test_evals():
    heading("eval calls are recorded but never count against a business")
    empty_ledger()
    row, config = business("ridgeline_contracting")
    usage.serving(row["id"], config)
    try:
        llm._create(purpose="eval", max_tokens=5, messages=[{"role": "user", "content": "x"}])
        usage.mark_eval_run()
        try:
            llm._create(purpose="customer_reply", max_tokens=5,
                        messages=[{"role": "user", "content": "x"}])
        finally:
            usage._eval_run = False
    finally:
        usage.clear()
    rows = db.usage_rows()
    check("both calls are in the ledger", len(rows) == 2, rows)
    check("as purpose 'eval', billable 0 (the call site kept as its label)",
          all(r["purpose"] == "eval" and r["billable"] == 0 for r in rows)
          and rows[1]["label"] == "customer_reply", rows)
    check("with their cost", all(r["micro_dollars"] > 0 for r in rows))
    check("and none of it counts against Ridgeline",
          db.month_spend(row["id"], usage.month_for(row["id"], config)) == 0)
    import judge
    real = os.environ.get("ANTHROPIC_API_KEY")
    import anthropic
    real_client = anthropic.Anthropic
    anthropic.Anthropic = lambda **k: types.SimpleNamespace(messages=FakeMessages())
    try:
        judge._call("Grade this")
    finally:
        anthropic.Anthropic = real_client
    last = db.usage_rows()[-1]
    check("the judge's calls go through the ledger too, as eval",
          last["purpose"] == "eval" and last["billable"] == 0, last)
    check("every *_eval.py marks itself an eval", not [
        p.name for p in ROOT.glob("*_eval.py") if "usage.mark_eval_run()" not in p.read_text()])
    empty_ledger()


# ---------------------------------------------------------------------------
# 7. Out of credit
# ---------------------------------------------------------------------------

def test_out_of_credit():
    heading("the API out of credit: the phone number, not a 500")
    empty_ledger()
    check("a 402 billing_error is recognised", usage.is_out_of_credit(out_of_credit()))
    check("an ordinary error isn't", not usage.is_out_of_credit(RuntimeError("timeout")))
    row, config = business("belmont_hair_studio")
    FAKE["raise"] = out_of_credit()
    try:
        reply = process_message("How much is a balayage?", "cust-credit", row["id"],
                                config, channel="webchat")
        check("process_message: the canned reply with the phone",
              reply == budget.over_budget_reply(config), reply)
        r = app.test_client().post(f"/webchat/{row['slug']}",
                                   json={"message": "Can I book a cut?", "session_id": "c2"})
        check("the web chat: 200, the phone number",
              r.status_code == 200 and config["business"]["phone"] in r.get_json()["reply"],
              (r.status_code, r.data[:200]))
        try:
            llm._create(purpose="classifier", max_tokens=5,
                        messages=[{"role": "user", "content": "x"}])
            raised = None
        except Exception as e:
            raised = e
        check("_create raises usage.OutOfCredit", isinstance(raised, usage.OutOfCredit), raised)
    finally:
        FAKE["raise"] = None
    alerts = [r for r in requests_filed() if "out of credit" in r["message"]]
    check("one operator alert this hour, not one per call", len(alerts) == 1,
          [r["message"][:60] for r in alerts])
    check("nothing recorded for the refused calls", db.usage_rows() == [])
    empty_ledger()


# ---------------------------------------------------------------------------
# 8. Who pays, in Preview's thread pool; and the history resent
# ---------------------------------------------------------------------------

def test_thread_pool():
    heading("Preview's thread pool charges the business that asked")
    import contextvars
    from concurrent.futures import ThreadPoolExecutor
    empty_ledger()
    row, config = business("sunrise_bakery_and_cafe")
    usage.serving(row["id"], config)

    def call():
        return llm._create(purpose="preview", max_tokens=5,
                           messages=[{"role": "user", "content": "x"}])
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pool.submit(contextvars.copy_context().run, call).result()
            pool.submit(call).result()            # how it was before: nobody
    finally:
        usage.clear()
    owners = [r["business_id"] for r in db.usage_rows()]
    check("a copied context is charged to Sunrise; a bare one to nobody",
          owners == [row["id"], None], owners)
    check("preview.py copies the context for every job",
          "contextvars.copy_context().run" in (ROOT / "preview.py").read_text())
    empty_ledger()


def test_history_cap():
    heading("how much conversation is resent with each reply")
    short = [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 90}
             for i in range(10)]
    check("a real conversation is sent whole (the longest seen: 962 characters)",
          llm.capped_history(short) == short)
    long = [{"role": "user" if i % 2 == 0 else "assistant", "content": "y" * 1000}
            for i in range(10)]
    kept = llm.capped_history(long)
    check("ten 1,000-character messages are cut to the newest 4",
          kept == long[-4:], len(kept))
    check("the newest message is always kept, however long",
          llm.capped_history([{"role": "user", "content": "z" * 9000}])[0]["content"]
          == "z" * 9000)


def main():
    test_scan()
    test_arithmetic()
    test_ledger()
    test_canned_reply_every_config()
    test_over_budget_turns()
    test_crossing_turn()
    test_soft_alert_once()
    test_evals()
    test_out_of_credit()
    test_thread_pool()
    test_history_cap()
    test_portal()
    test_operator_page()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

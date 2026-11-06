# usage.py — the one place a request goes to the model, and what it cost.
#
# Every model call in the app goes through send() (llm._create calls it;
# judge.py too, for its SDK and plain-HTTP paths). usage_test.py scans the
# code so nothing else calls messages.create or the API's URL. Each call is
# one row in usage_ledger: which business it was for, what for (purpose),
# the tokens the API reported, and their cost in millionths of a dollar.
# notes/spend_caps.md has the whole story.
#
# Who pays is named once, not threaded through every function: serving()
# sets it for what follows (budget.scope_request per request, process_message
# per customer turn). It's a ContextVar, so a thread pool that should charge
# the same business (Preview) copies it with contextvars.copy_context().
#
# Evals never count against a business: an eval script calls mark_eval_run(),
# and its calls are recorded as purpose "eval", billable 0.
#
# Bookkeeping never breaks a reply: recording fails quietly (a log line).
#
# Stdlib only at import (judge.py runs from evals and must stay light);
# db, clock, yaml and budget are imported where they're needed.

import logging
import os
from contextvars import ContextVar
from datetime import datetime

log = logging.getLogger("usage")

# Dollars per million tokens: input, output, cache write (5-minute), cache
# read. Prices will go stale; the token counts beside them won't. Check
# https://platform.claude.com/docs/en/about-claude/pricing (2026-11-04:
# writes are 1.25x input, reads 0.1x, except Opus 5.5's and Fable 5.1's
# reads). Matched by prefix, so a dated snapshot finds its model.
PRICES = {
    "claude-haiku-5-5":  (0.10, 0.50, 0.125, 0.01),
    "claude-haiku-4-5":  (1.00, 5.00, 1.25, 0.10),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
    "claude-sonnet-5":   (2.00, 10.00, 2.50, 0.20),
    "claude-opus-5-5":   (4.00, 20.00, 5.00, 0.20),
    "claude-fable-5-1":  (10.00, 50.00, 12.50, 0.25),
}

# {model: (input, output)} set by llm.py from LLM_PRICE_IN / LLM_PRICE_OUT,
# for measuring a model before it's in the table. Cache prices follow input.
PRICE_OVERRIDES = {}

_warned = set()


def prices_for(model):
    """(input, output, cache write, cache read) dollars per million tokens.
    A model the table doesn't know is priced as the dearest one it does,
    with a warning, so a new model can't read as free."""
    if model in PRICE_OVERRIDES:
        price_in, price_out = PRICE_OVERRIDES[model]
        return (price_in, price_out, price_in * 1.25, price_in * 0.1)
    known = next((v for k, v in PRICES.items() if (model or "").startswith(k)), None)
    if known:
        return known
    if model not in _warned:
        _warned.add(model)
        log.warning("No price for model %r: charged at the dearest known rate", model)
    return max(PRICES.values())


def micro_dollars(model, input_tokens=0, output_tokens=0, cache_read_tokens=0,
                  cache_write_tokens=0):
    """What one call cost, in millionths of a dollar (an integer).

    $1 per million tokens is one micro-dollar per token, so a price in
    nano-dollars per token is the price x 1000, a whole number for every
    price in the table; the sum is exact and rounds once, at the end."""
    nano = [round(p * 1000) for p in prices_for(model)]
    total = (input_tokens * nano[0] + output_tokens * nano[1]
             + cache_write_tokens * nano[2] + cache_read_tokens * nano[3])
    return (total + 500) // 1000


def tokens_of(usage):
    """The four token counts from a response's usage (an SDK object or the
    HTTP API's dict). Never estimated: what the API says it billed."""
    def get(name):
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)
        return value if isinstance(value, int) else 0
    return {"input_tokens": get("input_tokens"),
            "output_tokens": get("output_tokens"),
            "cache_read_tokens": get("cache_read_input_tokens"),
            "cache_write_tokens": get("cache_creation_input_tokens")}


# ---------------------------------------------------------------------------
# Who pays
# ---------------------------------------------------------------------------

_scope = ContextVar("usage_scope", default=None)
_eval_run = os.environ.get("USAGE_EVAL") == "1"


def mark_eval_run():
    """This process is an eval: every call it makes is purpose "eval"."""
    global _eval_run
    _eval_run = True


def eval_run():
    return _eval_run


def scope():
    """The dict naming who pays for what follows, or None."""
    return _scope.get()


def serving(business_id, config=None, **fields):
    """From here on, calls are for this business. The same business keeps
    what the request already worked out about it (its demo, its visitor);
    another one starts fresh."""
    current = _scope.get()
    if current is not None and current.get("business_id") == business_id:
        current.update(fields)
        if config is not None:
            current["config"] = config
        return current
    fresh = dict(fields, business_id=business_id, config=config)
    _scope.set(fresh)
    return fresh


def clear():
    """Nobody pays: the end of a request (budget.end_request)."""
    _scope.set(None)


def current_business():
    s = _scope.get()
    return s.get("business_id") if s else None


def _timezone_config(business_id):
    """A config with the business's time zone, read from its YAML without
    loading the rest (for months counted outside a turn)."""
    try:
        import yaml
        from pathlib import Path
        from db import get_business_by_id
        row = get_business_by_id(business_id)
        raw = yaml.safe_load(Path(row["config_path"]).read_text()) if row else None
        return raw or {}
    except Exception:
        return {}


def month_for(business_id=None, config=None):
    """The business-local month, "YYYY-MM": a business's budget starts over
    at its own midnight on the 1st, not the server's."""
    import clock
    if config is None:
        s = _scope.get()
        if s and s.get("business_id") == business_id and s.get("config"):
            config = s["config"]
        elif business_id is not None:
            config = _timezone_config(business_id)
    return clock.business_now(config).strftime("%Y-%m")


# ---------------------------------------------------------------------------
# Sending, and recording what it cost
# ---------------------------------------------------------------------------

class OutOfCredit(Exception):
    """The API refused the call for money: the account's credit balance is
    empty or its spend limit is reached. Every business is silent at once."""


def is_out_of_credit(error):
    """A 402 billing_error, or the 400 that says the balance is too low."""
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    kind = getattr(error, "type", None)
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        kind = kind or (body.get("error") or {}).get("type")
    text = str(error).lower()
    return (status == 402 or kind == "billing_error"
            or "credit balance" in text or "spend limit" in text
            or "usage limit" in text)


def _refused_for_money(error):
    """Log it, alert the operator (once an hour), and say so as OutOfCredit."""
    kind = getattr(error, "type", None) or type(error).__name__
    log.error("The API refused a call for money (%s, status %s): every business "
              "gets the call-us reply until the account has credit",
              kind, getattr(error, "status_code", None))
    s = _scope.get()
    if s is not None:
        s["refused"] = True
    try:
        import budget
        budget.alert_out_of_credit(kind)
    except Exception as e:
        log.warning("Couldn't alert the operator about credit (%s)", e)
    return OutOfCredit(kind)


def send(client, call, *, purpose, business_id=None):
    """messages.create, recorded in the ledger. The only call to it."""
    try:
        response = client.messages.create(**call)
    except Exception as e:
        if is_out_of_credit(e):
            raise _refused_for_money(e) from e
        raise
    model = getattr(response, "model", None)
    record(getattr(response, "usage", None),
           model=model if isinstance(model, str) else call.get("model"),
           purpose=purpose, business_id=business_id)
    return response


def send_http(body, api_key, *, purpose, timeout=60):
    """The same call over plain HTTP, for judge.py without the SDK."""
    import json
    import urllib.error
    import urllib.request
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={"x-api-key": api_key or "", "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as r:
            answer = json.load(r)
    except urllib.error.HTTPError as e:
        if is_out_of_credit(e):
            raise _refused_for_money(e) from e
        raise
    record(answer.get("usage"), model=answer.get("model") or body.get("model"),
           purpose=purpose)
    return answer


def record(usage, *, model, purpose, business_id=None, label=None):
    """One call in the ledger; returns its cost in micro-dollars (0 if it
    couldn't be recorded). Billable unless this is an eval."""
    if usage is None:
        return 0
    try:
        tokens = tokens_of(usage)
        cost = micro_dollars(model, **tokens)
        if business_id is None:
            business_id = current_business()
        billable = not (_eval_run or purpose == "eval")
        month = month_for(business_id)
        from db import add_usage
        add_usage(business_id=business_id, month=month,
                  created_at=datetime.now().isoformat(timespec="seconds"),
                  purpose="eval" if not billable else purpose,
                  label=label or purpose, billable=1 if billable else 0,
                  model=model or "unknown", micro_dollars=cost, **tokens)
        log.debug("%s for business %s: %s, $%.6f", purpose, business_id, tokens,
                  cost / 1e6)
        if billable:
            import budget
            budget.record(cost / 1e6)          # a demo's day, its visitor's
            if business_id is not None:
                budget.after_spend(business_id, month)
        return cost
    except Exception as e:
        log.warning("Couldn't record a %s call (%s: %s)", purpose,
                    type(e).__name__, e)
        return 0

# budget.py — a daily cap on what the model may cost for demos.
#
# Rate limits count requests; this counts dollars. A tester costs about 5-10
# cents (2026-10-20, from the cost lines in the log: a chat message $0.001 to
# $0.004, the owner assistant and Preview about $0.001, photo proposals up to
# $0.01), and the rate limits let one address spend about $1.50 a day on
# chat. Across many visitors, nothing stopped the spend but the Anthropic
# account limit, and when that was reached on 2026-10-20 the bot stopped for
# everyone, real businesses included.
#
# Two budgets, demos only (a real business is never capped):
#   each demo   DEMO_DAILY_BUDGET   $0.50 a day, keyed by its slug (random,
#               never reused; ids are, after a sweep)
#   all demos   DEMOS_DAILY_BUDGET  $5 a day, so a crowd can't spend the
#               account down to where real businesses lose their bot
#
# Two layers:
#   the door    each route that uses the model asks refusal() before it
#               starts, beside its rate-limit check, and answers with the
#               note in its own shape (a chat reply, a flash message).
#   the call    llm._create calls check() before every request to the model
#               and record() after. It's the guarantee: a path that missed
#               the door still can't spend. budget_test.py sweeps the routes
#               to find any that do.
#
# The request's demo, and whether it may use the model, are worked out once,
# before the view runs (scope_request: the slug or business id in the URL,
# else the signed-in owner's business), and kept in flask.g. Once, not per
# call: a chat message makes several calls (understand, then reply), and
# checked before each, the budget ran out between them and the message that
# crossed it got "Sorry, I'm having trouble right now" (2026-10-20, in the
# browser). A request that starts under budget finishes; the overshoot is at
# most one request, about half a cent. Outside a request (evals, scripts)
# nothing is capped or counted.
#
# Like ratelimit.py, it fails OPEN: if the table can't be read, the request
# goes through. A broken counter shouldn't take the demos down.

import logging
import os
from datetime import datetime, timezone

from flask import g, has_request_context, request

log = logging.getLogger("budget")

DEMO_DAILY = float(os.environ.get("DEMO_DAILY_BUDGET", "0.50"))
DEMOS_DAILY = float(os.environ.get("DEMOS_DAILY_BUDGET", "5.00"))

ALL_DEMOS = "all-demos"

NOTE = ("This demo has used its share of the assistant for today. Start a "
        "fresh demo tomorrow, or send us feedback: we read every message.")
ALL_NOTE = ("The demos have used their share of the assistant for today. "
            "Please come back tomorrow, or send us feedback: we read every "
            "message.")


class Spent(Exception):
    """A model call refused because a demo's budget for today is used up."""


def _day():
    return datetime.now(timezone.utc).date().isoformat()


def _key(slug):
    return f"demo:{slug}"


def scope_request():
    """Before each request: which demo, if any, this request spends for, and
    the note to answer with if that demo may not use the model today."""
    g.budget_demo = g.budget_note = None
    if request.endpoint in (None, "static"):
        return
    from db import get_business_by_id, get_business_by_slug
    args = request.view_args or {}
    try:
        if "slug" in args:
            business = get_business_by_slug(args["slug"])
        elif "business_id" in args:
            business = get_business_by_id(args["business_id"])
        else:
            from admin.auth import current_user
            user = current_user()
            business = (get_business_by_id(user["business_id"])
                        if user and user["business_id"] else None)
    except Exception as e:
        log.warning("Couldn't tell which business this request is for (%s)", e)
        return
    if business and business.get("is_demo"):
        g.budget_demo = business["slug"]
        g.budget_note = _over(business["slug"])


def _demo():
    return g.get("budget_demo") if has_request_context() else None


def _over(demo):
    """The note if this demo, or all demos, have spent today's budget."""
    from db import get_model_spend
    day = _day()
    try:
        if get_model_spend(ALL_DEMOS, day) >= DEMOS_DAILY * 1e6:
            log.info("All demos have spent their $%g for %s", DEMOS_DAILY, day)
            return ALL_NOTE
        if get_model_spend(_key(demo), day) >= DEMO_DAILY * 1e6:
            log.info("Demo %s has spent its $%g for %s", demo, DEMO_DAILY, day)
            return NOTE
    except Exception as e:
        log.warning("Budget unavailable (%s) — allowing", e)
    return None


def refusal():
    """The note to answer with instead of using the model, or None. Decided
    as the request started (scope_request), so every call in it agrees."""
    return g.get("budget_note") if has_request_context() else None


def check():
    """Raise Spent if this request's demo may not use the model now."""
    note = refusal()
    if note:
        raise Spent(note)


def record(dollars):
    """Count one model call's cost against this request's demo and all demos."""
    demo = _demo()
    if not demo or dollars <= 0:
        return
    from db import add_model_spend
    day = _day()
    try:
        add_model_spend(_key(demo), day, dollars * 1e6)
        add_model_spend(ALL_DEMOS, day, dollars * 1e6)
    except Exception as e:
        log.warning("Couldn't record $%.5f for demo %s (%s)", dollars, demo, e)

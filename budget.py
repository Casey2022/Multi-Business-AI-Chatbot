# budget.py — caps on what the model may cost: a day's for demos, a
# month's for every business.
#
# The daily caps, demos only. Rate limits count requests; this counts
# dollars. A tester costs about 5-10 cents (2026-10-20, from the cost lines
# in the log: a chat message $0.001 to $0.004, the owner assistant and
# Preview about $0.001, photo proposals up to $0.01), and the rate limits
# let one address spend about $1.50 a day on chat. Across many visitors,
# nothing stopped the spend but the Anthropic account limit, and when that
# was reached on 2026-10-20 the bot stopped for everyone, real businesses
# included.
#
#   each demo   DEMO_DAILY_BUDGET   $0.50 a day, keyed by its slug (random,
#               never reused; ids are, after a sweep)
#   each visitor VISITOR_DAILY_BUDGET $1 a day, keyed by the visitor's
#               address (ratelimit.client_address), across every demo they
#               start. Without it, one visitor starting demo after demo spent
#               the whole all-demos day, and everyone else got ALL_NOTE (2026-
#               10-24, offline: 8 demos). A tester spends 5-10 cents, so $1
#               leaves room for a classroom on one Wi-Fi.
#   all demos   DEMOS_DAILY_BUDGET  $5 a day, so a crowd can't spend the
#               account down to where real businesses lose their bot
#
# The monthly cap, every business (2026-11-04, notes/spend_caps.md): the
# usage_ledger's billable rows for the business-local month against
# budget.monthly_usd (hard) and budget.alert_at (soft: one alert to the
# operator). Set by the operator (AI spend page), else the YAML's budget:,
# else DEFAULT_MONTHLY_BUDGET_USD; never unlimited. Normal use is small (a
# customer conversation costs about a cent); the cap is for a script
# hammering a chat, a loop bug, or the account running dry. Over it:
#   a customer   gets over_budget_reply: no model call, and always a phone
#                number (conversation.process_message, after the urgent
#                rules, before the booking flow)
#   the owner    gets MONTH_NOTE from the portal's model features; every
#                other page works
#
# Two layers:
#   the door    each route that uses the model asks refusal() before it
#               starts, beside its rate-limit check, and answers with the
#               note in its own shape (a chat reply, a flash message). The
#               customer routes are the exception for the monthly cap:
#               process_message decides that, so an urgent rule still
#               answers and the reply is the phone number, not a note.
#   the call    llm._create calls check() before every request to the model,
#               and usage.record charges it after. It's the guarantee: a path
#               that missed the door still can't spend. budget_test.py sweeps
#               the routes to find any that do.
#
# Whether a request (or a customer turn) may use the model is worked out
# once, before it starts (scope_request: the slug or business id in the URL,
# else the signed-in owner's business), and kept in usage's scope, which a
# thread pool copies (Preview). Once, not per call: a chat message makes
# several calls (understand, then reply), and checked before each, the
# budget ran out between them and the message that crossed it got "Sorry,
# I'm having trouble right now" (2026-10-20, in the browser). A request that
# starts under budget finishes; the overshoot is at most one request, about
# half a cent. Outside a request or turn (evals, scripts) the daily caps
# don't apply; the monthly one is checked at the call, except for evals.
#
# Like ratelimit.py, it fails OPEN: if the table can't be read, the request
# goes through. A broken counter shouldn't take the business down.

import logging
import os
from datetime import datetime, timezone

from flask import request

import usage

log = logging.getLogger("budget")

DEMO_DAILY = float(os.environ.get("DEMO_DAILY_BUDGET", "0.50"))
DEMOS_DAILY = float(os.environ.get("DEMOS_DAILY_BUDGET", "5.00"))
VISITOR_DAILY = float(os.environ.get("VISITOR_DAILY_BUDGET", "1.00"))
DEFAULT_MONTHLY = float(os.environ.get("DEFAULT_MONTHLY_BUDGET_USD", "10.00"))
ALERT_SHARE = 0.8           # the soft limit when none is set

ALL_DEMOS = "all-demos"

NOTE = ("This demo has used its share of the assistant for today. Start a "
        "fresh demo tomorrow, or send us feedback: we read every message.")
VISITOR_NOTE = ("You've used your share of the demo assistant for today. "
                "Please come back tomorrow, or send us feedback: we read every "
                "message.")
ALL_NOTE = ("The demos have used their share of the assistant for today. "
            "Please come back tomorrow, or send us feedback: we read every "
            "message.")
MONTH_NOTE = ("This business has reached its monthly AI limit, so the AI "
              "features are paused until next month. Everything else in the "
              "portal still works. To raise the limit, ask us through Help & "
              "feedback.")

# The routes a customer writes to: over the monthly cap they still answer
# (process_message gives the phone number), so they don't get MONTH_NOTE.
CUSTOMER_ENDPOINTS = {"webchat_reply", "textsim_reply", "sms_reply"}


class Spent(Exception):
    """A model call refused because a budget is used up."""


def _day():
    return datetime.now(timezone.utc).date().isoformat()


def _key(slug):
    return f"demo:{slug}"


def _visitor_key(address):
    return f"visitor:{address}"


def _address():
    """The visitor's address, as the rate limits read it (not the first
    X-Forwarded-For entry, which the visitor writes)."""
    import ratelimit
    return ratelimit.client_address(request.headers.get("X-Forwarded-For", ""),
                                    request.remote_addr)


# ---------------------------------------------------------------------------
# The monthly budget
# ---------------------------------------------------------------------------

def _dollars(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _yaml_budget(business_id):
    try:
        return (usage._timezone_config(business_id) or {}).get("budget") or {}
    except Exception:
        return {}


def limits(business_id, config=None):
    """(hard, soft) dollars a month: the operator's setting, else the
    YAML's budget:, else DEFAULT_MONTHLY_BUDGET_USD (soft: 80% of hard)."""
    from db import get_config_overrides
    try:
        chosen = get_config_overrides(business_id).get("budget") or {}
    except Exception:
        chosen = {}
    from_yaml = ((config or {}).get("budget") if config is not None
                 else _yaml_budget(business_id)) or {}
    hard = (_dollars(chosen.get("monthly_usd")) or _dollars(from_yaml.get("monthly_usd"))
            or DEFAULT_MONTHLY)
    soft = (_dollars(chosen.get("alert_at")) or _dollars(from_yaml.get("alert_at"))
            or hard * ALERT_SHARE)
    return hard, min(soft, hard)


def month_state(business_id, config=None, month=None):
    """This month (or `month`) for a business: spent, hard and soft limits
    (dollars), the share used, and whether it's over."""
    from db import month_spend
    month = month or usage.month_for(business_id, config)
    hard, soft = limits(business_id, config)
    spent = month_spend(business_id, month) / 1e6
    return {"month": month, "spent": spent, "budget": hard, "alert_at": soft,
            "share": spent / hard if hard else 0, "over": spent >= hard}


def over_month(business_id, config=None):
    """True if this business has spent its month's budget. Fails open."""
    if business_id is None:
        return False
    try:
        state = month_state(business_id, config)
    except Exception as e:
        log.warning("Monthly budget unavailable for %s (%s) — allowing", business_id, e)
        return False
    if state["over"]:
        log.info("Business %s has spent its $%g for %s", business_id,
                 state["budget"], state["month"])
        _alert(business_id, state["month"], "limit", state)
    return state["over"]


def over_budget_reply(config):
    """What a customer gets instead of the model: never empty, and a phone
    number whenever the config has one. Miller has no stated hours, so the
    hours are added only when there are some."""
    business = (config or {}).get("business") or {}
    phone = (business.get("phone") or "").strip()
    name = (business.get("name") or "").strip()
    if not phone:
        return (f"Sorry, I can't answer messages here right now. Please contact "
                f"{name or 'the business'} directly.")
    hours = (business.get("hours") or "").strip()
    return (f"Sorry, I can't answer messages here right now. Please call us at "
            f"{phone}" + (f" ({hours})" if hours else "") + ".")


def admit_turn(business_id, config):
    """process_message's door: True if this customer turn may use the model.
    Decided once; a turn that starts under budget finishes."""
    usage.serving(business_id, config, refused=False)
    over = over_month(business_id, config)
    usage.serving(business_id, config, admitted=not over)
    return not over


def turn_refused():
    """True if a call in this turn was refused for money (out of credit, or
    the guarantee in check()), whatever the code around it made of that."""
    s = usage.scope()
    return bool(s and s.get("refused"))


# ---------------------------------------------------------------------------
# Per request
# ---------------------------------------------------------------------------

def scope_request():
    """Before each request: which business this request spends for, and the
    note to answer with if it may not use the model."""
    usage.clear()
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
    if not business:
        return
    s = usage.serving(business["id"])
    if business.get("is_demo"):
        s["demo"] = business["slug"]
        s["visitor"] = _address()
        s["note"] = _over(business["slug"], s["visitor"])
    over = over_month(business["id"])
    s["admitted"] = not over
    if over and not s.get("note") and request.endpoint not in CUSTOMER_ENDPOINTS:
        s["note"] = MONTH_NOTE


def end_request(_error=None):
    """After each request: nobody pays for what this thread does next."""
    usage.clear()


def _demo():
    s = usage.scope()
    return s.get("demo") if s else None


def _over(demo, visitor=None):
    """The note if this demo, this visitor, or all demos have spent today's
    budget."""
    from db import get_model_spend
    day = _day()
    try:
        if get_model_spend(ALL_DEMOS, day) >= DEMOS_DAILY * 1e6:
            log.info("All demos have spent their $%g for %s", DEMOS_DAILY, day)
            return ALL_NOTE
        if visitor and get_model_spend(_visitor_key(visitor), day) >= VISITOR_DAILY * 1e6:
            log.info("A visitor has spent their $%g for %s", VISITOR_DAILY, day)
            return VISITOR_NOTE
        if get_model_spend(_key(demo), day) >= DEMO_DAILY * 1e6:
            log.info("Demo %s has spent its $%g for %s", demo, DEMO_DAILY, day)
            return NOTE
    except Exception as e:
        log.warning("Budget unavailable (%s) — allowing", e)
    return None


def refusal():
    """The note to answer with instead of using the model, or None. Decided
    as the request started (scope_request), so every call in it agrees."""
    s = usage.scope()
    return s.get("note") if s else None


def check(business_id=None):
    """Raise Spent if this call may not use the model now.

    The request's (or turn's) decision when it's for the same business;
    otherwise (a script, a call for another business) the ledger now.
    Evals aren't stopped here: their own calls never count, and the door
    in process_message is what the over-budget journey tests."""
    s = usage.scope()
    note = s.get("note") if s else None
    if note:
        raise Spent(note)
    if business_id is None:
        business_id = s.get("business_id") if s else None
    if business_id is None:
        return
    if s and s.get("business_id") == business_id and "admitted" in s:
        over = not s["admitted"]
    elif usage.eval_run():
        return
    else:
        over = over_month(business_id)
    if over:
        if s is not None:
            s["refused"] = True
        raise Spent(MONTH_NOTE)


def record(dollars):
    """Count one model call's cost against this request's demo, its visitor,
    and all demos (the daily caps; the ledger has the monthly)."""
    demo = _demo()
    if not demo or dollars <= 0:
        return
    from db import add_model_spend
    day = _day()
    try:
        add_model_spend(_key(demo), day, dollars * 1e6)
        add_model_spend(ALL_DEMOS, day, dollars * 1e6)
        visitor = usage.scope().get("visitor")
        if visitor:
            add_model_spend(_visitor_key(visitor), day, dollars * 1e6)
    except Exception as e:
        log.warning("Couldn't record $%.5f for demo %s (%s)", dollars, demo, e)


# ---------------------------------------------------------------------------
# Alerts to the operator: the Support inbox, and email when it's set up
# ---------------------------------------------------------------------------

def after_spend(business_id, month):
    """After a billable call: the soft-limit alert, once a month."""
    try:
        state = month_state(business_id, month=month)
        if state["spent"] >= state["alert_at"]:
            _alert(business_id, month, "soft", state)
    except Exception as e:
        log.warning("Couldn't check business %s against its alert (%s)", business_id, e)


def _alert(business_id, period, kind, state=None):
    """File one alert for (business, period, kind); later ones are dropped."""
    from db import claim_usage_alert, get_business_by_id
    try:
        if not claim_usage_alert(business_id, period, kind):
            return False
        business = get_business_by_id(business_id) if business_id else None
        name = (business or {}).get("name") or "the account"
        if kind == "out_of_credit":
            message = (f"The Anthropic API refused a call for money ({state}): the "
                       f"account is out of credit or at its spend limit. Every "
                       f"business is answering customers with its phone number "
                       f"until it has credit again (Claude Console: Billing, Limits).")
        else:
            what = ("reached its monthly AI limit: customers are given the "
                    "phone number and the portal's AI features are paused"
                    if kind == "limit" else "passed its monthly AI alert")
            message = (f"{name} has {what}. This month ({period}): "
                       f"${state['spent']:.2f} of ${state['budget']:.2f} "
                       f"(alert at ${state['alert_at']:.2f}). Raise it on the AI "
                       f"spend page if that's expected.")
        import support
        support.file_request(None, business, message, None, "/admin/usage", None)
        log.warning("Usage alert (%s) for business %s, %s", kind, business_id, period)
        return True
    except Exception as e:
        log.warning("Couldn't file the %s alert for business %s (%s)", kind, business_id, e)
        return False


def alert_out_of_credit(kind):
    """At most one alert an hour for the whole account (business 0)."""
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    return _alert(0, hour, "out_of_credit", kind)

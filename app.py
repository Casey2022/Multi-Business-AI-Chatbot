# app.py — Flask webhook server and channel adapters.
#
# Architecture:
#   Channel endpoints (/sms, future /webchat/<slug>) are thin adapters.
#   They parse channel-specific input, identify the business, load config,
#   then call process_message() — the channel-agnostic brain.
#   process_message() returns reply text; the endpoint formats it for its
#   channel (TwiML, JSON, etc.) and responds.
#
# Adding a new channel = one new endpoint + one call to process_message().
# The brain never changes.

from dotenv import load_dotenv
load_dotenv()  # Must run before any module reads os.environ

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

import logging
import os
from datetime import timedelta

# Logging is configured BEFORE our own modules are imported, and that order
# is load-bearing. rag.py announces its embedding backend at import time, so
# that line is emitted *during* the import statement below. Anything logged
# before handlers exist falls to logging's last-resort handler, which passes
# only WARNING and above — so the line vanished with no error to explain it.
from logging_setup import setup_logging, new_turn, scrub
setup_logging()

from flask import (Flask, request, render_template, redirect, session,
                   url_for)
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator

from config import load_config
from db import (
    init_db,
    save_message,
    get_recent_messages,
    get_business_by_number,
    get_business_by_slug,
)
from rules import get_reply, BOOK_INTENT
from llm import get_llm_reply, start_turn_accounting, turn_cost_summary
from scheduler import handle_booking, is_mid_booking
from phone_utils import normalize as normalize_phone
from admin import admin_bp

log_boot = logging.getLogger("bootstrap")
log_sec = logging.getLogger("security")
log = logging.getLogger("app")
log_web = logging.getLogger("webchat")
log_cost = logging.getLogger("cost")

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
def bootstrap():
    """Prepare everything the app needs to serve requests.

    Idempotent by design: safe to run on every boot. Creates tables, clears
    out demo tenants left by the last run, registers any seed business that
    isn't in the database yet, seeds an operator account if no users exist,
    imports a knowledge base for any business without one, and ingests each
    business's documents if their vector collection is missing.

    Every step is 'do this if it isn't already done' rather than 'do this if
    the database looks brand new'. That distinction is the whole point: the
    old version only seeded an EMPTY registry, so three businesses added to
    the seed list never appeared on a machine that already had two, and
    nothing anywhere said so. Setup steps that only work on a fresh install
    aren't setup steps, they're a trap for the second install.
    """
    init_db()

    # Demo tenants don't survive a restart. Their visitor's browser session
    # is gone, their chat history means nothing to anyone, and on an
    # ephemeral filesystem any collection they published was lost with the
    # disk — so a demo that outlived the process is a row that will never be
    # used again and would otherwise sit there until the idle sweep notices.
    try:
        from demo import sweep_all
        # drop_collections=False: this runs in gunicorn's manager process,
        # which must never touch ChromaDB (see vector_boot.py). The orphaned
        # demo collections are dropped by vector_boot instead.
        sweep_all(drop_collections=False)
    except Exception as e:
        # Tidying is not worth failing a boot over.
        log_boot.warning(f"Could not clear old demos: {e}")

    # Register any business in the seed list that isn't here yet. This used
    # to run only when the table was completely EMPTY, which meant adding a
    # new business to the list did nothing on a machine that already had
    # one — the row never appeared, nothing said why, and the only way to
    # find out was to query the database by hand. Seeding is idempotent now,
    # so a restart is all it takes.
    from db import get_all_businesses
    from seed_businesses import seed
    added = seed(quiet=True)
    if added:
        log_boot.info("Registered %d new business(es): %s",
                      len(added), ", ".join(added))
    businesses = get_all_businesses()

    # Seed a default operator if no users exist. The deployed filesystem is
    # ephemeral, so the database is rebuilt on every boot — without this the
    # admin portal would have no accounts and be unreachable.
    from db import get_connection, create_user
    conn = get_connection()
    user_count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    conn.close()

    if user_count == 0:
        email    = os.environ.get("ADMIN_EMAIL")
        password = os.environ.get("ADMIN_PASSWORD")
        if email and password:
            # create_user enforces the password policy. A weak ADMIN_PASSWORD
            # must not take the whole app down on boot -- it should fail the
            # same way missing credentials do: loudly, with the portal left
            # unseeded until the environment is corrected.
            try:
                create_user(email, password, business_id=None, is_operator=True)
                log_boot.info(f"Seeded operator account: {email}")
            except ValueError as e:
                log_boot.warning("ADMIN_PASSWORD was rejected (%s) -- the admin "
                                 "portal is unreachable until it is set to a "
                                 "stronger value and the app restarted.", e)
        else:
            log_boot.warning("No users exist and ADMIN_EMAIL / "
                  "ADMIN_PASSWORD are not set — the admin portal is "
                  "unreachable.")

    from scheduling import (unknown_duration_services,
                            unknown_condition_services)
    from import_documents import import_for_business
    for b in businesses:
        if not b["active"]:
            continue
        # A demo clone shares its template's knowledge base until it
        # publishes. sweep_all() above should already have deleted every
        # demo, but that call is wrapped in a try/except that swallows, so
        # skip them here too rather than depend on the sweep having worked.
        # (vector_boot.ingest_all skips them for the same reason.)
        if b["is_demo"]:
            continue
        try:
            config = load_config(b["config_path"], b["id"])

            # A business with no knowledge base yet gets one from its seed
            # Markdown. Registering a business and giving it something to
            # know are two halves of the same job; leaving the second half
            # as a command to remember is how one goes live able to book
            # appointments but unable to answer a single question about
            # itself. No-op once the table has sections — the database is
            # authoritative from then on.
            imported = import_for_business(b, config)
            if imported:
                log_boot.info("Imported %d knowledge section(s) for %s",
                              imported, b["name"])
            # A per-service duration whose key names no service does nothing
            # at all — the booking silently takes the default length. Said
            # once at startup, it's a typo; left unsaid, it's a stylist
            # wondering why her afternoon keeps getting double-booked.
            stale = unknown_duration_services(config)
            if stale:
                log_boot.warning(
                    "%s has service_durations for services it doesn't "
                    "offer: %s — those bookings will use the default length",
                    b["name"], ", ".join(repr(s) for s in stale))
            # Same failure one layer over: a question conditioned on a
            # service that was renamed is never asked again, for anyone,
            # and the owner's only clue is an answer that stopped arriving.
            orphaned = unknown_condition_services(config)
            if orphaned:
                log_boot.warning(
                    "%s has booking questions set for services it doesn't "
                    "offer: %s — those questions will never be asked",
                    b["name"],
                    ", ".join(f"{key} -> {name!r}" for key, name in orphaned))
        except Exception as e:
            # A broken config shouldn't stop the server from starting —
            # that business just won't be fully set up until it's fixed.
            log_boot.warning(f"Setup failed for {b['name']}: {e}")

    # The vector collections are built in a separate process, never here.
    # With gunicorn --preload (Render's default) this function runs in the
    # manager process, and a ChromaDB client created here is copied into
    # every worker, where the first search freezes. See vector_boot.py.
    from vector_boot import run_in_subprocess
    run_in_subprocess()

    log_boot.info(f"Ready — {len(businesses)} business(es) registered.")
    
app = Flask(__name__)

# ---------------------------------------------------------------------------
# Session security
# ---------------------------------------------------------------------------
#
# The secret key signs the session cookie, and the session cookie is the only
# thing separating a stranger from the admin portal. This used to fall back
# to the literal string "dev-secret-change-in-production" — which lives in a
# public GitHub repository. Anyone who noticed could have signed a cookie
# claiming to be the operator and had write access to every business, and
# nothing would have looked wrong from the outside.
#
# So it fails closed. A missing secret is a configuration error, and a
# configuration error should stop the program rather than let it serve
# requests it can't actually secure. A deployment that refuses to boot is a
# problem you find in a minute; a deployment signing cookies with a public
# string is one you find when someone tells you.
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY is not set. It signs the session cookie, so without it "
        "anyone could forge a login. Generate one with:\n"
        "    python3 -c 'import secrets; print(secrets.token_hex(32))'\n"
        "and set it in .env locally, or in the environment on the host."
    )
app.secret_key = SECRET_KEY

app.config.update(
    # Never send the session cookie over plain HTTP. Off by default in
    # Flask; the deployed site is HTTPS-only, and on a local HTTP dev server
    # this would stop logins working, hence the env switch.
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "true").lower() == "true",
    # Not readable from JavaScript. Already Flask's default — set explicitly
    # so a future config change can't quietly turn it off.
    SESSION_COOKIE_HTTPONLY=True,
    # Not sent on cross-site POSTs. This is real CSRF defence in modern
    # browsers, and it does not replace a token — an older browser, or a
    # same-site subdomain, gets none of it.
    SESSION_COOKIE_SAMESITE="Lax",
    # A signed-in session goes stale after a day of inactivity. Sessions
    # were browser-session cookies with no expiry at all, so an owner who
    # signed in on a shared machine stayed signed in until it rebooted.
    PERMANENT_SESSION_LIFETIME=timedelta(
        hours=int(os.environ.get("SESSION_HOURS", "24"))),
)

# Secure-by-default is right for the deployed site and a trap for local
# development: over plain http://127.0.0.1 the browser silently refuses to
# store the cookie, so logging in appears to work and then doesn't stick,
# with nothing on screen or in the log explaining it. Say so at boot.
if app.config["SESSION_COOKIE_SECURE"]:
    log_boot.info("Session cookies are HTTPS-only. Testing over plain "
                  "http://127.0.0.1? Set COOKIE_SECURE=false in .env or "
                  "logins won't stick.")

init_db()          # tables only — no ChromaDB access before fork
app.register_blueprint(admin_bp)

# Run the startup sequence.
#
# This call has to be here, at module scope. gunicorn imports `app:app` and
# then serves; it never calls anything else, and neither does the dev
# server. So bootstrap() — which registers businesses, imports their
# knowledge bases and ingests their vector collections — was defined,
# documented, maintained, and never once executed. Everything it promises
# to do automatically was in fact being done by hand, and the only symptom
# was a business whose bot knew nothing about it.
#
# Safe with or without gunicorn --preload, and Render turns --preload ON
# (its GUNICORN_CMD_ARGS default, invisible in the dashboard). This module
# may be imported in the manager process and copied into workers, so
# nothing here may create a ChromaDB client: bootstrap() builds the
# collections in a separate process (vector_boot.py), and workers open
# their own client on their first search. Until 2026-09-26 this comment
# said the Procfile didn't use --preload, which was true and irrelevant:
# Render doesn't read the Procfile's flags, and adds its own.
#
# Wrapped because a business with a bad config should cost that business
# its RAG, not cost everyone the server.
try:
    bootstrap()
except Exception as e:
    log_boot.exception("Bootstrap failed — serving anyway: %s", e)

# What startup cost, in memory. Render's free plan has no metrics page, so
# the log is the only place to see how close the worker starts to its limit.
try:
    import resources
    log_boot.info("After startup: %s", resources.summary())
except Exception:
    pass

# The one thing startup must not leave behind. If a ChromaDB client exists
# now, something in bootstrap() opened one in what may be gunicorn's
# manager process, and every worker's first search will freeze.
try:
    import rag as _rag
    if _rag._chroma_client is not None:
        log_boot.error("Startup opened a ChromaDB client in process %s. Under "
                       "gunicorn --preload every worker will inherit it and "
                       "hang on its first search. Move that call into "
                       "vector_boot.py.", _rag._chroma_pid)
except Exception:
    pass

# ---------------------------------------------------------------------------
# Security — Twilio webhook signature verification
# ---------------------------------------------------------------------------

_twilio_validator = RequestValidator(os.environ.get("TWILIO_AUTH_TOKEN", ""))

# Dev bypass: set ALLOW_UNSIGNED_REQUESTS=true in .env to accept curl tests.
# Default is False — unsigned requests are rejected unless explicitly allowed.
# In production: leave unset or set to false.
ALLOW_UNSIGNED_REQUESTS = (
    os.environ.get("ALLOW_UNSIGNED_REQUESTS", "false").lower() == "true"
)


def is_valid_twilio_request():
    """Return True if the current request carries a valid Twilio signature.

    Returns True unconditionally when ALLOW_UNSIGNED_REQUESTS is set,
    but logs a loud warning so the bypass is never silently left on
    in production.
    """
    if ALLOW_UNSIGNED_REQUESTS:
        signature = request.headers.get("X-Twilio-Signature", "")
        if not signature:
            log_sec.warning("ALLOW_UNSIGNED_REQUESTS=true "
                  "— accepting unsigned request")
        return True

    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        log_sec.warning("Rejected: no X-Twilio-Signature header")
        return False

    is_valid = _twilio_validator.validate(
        request.url,
        request.form.to_dict(),
        signature
    )
    if not is_valid:
        log_sec.warning(f"Rejected: signature mismatch for {request.url}")
    return is_valid


# ---------------------------------------------------------------------------
# Channel-agnostic brain
# ---------------------------------------------------------------------------

def safe_process_message(message, sender_id, business_id, config, channel):
    """process_message, but a bug becomes an apology instead of a crash.

    Without this, any exception reached Flask as an HTML error page. The
    chat widget can't read that, so it showed "couldn't reach the assistant"
    (2026-09-25), and Twilio would have sent the customer nothing at all.
    The traceback still goes to the log in full.
    """
    try:
        return process_message(message, sender_id, business_id, config,
                               channel=channel)
    except Exception:
        log.exception("Unhandled error answering %s on %s (business %s)",
                      sender_id, channel, business_id)
        from llm import unavailable_reply
        return unavailable_reply(config)


def process_message(message, sender_id, business_id, config, channel="sms"):
    """Core message handler — channel-agnostic.

    Routes the message through the bot's decision layers:
      1. Booking state machine  (if customer is mid-booking)
      2. Rules engine           (fast keyword matching)
      3. LLM + RAG fallback     (when no rule matched)

    Saves both the customer's message and the bot's reply to the database
    AFTER generating the reply, so the current message is never included
    in the history passed to the LLM (which would duplicate it).

    Returns the reply text string.
    """
    # Open the books for this turn; turn_cost_summary() closes them below.
    start_turn_accounting()

    reply_text = None
    source = "unknown"   # which layer produced the reply

    # --- 1. Booking state machine ---
    # If the customer is mid-booking, bypass rules entirely — every message
    # in a booking flow is an answer to the bot's last question.
    if is_mid_booking(sender_id, business_id):
        reply_text = handle_booking(sender_id, message, config, business_id,
                                    channel=channel)
        source = "scheduler"

    else:
        # --- 2. Rules engine ---
        reply_text = get_reply(message, config)

        if reply_text == BOOK_INTENT:
            # Rule matched a booking trigger — start the booking flow.
            log.info(f"Booking intent detected for {sender_id}")
            reply_text = handle_booking(sender_id, message, config,
                                        business_id, channel=channel)
            source = "scheduler"

        elif reply_text is None:
            # No keyword rule matched. Before answering, check whether this
            # is actually a booking request — keyword matching can't tell
            # "I'd like to order a cake for Friday" from a question, and
            # was replying by asking the customer to type "order".
            from scheduler import get_slot_definitions
            from llm import classify_and_extract

            slots  = get_slot_definitions(config)
            result = classify_and_extract(message, slots, config)

            if result.get("intent") == "book":
                log.info(f"Booking intent detected (LLM) for {sender_id}")
                extracted = {k: v for k, v in result.items() if k != "intent"}
                reply_text = handle_booking(
                    sender_id, message, config, business_id,
                    prefilled=extracted, channel=channel
                )
                source = "scheduler"
            else:
                history    = get_recent_messages(sender_id, business_id, limit=10)
                reply_text = get_llm_reply(message, history, config, channel=channel)
                source     = "llm"

        else:
            source = "rule"

    # Save after all logic — preserves the fetch-before-save ordering above.
    save_message(sender_id, "user",      message,    business_id, source ="customer")
    save_message(sender_id, "assistant", reply_text, business_id, source =source)

    # What this one message cost. Grouped under the turn id like every other
    # line, so the log answers "what does a booking conversation cost?" by
    # reading rather than by waiting a day for a vendor dashboard.
    spent = turn_cost_summary()
    if spent:
        log_cost.info("business %s — %s", business_id, spent)

    return reply_text


# ---------------------------------------------------------------------------
# Channel adapters
# ---------------------------------------------------------------------------

def _client_ip():
    """The caller's address, honouring the proxy header Render sets.

    request.remote_addr on a platform like Render is the load balancer, so
    every visitor looks like one address. X-Forwarded-For's FIRST entry is
    the original client; later entries are the proxies it passed through.
    A client can forge the header, but on a platform that always sets it
    the forged value is appended to, not substituted for, the real one.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


@app.route("/sms", methods=["POST"])
def sms_reply():
    """Twilio SMS channel adapter.

    Receives Twilio's webhook, identifies the business from the 'To' number,
    loads the right config, calls process_message(), returns TwiML.
    """
    # Security gate — must pass before touching any message content.
    if not is_valid_twilio_request():
        return "Forbidden", 403

    # Parse Twilio's form-encoded webhook body.
    incoming_msg = request.form.get("Body", "").strip()
    from_number  = normalize_phone(request.form.get("From", "unknown"))
    to_number    = normalize_phone(request.form.get("To",   "unknown"))

    new_turn()      # every line logged for this message shares one id
    log.info("Incoming SMS for %s from %s (%s)", business["name"],
             from_number, scrub(incoming_msg))
    log.debug("SMS content from %s: %r", from_number, incoming_msg)

     # Identify which business this webhook is for.
    business = get_business_by_number(to_number)
    if not business:
        log.warning(f"No business registered for {to_number}")
        return "Forbidden", 404

    config      = load_config(business["config_path"], business["id"])
    business_id = business["id"]

    log.info(f"Serving: {business['name']} (id={business_id})")

    # Run the message through the channel-agnostic brain.
    reply_text = safe_process_message(
        incoming_msg, from_number, business_id, config, channel="sms"
    )

    # Format the reply as TwiML XML for Twilio.
    resp = MessagingResponse()
    resp.message(reply_text)
    return str(resp)

@app.route("/webchat/<slug>", methods=["POST"])
def webchat_reply(slug):
    """Web chat channel adapter.

    Accepts JSON: {"message": str, "session_id": str}
    Returns JSON:  {"reply": str}

    The session_id plays the role the phone number plays for SMS — it's
    the key for history, booking state, and appointments. Generated
    client-side and held for the life of the browser session.
    """
    business = get_business_by_slug(slug)
    if not business:
        return {"error": "Unknown business"}, 404

    data = request.get_json(silent=True) or {}
    message    = (data.get("message") or "").strip()
    session_id = (data.get("session_id") or "").strip()

    if not message or not session_id:
        return {"error": "message and session_id are required"}, 400

    # Cap message length — a web form can send arbitrarily large payloads,
    # unlike SMS which is naturally capped by the carrier.
    if len(message) > 1000:
        return {"error": "Message too long"}, 400

    # Every message here costs an LLM call and an embedding call. This
    # endpoint is public, so the limit is the difference between a demo and
    # a bill. Counted per session AND per IP: a script that mints a fresh
    # session_id per request would sail past a session-only limit.
    import ratelimit
    allowed, wait = ratelimit.webchat(slug, session_id, _client_ip())
    if not allowed:
        log_web.warning("Rate limited %s (%s) on %s", session_id,
                        _client_ip(), slug)
        return {"reply": "You're sending messages faster than I can keep up "
                         "with. Give me a moment and try again.",
                "retry_after": wait}, 429

    config      = load_config(business["config_path"], business["id"])
    business_id = business["id"]

    # A demo is swept once nobody has used it for a while, and this is what
    # "used" means for a visitor who is chatting rather than clicking around
    # the portal. Cheap enough to run on every message; skipped entirely for
    # real businesses, which are never swept.
    if business.get("is_demo"):
        from demo import touch
        touch(business_id)

    new_turn()
    log_web.info("%s <- %s (%s)", business["name"], session_id, scrub(message))
    log_web.debug("Web chat content from %s: %r", session_id, message)

    reply_text = safe_process_message(
        message, session_id, business_id, config, channel="webchat"
    )

    return {"reply": reply_text}

@app.route("/demo", methods=["GET"])
def demo_picker():
    """Pick a business to explore. The front door of the sandbox."""
    from demo import catalogue
    return render_template("demo_picker.html", templates=catalogue(),
                           error=request.args.get("error"))


@app.route("/demo/start", methods=["POST"])
def demo_start():
    """Mint a demo tenant for this visitor and sign them in as its owner.

    The visitor gets a whole business of their own — settings, knowledge
    base, diary — cloned from the template they picked. Nothing they do
    here can reach the business it was cloned from.
    """
    slug = (request.form.get("template") or "").strip()

    # This endpoint writes rows, so it's limited harder than chat is. The
    # other two halves of the same defence live in demo.py: a ceiling on
    # how many demos exist at once, and a sweep that clears the idle ones.
    import ratelimit
    allowed, wait = ratelimit.demo_start(_client_ip())
    if not allowed:
        log_web.warning("Demo creation throttled for %s", _client_ip())
        return redirect(url_for(
            "demo_picker",
            error=f"That's a lot of demos. Try again in {max(wait, 1)} seconds."
        ))

    from demo import clone_template
    minted = clone_template(slug)
    if not minted:
        return redirect(url_for(
            "demo_picker",
            error="Couldn't start that demo just now — please try another."
        ))

    # Sign them in as the clone's owner. Set directly rather than posting
    # the generated password back through the login form: the password
    # exists because the users table requires one, and putting it on the
    # wire would only give it somewhere to leak.
    #
    # If somebody was already signed in, remember who. Starting a demo used
    # to replace their session with no warning and no way back: an operator
    # who clicked a demo out of curiosity found their own dashboard
    # redirecting them into a sandbox business, with nothing on screen
    # saying why. The nav offers them a way back now.
    displaced = session.get("user_email")
    session["user_email"] = minted["email"]
    if displaced and displaced != minted["email"]:
        session["displaced_user"] = displaced
        log_web.info("Demo replaced the session of %s", displaced)
    # permanent=True is what arms PERMANENT_SESSION_LIFETIME. It reads
    # backwards: permanent=False means a browser-session cookie, which
    # sounds safer but has no timeout at all — it lives as long as the
    # browser does, and every modern browser restores sessions after a
    # restart. permanent=True plus a lifetime is an idle timeout, refreshed
    # on each request, which is what we actually want.
    session.permanent = True
    log_web.info("Demo started: %s from template %r",
                 minted["business"]["slug"], slug)
    return redirect(url_for("chat_page", slug=minted["business"]["slug"]))


@app.route("/chat/<slug>", methods=["GET"])
def chat_page(slug):
    """Serve the customer-facing chat page for a business.

    This lives at /chat, not /demo, because "demo" had quietly come to mean
    two different things: a demo OF the chatbot (this page, for any
    business, including real clients) and a demo TENANT (a throwaway clone
    with is_demo = 1). /demo/bobs_plumbing was the real Bob's Plumbing while
    /demo/demo-bobs_plumbing-0fe37a was a clone, and nothing in the URL said
    which. One word doing two jobs is how the wrong business gets opened.

    Now /chat/<slug> is the chat page for anyone, and "demo" means only the
    sandbox: /demo picks a business, /demo/start mints a clone.
    """
    business = get_business_by_slug(slug)
    if not business:
        return "Unknown business", 404
    config = load_config(business["config_path"], business["id"])
    return render_template(
        "demo.html",
        business=business,
        is_demo=bool(business.get("is_demo")),
        greeting=f"Hi! I'm the {config['business']['name']} assistant. How can I help?",
    )
@app.route("/demo/leave", methods=["GET"])
def demo_leave():
    """Give someone their own account back after a demo.

    Their demo tenant is left alone — the idle sweep takes it, the same as
    if they'd closed the tab. This only swaps the session back.
    """
    displaced = session.pop("displaced_user", None)
    if displaced:
        session["user_email"] = displaced
        log_web.info("Returned %s to their own account", displaced)
        return redirect(url_for("admin.dashboard"))
    return redirect(url_for("demo_picker"))


@app.route("/demo/<slug>", methods=["GET"])
def demo_page_legacy(slug):
    """The old address for the chat page. Kept working, not kept as truth.

    It's in the README, on the deployed site and in whatever bookmarks and
    links already exist; breaking those to tidy a name would be a poor
    trade. A 302 rather than a 301 because a permanent redirect is cached
    hard by browsers and is a genuine nuisance to take back.
    """
    return redirect(url_for("chat_page", slug=slug), code=302)


@app.route("/", methods=["GET"])
def index():
    return redirect(url_for("demo_picker"))

if __name__ == "__main__":
    app.run(debug=True)
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

# Logging is configured BEFORE our own modules are imported, and that order
# is load-bearing. rag.py announces its embedding backend at import time, so
# that line is emitted *during* the import statement below. Anything logged
# before handlers exist falls to logging's last-resort handler, which passes
# only WARNING and above — so the line vanished with no error to explain it.
from logging_setup import setup_logging, new_turn
setup_logging()

from flask import Flask, request, render_template
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
from llm import get_llm_reply
from scheduler import handle_booking, is_mid_booking
from phone_utils import normalize as normalize_phone
from admin import admin_bp

log_boot = logging.getLogger("bootstrap")
log_sec = logging.getLogger("security")
log = logging.getLogger("app")
log_web = logging.getLogger("webchat")

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
def bootstrap():
    """Prepare everything the app needs to serve requests.

    Idempotent by design: safe to run on every boot. Creates tables, registers
    businesses if the registry is empty, seeds an operator account if no users
    exist, and ingests each business's documents if their vector collection is
    missing. This is what makes deployment to an ephemeral filesystem work
    without manual setup steps.
    """
    init_db()

    from db import get_all_businesses
    businesses = get_all_businesses()

    if not businesses:
        log_boot.info("No businesses registered — running seed.")
        from seed_businesses import seed
        seed()
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
            create_user(email, password, business_id=None, is_operator=True)
            log_boot.info(f"Seeded operator account: {email}")
        else:
            log_boot.warning("No users exist and ADMIN_EMAIL / "
                  "ADMIN_PASSWORD are not set — the admin portal is "
                  "unreachable.")

    from rag import ensure_ingested
    for b in businesses:
        if not b["active"]:
            continue
        try:
            ensure_ingested(load_config(b["config_path"], b["id"]))
        except Exception as e:
            # A failed ingest shouldn't stop the server from starting —
            # that business just won't have RAG until it's fixed.
            log_boot.warning(f"Ingest failed for {b['name']}: {e}")

    log_boot.info(f"Ready — {len(businesses)} business(es) registered.")
    
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
init_db()          # tables only — no ChromaDB access before fork
app.register_blueprint(admin_bp)

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
    reply_text = None
    source = "unknown"   # which layer produced the reply

    # --- 1. Booking state machine ---
    # If the customer is mid-booking, bypass rules entirely — every message
    # in a booking flow is an answer to the bot's last question.
    if is_mid_booking(sender_id, business_id):
        reply_text = handle_booking(sender_id, message, config, business_id)
        source = "scheduler"

    else:
        # --- 2. Rules engine ---
        reply_text = get_reply(message, config)

        if reply_text == BOOK_INTENT:
            # Rule matched a booking trigger — start the booking flow.
            log.info(f"Booking intent detected for {sender_id}")
            reply_text = handle_booking(sender_id, message, config, business_id)
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
                    prefilled=extracted
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
    log.info(f"Incoming message from {from_number}: {incoming_msg!r}")

     # Identify which business this webhook is for.
    business = get_business_by_number(to_number)
    if not business:
        log.warning(f"No business registered for {to_number}")
        return "Forbidden", 404

    config      = load_config(business["config_path"], business["id"])
    business_id = business["id"]

    log.info(f"Serving: {business['name']} (id={business_id})")

    # Run the message through the channel-agnostic brain.
    reply_text = process_message(
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

    new_turn()
    log_web.info(f"{business['name']} <- {session_id}: {message!r}")

    reply_text = process_message(
        message, session_id, business_id, config, channel="webchat"
    )

    return {"reply": reply_text}

@app.route("/demo/<slug>", methods=["GET"])
def demo_page(slug):
    """Serve the demo chat UI for a business."""
    business = get_business_by_slug(slug)
    if not business:
        return "Unknown business", 404
    config = load_config(business["config_path"], business["id"])
    return render_template(
        "demo.html",
        business=business,
        greeting=f"Hi! I'm the {config['business']['name']} assistant. How can I help?",
    )
@app.route("/", methods=["GET"])
def index():
    return "Chatbot server is running.", 200

if __name__ == "__main__":
    app.run(debug=True)
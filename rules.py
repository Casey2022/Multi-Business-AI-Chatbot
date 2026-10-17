# rules.py — keyword matching engine.
#
# Fast path: tries to answer common questions instantly without any LLM call.
# get_reply returns:
#   - A reply string      → rule matched, send this text
#   - BOOK_INTENT         → booking trigger, app.py starts the booking flow
#   - None                → no match, defer to the LLM
#
# Config is passed explicitly per-request so the same module can serve
# different businesses on different requests.

import logging
import re

from config import substitute

log = logging.getLogger("rules")

# Sentinel value signalling "start the booking flow."
# Double-underscore prefix marks it as structural/internal, not customer-facing.
BOOK_INTENT = "__BOOK__"

# BOOKING_RULE is hardcoded here, not in YAML, because booking is a universal
# system capability — every business gets it automatically. Business-specific
# rules come from config["rules"] and are merged in at call time.
BOOKING_RULE = {
    "name": "book",
    "keywords": ["book", "schedule", "appointment", "make an appointment", "order"],
    # "command": the WHOLE message must be a booking command, optionally
    # with polite framing ("I'd like to book", "order please"). It used to
    # be "any", which fired on the word anywhere: "I cancelled my cake
    # order, do I get my deposit back?" started a booking on the live demo
    # (2026-09-25), and Sunrise's own "cancel my order" rule could never
    # win. A longer message goes to llm.classify_and_extract, which reads
    # what the sentence is about instead of which words it contains.
    "match": "command",
    "reply": BOOK_INTENT,
}

_POLITE_START = r"(?:(?:hi|hello|hey)[,!.]?\s+)?(?:(?:i(?:'d| would)? (?:like|want|need) to|can i|could i|i'?d like to|let me|i want an?|i need an?|i'?d like an?)\s+)?"
_POLITE_END = r"(?:\s+(?:please|pls|now|today))?\s*[.!?]*"
_ARTICLE = r"(?:(?:a|an|my)\s+)?"


def is_booking_command(text, keywords=BOOKING_RULE["keywords"]):
    """True if the message is just a request to start a booking.

    "book", "order please", "I'd like to book an appointment" -> True.
    "I cancelled my order, refund?", "where's my order" -> False.
    """
    words = "|".join(re.escape(k) for k in sorted(keywords, key=len, reverse=True))
    # "book an appointment", "place an order", "make an order"
    core = rf"(?:(?:place|make)\s+)?{_ARTICLE}(?:{words})(?:\s+{_ARTICLE}(?:{words}))?"
    pattern = rf"^{_POLITE_START}{core}{_POLITE_END}$"
    return re.match(pattern, text.strip().lower()) is not None


def _mentions(text, phrases):
    """True if any phrase appears in the text as whole words."""
    return any(re.search(rf"\b{re.escape(p)}\b", text) for p in phrases)


def get_reply(message, config):
    """Try to match the message against keyword rules for the given business.

    Builds the rules list fresh from config on every call so each request
    uses the correct business's rules. Cheap — just a list concat.

    Returns a reply string, BOOK_INTENT, or None.
    """
    text = message.strip().lower()

    # BOOKING_RULE first so a bare "book" or "order" always starts a
    # booking, even if a business rule shares the word. It only matches
    # whole-message commands now, so it no longer steals sentences that
    # merely mention an order ("cancel my order" reaches its own rule).
    rules = [BOOKING_RULE] + config.get("rules", [])

    for rule in rules:
        matched = False
        keywords = rule.get("keywords", [])

        if rule.get("match") == "command":
            matched = is_booking_command(text, keywords)
        elif rule.get("match") == "exact":
            # Entire normalized message must equal one of the keywords.
            if text in keywords:
                matched = True
        else:
            # "any" — keyword appears as a whole word.
            #
            # Substring matching was the original behaviour and caused real
            # misfires: a location rule keyed on "address" matched "...to
            # address a plumbing issue", stealing the question from RAG.
            # Word boundaries stop the substring class of that problem;
            # ambiguous words still need removing from the keyword list.
            if _mentions(text, keywords):
                matched = True

        # "unless": words that mean the message is asking something the
        # canned reply doesn't answer, so the model should have it. The
        # "cancel my appointment" rule says "call us", which is right for
        # doing it and wrong for "what's the charge if I cancel my
        # appointment tomorrow?": the cancellation rules answer that
        # (Casey, 2026-10-17).
        if matched and _mentions(text, rule.get("unless", [])):
            log.debug("Rule %s stepped aside (an 'unless' word)", rule["name"])
            matched = False

        if matched:
            log.debug("Matched rule %s", rule["name"])
            reply = rule["reply"]

            # The booking sentinel is a signal to app.py, not text for the
            # customer — return it before substitution touches it.
            if reply == BOOK_INTENT:
                return reply

            # Substitute {business_name}, {phone}, etc. at match time,
            # not load time — so dynamic placeholders work correctly.
            return substitute(reply, config)

    # A log line, not print(): print wrote through the evals' progress bars
    # and left "[rules] no match" fragments all over them (2026-10-15).
    log.debug("No rule matched; deferring to the model")
    return None
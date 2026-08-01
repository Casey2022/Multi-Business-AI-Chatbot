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

from config import substitute

# Sentinel value signalling "start the booking flow."
# Double-underscore prefix marks it as structural/internal, not customer-facing.
BOOK_INTENT = "__BOOK__"

# BOOKING_RULE is hardcoded here, not in YAML, because booking is a universal
# system capability — every business gets it automatically. Business-specific
# rules come from config["rules"] and are merged in at call time.
BOOKING_RULE = {
    "name": "book",
    "keywords": ["book", "schedule", "appointment", "make an appointment", "order"],
    "match": "any",
    "reply": BOOK_INTENT,
}


def get_reply(message, config):
    """Try to match the message against keyword rules for the given business.

    Builds the rules list fresh from config on every call so each request
    uses the correct business's rules. Cheap — just a list concat.

    Returns a reply string, BOOK_INTENT, or None.
    """
    text = message.strip().lower()

    # BOOKING_RULE first so it always wins over any business-defined rule
    # that might have overlapping keywords.
    rules = [BOOKING_RULE] + config.get("rules", [])

    for rule in rules:
        matched = False
        keywords = rule.get("keywords", [])

        if rule.get("match") == "exact":
            # Entire normalized message must equal one of the keywords.
            # Used for short, unambiguous inputs like "hi".
            if text in keywords:
                matched = True
        else:
            # "any" — keyword appears anywhere in the message.
            # Used for content-bearing questions like "what are your hours".
            if any(keyword in text for keyword in keywords):
                matched = True

        if matched:
            # Booking triggers should be commands ("book", "order please"),
            # not sentences that happen to contain the word ("lets order a
            # dozen cupcakes with chocolate frosting..."). Long messages fall
            # through — usually to the LLM, which points the customer at
            # booking conversationally.
            if rule["reply"] == BOOK_INTENT and len(text.split()) > 4:
                continue

            print(f"[rules] matched rule: {rule['name']}")
            reply = rule["reply"]
            if reply == BOOK_INTENT:
                return reply
            # Substitute {business_name}, {phone}, etc. at match time,
            # not load time — so dynamic placeholders work correctly.
            return substitute(reply, config)

    print("[rules] no match -> defer to LLM")
    return None
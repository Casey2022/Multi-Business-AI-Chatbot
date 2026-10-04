"""conversation.py — what happens to one customer message, whatever the channel.

Moved out of app.py (2026-10-03) unchanged, so it can be run without Flask
or the server's startup: journey_eval.py drives real conversations through
exactly this function, the path a live message takes (booking state,
keyword rules, the booking-or-question classifier, then booking or Q&A).
The single-question evals call the Q&A step directly and never met the
routing, which is where both of 2026-10-03's live failures were.

app.py's channel adapters call it through app.safe_process_message.
"""

import logging

from db import save_message, get_recent_messages
from rules import get_reply, BOOK_INTENT
from llm import get_llm_reply, start_turn_accounting, turn_cost_summary
from scheduler import handle_booking, is_mid_booking

log = logging.getLogger("app")
log_cost = logging.getLogger("cost")


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

            slots   = get_slot_definitions(config)
            history = get_recent_messages(sender_id, business_id, limit=10)
            # The classifier sees what the bot just said, so an answer to
            # the bot's own question isn't read as a new order.
            previous_reply = next((m.get("content") for m in reversed(history)
                                   if m.get("role") == "assistant"), None)
            result = classify_and_extract(message, slots, config,
                                          previous_reply=previous_reply,
                                          history=history)

            if result.get("intent") == "book":
                log.info(f"Booking intent detected (LLM) for {sender_id}")
                extracted = {k: v for k, v in result.items() if k != "intent"}
                reply_text = handle_booking(
                    sender_id, message, config, business_id,
                    prefilled=extracted, channel=channel
                )
                source = "scheduler"
            else:
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

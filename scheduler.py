# scheduler.py — multi-turn booking state machine.
#
# Manages the booking conversation flow as a finite state machine.
# State persists in SQLite so conversations survive server restarts and
# arbitrary gaps between messages.
#
# States:
#   idle              → normal Q&A, not in a booking flow
#   awaiting_service  → asked "what service?", waiting for reply
#   awaiting_datetime → asked "when?", waiting for reply
#
# Config and business_id are passed explicitly per-request so the same
# module can serve any business simultaneously.

import calendar_sync
from config import substitute
from db import get_state, set_state, save_appointment, get_recent_messages
from llm import parse_datetime
from llm import parse_datetime, extract_booking_slots

def get_slot_definitions(config):
    """Return the ordered list of slots this business collects.

    Always starts with service and datetime (universal), then appends
    the business's configured extra_questions. Each slot is a dict with
    'key' (storage name), 'prompt' (what to ask), and 'description'
    (what it means — used to guide LLM extraction).

    Single source of truth: both the extractor and the fill-loop read this.
    """
    BOOKING = config.get("booking", {})
    noun = BOOKING.get("noun", "appointment")

    slots = [
        {
            "key": "service",
            "prompt": BOOKING.get("greeting", "What service do you need?"),
            "description": f"the service or item being requested for this {noun}",
        },
        {
            "key": "datetime",
            "prompt": BOOKING.get("ask_datetime", "When would you like it?"),
            "description": "the requested date and time, in the customer's own words",
        },
    ]

    for q in BOOKING.get("extra_questions", []):
        slots.append({
            "key": q["key"],
            "prompt": q["prompt"],
            # Reuse the prompt as the description — it already explains
            # what we're asking for, which is exactly what the extractor
            # needs to know.
            "description": q["prompt"],
        })

    return slots


def is_mid_booking(phone, business_id):
    """Return True if this customer is currently in a booking flow.

    Used by app.py to decide whether to route the message to handle_booking
    or to the rules engine. Encapsulates state-name knowledge so app.py
    never needs to know what the state values are called.
    """
    return get_state(phone, business_id)["state"] != "idle"

def _finalize_booking(phone, business_id, pending, config):
    """Save the booking and return the confirmation.

    Ordering matters: the calendar write happens FIRST. If it fails, nothing
    is saved and no confirmation is produced — a booking the business owner
    can't see on their calendar is worse than no booking at all.
    """
    from datetime import datetime as _dt

    BOOKING = config.get("booking", {})
    service = pending.get("service", "service")
    parsed  = pending.get("datetime_parsed") or pending.get("datetime")

    extras = {
        k: v for k, v in pending.items()
        if k not in ("service", "datetime", "datetime_parsed")
    }

    # --- Calendar sync (only if this business has it configured) ---
    event_id = None
    calendar_id  = None
    sync_status  = "none"

    if calendar_sync.is_enabled(config):
        try:
            event_id = calendar_sync.create_event(
                config,
                service_name=service,
                start_iso=parsed,
                customer_id=phone,
                details=extras,
            )
            calendar_id = config["calendar"]["calendar_id"]
            sync_status = "synced"
        except Exception as e:
            print(f"[scheduler] Calendar write FAILED: {e}")
            # Reset state so the customer isn't stuck mid-booking, and be
            # honest that nothing was booked.
            set_state(phone, business_id, "idle", pending={})
            phone_number = config["business"].get("phone", "us")
            return (f"Sorry — I couldn't complete that booking just now. "
                    f"Please give us a call at {phone_number} and we'll "
                    f"get you scheduled.")

    save_appointment(phone, service, parsed, business_id, details=extras, external_event_id=event_id, external_calendar=calendar_id, sync_status=sync_status,)

    noun = BOOKING.get("noun", "appointment")
    print(f"[scheduler] Saved {noun}: {phone} | {service} | {parsed} | extras={extras}")

    friendly_when = _dt.strptime(parsed, "%Y-%m-%d %H:%M").strftime(
        "%A, %B %-d at %-I:%M %p"
    )

    set_state(phone, business_id, "idle", pending={})

    final = BOOKING.get(
        "final_confirmation",
        "Perfect! {noun} confirmed: {service} on {datetime}."
    )
    final = (final
             .replace("{service}", service)
             .replace("{datetime}", friendly_when)
             .replace("{noun}", noun))
    return substitute(final, config)

def _first_missing_slot(pending, slots):
    """Return the first slot definition with no value in pending, or None."""
    for slot in slots:
        if not pending.get(slot["key"]):
            return slot
    return None


def _ask_next_or_finalize(phone, business_id, pending, config, slots,
                          first_turn=False):
    """Ask for the first missing slot, or finalize if everything's filled.

    The datetime slot is special: it's stored as the customer's raw phrasing
    during collection, then parsed to ISO right before finalizing. That way a
    correction ("actually Thursday") re-parses cleanly.
    """
    BOOKING = config.get("booking", {})

    missing = _first_missing_slot(pending, slots)
    if missing:
        prompt = substitute(missing["prompt"], config)
        # On the opening turn, lead with the booking greeting for warmth
        # unless the greeting IS the question we're about to ask.
        if first_turn and missing["key"] != "service":
            greeting = substitute(BOOKING.get("greeting", ""), config)
            if greeting:
                return f"{greeting} {prompt}"
        return prompt

    # All slots filled — parse the datetime before saving.
    parsed = parse_datetime(pending["datetime"])
    if parsed is None:
        # Unparseable: clear it so the loop asks again next turn.
        pending.pop("datetime", None)
        set_state(phone, business_id, "collecting", pending=pending)
        return substitute(
            BOOKING.get(
                "fallback_after_bad_date",
                "Sorry, I couldn't read that as a date and time. "
                "Try something like 'Tuesday at 3pm'."
            ),
            config
        )

    pending["datetime_parsed"] = parsed
    set_state(phone, business_id, "confirming", pending=pending)
    return _confirmation_question(pending, config)

# Recognized responses in the confirming state. Kept deliberately small —
# anything else is treated as a possible correction and run through extraction.
AFFIRMATIVE = {"yes", "y", "yep", "yeah", "yup", "correct", "right",
               "confirm", "confirmed", "sounds good", "that's right", "ok", "okay"}
NEGATIVE    = {"no", "n", "nope", "nah", "wrong", "incorrect"}


def _confirmation_question(pending, config):
    """Read the booking back to the customer and ask them to confirm."""
    from datetime import datetime as _dt

    BOOKING = config.get("booking", {})
    service = pending.get("service", "your order")
    parsed  = pending.get("datetime_parsed")

    friendly = _dt.strptime(parsed, "%Y-%m-%d %H:%M").strftime(
        "%A, %B %-d at %-I:%M %p"
    )
    template = BOOKING.get(
        "confirm_prompt",
        "Just to confirm: {service} on {datetime}. Is that right?"
    )
    return substitute(
        template.replace("{service}", service).replace("{datetime}", friendly),
        config
    )

def handle_booking(phone, message, config, business_id):
    """Advance the booking using slot extraction plus a fill-the-gaps loop.

    Every message runs through extraction, so customers can volunteer or
    correct any slot at any point. The machine then asks about the first
    still-missing slot, or finalizes when all are filled.

    State is just "collecting" — the *data* determines what's asked next,
    not the state name.
    """
    BOOKING = config.get("booking", {})
    slots   = get_slot_definitions(config)

    current = get_state(phone, business_id)
    state   = current["state"]
    pending = current["pending"]
    text    = message.strip().lower()

    # --- Cancel: transversal, checked before anything else ---
    if text in ("cancel", "stop", "nevermind", "never mind"):
        set_state(phone, business_id, "idle", pending={})
        noun = BOOKING.get("noun", "appointment")
        return substitute(
            BOOKING.get("cancel_reply", f"No problem — {noun} cancelled."),
            config
        )

    # --- Opening turn: greet, then extract from the triggering message ---
    # The trigger itself may carry information ("book a drain cleaning").
    if state == "idle":
        extracted = extract_booking_slots(message, slots, config)
        pending.update(extracted)
        set_state(phone, business_id, "collecting", pending=pending)
        return _ask_next_or_finalize(
            phone, business_id, pending, config, slots, first_turn=True
        )

    # --- Mid-booking: extract from every message, then re-evaluate ---
    if state == "collecting":
        extracted = extract_booking_slots(
            message, slots, config, already_filled=pending
        )

        # Fallback: if extraction found nothing but we're clearly waiting on
        # a specific slot, treat the whole message as that slot's answer.
        # Handles terse replies ("none", "blue") the extractor may skip.
        if not extracted:
            missing = _first_missing_slot(pending, slots)
            if missing:
                extracted = {missing["key"]: message.strip()[:200]}
                print(f"[scheduler] No slots extracted — "
                      f"treating message as '{missing['key']}'")

        pending.update(extracted)
        set_state(phone, business_id, "collecting", pending=pending)
        return _ask_next_or_finalize(phone, business_id, pending, config, slots)
    # --- Confirming: read-back accepted, rejected, or corrected ---
    if state == "confirming":
        if text in AFFIRMATIVE:
            return _finalize_booking(phone, business_id, pending, config)

        # Not a plain yes — see if they're correcting something
        # ("no, 2pm instead" / "make it Thursday").
        extracted = extract_booking_slots(
            message, slots, config, already_filled=pending
        )
        if extracted:
            pending.update(extracted)
            pending.pop("datetime_parsed", None)   # force a re-parse
            set_state(phone, business_id, "collecting", pending=pending)
            return _ask_next_or_finalize(phone, business_id, pending, config, slots)

        if text in NEGATIVE:
            pending.pop("datetime", None)
            pending.pop("datetime_parsed", None)
            set_state(phone, business_id, "collecting", pending=pending)
            return "No problem — what date and time would work better?"

        # Unclear response — ask again rather than guessing.
        return _confirmation_question(pending, config)

    # --- Unknown state: reset gracefully ---
    print(f"[scheduler] WARNING: unknown state '{state}' for {phone} — resetting.")
    set_state(phone, business_id, "idle", pending={})
    return "Something went wrong on my end. Let's start over — how can I help?"
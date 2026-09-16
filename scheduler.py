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
from config import substitute, question_labels, humanize
from db import get_state, set_state, save_appointment, get_recent_messages
from llm import parse_datetime
from llm import parse_datetime, extract_booking_slots

import logging
log = logging.getLogger("scheduler")
log_cal = logging.getLogger("calendar")

# Bookkeeping the booking flow keeps in `pending` that is not a customer
# answer. Prefixed so it can never collide with a slot key an owner invents.
INTERNAL_PENDING_KEYS = ("_checks", "_address_confirm", "_address_attempts",
                         "_offered")

# How many times we'll ask a customer to clarify an address before giving up
# and booking it as typed. Two is a compromise: enough to recover a typo or a
# missing city, few enough that nobody feels interrogated by a bot that won't
# take their answer.
ADDRESS_MAX_ATTEMPTS = 2

# Where a checked address is stored. Matches the key Bob's config already
# used, so appointments booked before this change keep reading correctly.
ADDRESS_SLOT_KEY = "service_address"

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
            # The catalogue goes in the description because the description is
            # all the extractor sees. Without it the model was guessing what
            # this business even sells: "pipe leak" went unrecognised for four
            # turns in a row, and when it did land it arrived as the invented
            # "pipe leak repair". Mapping a customer's words onto a catalogue
            # needs to know the catalogue.
            "description": _service_description(config, noun),
        },
        {
            "key": "datetime",
            "prompt": BOOKING.get("ask_datetime", "When would you like it?"),
            "description": "the requested date and time, in the customer's own words",
        },
    ]

    # The address is a built-in slot, not something the owner wires up. They
    # turn on "limit service area" and pick a distance; asking for an address
    # is how that gets enforced, so the flow adds the question itself.
    #
    # It goes second, before the date and time, so someone we can't serve
    # finds out before describing their problem and picking a slot — and
    # before we spend a calendar lookup holding a time for a booking that
    # won't happen.
    import geocode
    extras = list(BOOKING.get("extra_questions", []))
    if geocode.radius_config(config):
        # If the owner already wrote their own address question, keep their
        # wording — it's theirs, and it may say something specific about
        # gates or parking.
        existing = next((q for q in extras
                         if q["key"] == ADDRESS_SLOT_KEY), None)
        slots.insert(1, {
            "key": ADDRESS_SLOT_KEY,
            "prompt": ((existing or {}).get("prompt")
                       or BOOKING.get("ask_address",
                                      "What's the address we're coming to?")),
            "description": "the street address where the work will happen",
            "type": "address",
        })
        # and don't ask for it twice
        extras = [q for q in extras if q["key"] != ADDRESS_SLOT_KEY]

    for q in extras:
        slots.append({
            "key": q["key"],
            "prompt": q["prompt"],
            "type": q.get("type", "text"),
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

    checks = pending.get("_checks") or {}
    extras = {
        k: v for k, v in pending.items()
        if k not in ("service", "datetime", "datetime_parsed")
        and k not in INTERNAL_PENDING_KEYS
    }

    # --- Calendar sync (only if this business has it configured) ---
    event_id = None
    calendar_id  = None
    sync_status  = "none"

    if calendar_sync.is_enabled(config):
        try:
            if not calendar_sync.is_slot_available(config, parsed):
                set_state(phone, business_id, "idle", pending={})
                return ("Sorry — that time was just taken while we were "
                        "talking. Please start again and I'll find you "
                        "another slot.")
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
            log.warning(f"Calendar write FAILED: {e}")
            # Reset state so the customer isn't stuck mid-booking, and be
            # honest that nothing was booked.
            set_state(phone, business_id, "idle", pending={})
            phone_number = config["business"].get("phone", "us")
            return (f"Sorry — I couldn't complete that booking just now. "
                    f"Please give us a call at {phone_number} and we'll "
                    f"get you scheduled.")

    save_appointment(phone, service, parsed, business_id, details=extras,
                     external_event_id=event_id, external_calendar=calendar_id,
                     sync_status=sync_status,
                     address_check=checks or None)

    noun = BOOKING.get("noun", "appointment")
    log.info(f"Saved {noun}: {phone} | {service} | {parsed} | extras={extras}")

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

def _address_was_inferred(typed, check):
    """True when the geocoder supplied a town the customer never mentioned.

    This is the whole test. "1738 William St, Buffalo NY" resolving to
    Buffalo is the system agreeing with the customer. The same street
    resolving to Rochester because that's where we biased the search is the
    system deciding on their behalf — and that decision is what books a job
    sixty miles from where someone actually lives. Confirm what we inferred;
    stay quiet about what we were told.
    """
    locality = (check.get("locality") or "").strip().lower()
    if not locality:
        return False
    return locality not in (typed or "").lower()


def _check_addresses(phone, business_id, pending, config, slots):
    """Verify address answers. Returns a reply to send, or None to carry on.

    Four ways out:
      outside            — turn the customer away, nothing booked
      needs confirming   — read the resolved address back and wait
      customer can fix   — ask for a fuller address, up to ADDRESS_MAX_ATTEMPTS
      anything else      — proceed, flagged, because our problems aren't theirs
    """
    import geocode

    checks   = pending.get("_checks") or {}
    BOOKING  = config.get("booking", {})
    recorded = False

    for slot in slots:
        if slot.get("type") != "address":
            continue
        answer = pending.get(slot["key"])
        if not answer or slot["key"] in checks:
            continue

        try:
            result = geocode.check_service_area(answer, config,
                                                business_id=business_id)
        except Exception as e:
            log.warning("Address check failed for %r (continuing): %s", answer, e)
            result = {"status": "unverified", "reason": f"checker error: {e}",
                      "address": answer, "miles": None, "formatted": None,
                      "customer_can_fix": False}

        if result["status"] == "outside":
            checks[slot["key"]] = result
            pending["_checks"] = checks
            log.info("Booking stopped — %r is outside the service area (%s mi)",
                     answer, result["miles"])
            set_state(phone, business_id, "idle", pending={})
            return substitute(BOOKING.get(
                "out_of_area_reply",
                "That address looks like it's outside our service area "
                "({service_area}). Give us a call at {phone} and we'll let "
                "you know what we can do."
            ), config)

        # Couldn't place it, and the customer could plausibly help.
        if result["status"] == "unverified" and result.get("customer_can_fix"):
            attempts = pending.get("_address_attempts", 0) + 1
            if attempts <= ADDRESS_MAX_ATTEMPTS:
                pending["_address_attempts"] = attempts
                pending.pop(slot["key"], None)      # ask the slot again
                log.info("Address %r not usable (%s) — asking again (%d/%d)",
                         answer, result["reason"], attempts, ADDRESS_MAX_ATTEMPTS)
                set_state(phone, business_id, "collecting", pending=pending)
                return BOOKING.get(
                    "address_clarify",
                    "I couldn't find that address. Could you give it to me "
                    "with the city or ZIP code?"
                )
            # Out of attempts. Take what they typed, flag it, keep going —
            # the promise from the start was that a geocoder can't block a
            # booking outright.
            log.info("Address %r still unusable after %d attempts — "
                     "booking it unverified", answer, ADDRESS_MAX_ATTEMPTS)
            checks[slot["key"]] = result
            pending["_checks"] = checks
            recorded = True
            continue

        # Found something, but we filled in a town they never said.
        if _address_was_inferred(answer, result) and result.get("formatted"):
            pending["_address_confirm"] = {
                "key": slot["key"],
                "typed": answer,
                "formatted": result["formatted"],
                "check": result,
            }
            log.info("Address %r resolved to %r — confirming with the customer",
                     answer, result["formatted"])
            set_state(phone, business_id, "collecting", pending=pending)
            return BOOKING.get(
                "address_confirm",
                "Just to make sure I have the right place — did you mean "
                "{address}?"
            ).replace("{address}", result["formatted"])

        checks[slot["key"]] = result
        pending["_checks"] = checks
        recorded = True

    if recorded:
        # handle_booking saved the state before calling us, so a check
        # recorded above would be lost — and re-run on every later message in
        # this booking. Persist it here instead.
        set_state(phone, business_id, "collecting", pending=pending)

    return None


# Openers that mean a customer is asking rather than answering. Kept small
# and literal: "none", "no", "yes" are answers, and a sentence ending in "?"
# is not.
QUESTION_OPENERS = ("what", "when", "where", "why", "how", "who", "can you",
                    "could you", "do you", "does", "are you", "is there",
                    "will you", "would you", "any chance")


def _looks_like_a_question(message):
    text = (message or "").strip().lower()
    if not text:
        return False
    if text.endswith("?"):
        return True
    return text.startswith(QUESTION_OPENERS) and len(text.split()) > 2


def _answer_mid_booking(phone, message, config, business_id):
    """Answer a question asked during booking, using the normal Q&A path.

    Best-effort: if the model is unavailable the booking continues without
    an answer rather than failing. Never lets a question become a slot value.
    """
    try:
        from llm import get_llm_reply
        history = get_recent_messages(phone, business_id, limit=6)
        return get_llm_reply(message, history=history, config=config,
                             channel="webchat")
    except Exception as e:
        log.warning("Couldn't answer a mid-booking question (%s)", e)
        return None


def _service_description(config, noun):
    """What the extractor is told the service slot means."""
    services = [s for s in (config.get("services") or []) if s]
    if not services:
        return f"the service or item being requested for this {noun}"
    catalogue = "; ".join(services)
    return (f"the service being requested for this {noun}. This business "
            f"offers exactly these: {catalogue}. Return the business's own "
            f"wording for whichever one the customer means — a customer "
            f"saying 'pipe leak' or 'my sink is dripping' means the closest "
            f"listed service. If the customer clearly wants something not on "
            f"that list, return their words unchanged rather than forcing a "
            f"match.")


def _snap_to_catalogue(value, config):
    """Tidy a near-miss into the business's own wording, conservatively.

    Only when one string contains the other — "pipe leak repair" becomes
    "leak repair". Deliberately NOT fuzzy: "water heater repair" and "water
    heater installation" share most of their words and are different jobs,
    and a scoring threshold that mapped one to the other would put the wrong
    work on the van. Anything less obvious is left alone for the owner to
    read.
    """
    if not value:
        return value
    text = " ".join(str(value).lower().split())
    for service in (config.get("services") or []):
        listed = " ".join(service.lower().split())
        if listed == text:
            return service
        if listed in text or text in listed:
            log.info("Service %r recorded as %r", value, service)
            return service
    return value


def _match_offered(message, offered):
    """Which offered time did the customer just accept? None if unclear.

    A reply like "Wednesday at 9" means the Wednesday we just named, not the
    next Wednesday on the calendar. Matching here — against our own offer —
    is deterministic and needs no model. Genuine ambiguity ("Wednesday" when
    three Wednesday slots were offered) returns None and lets the normal
    parsing run, rather than guessing.
    """
    import re
    from datetime import datetime

    text = (message or "").lower()
    if not text.strip():
        return None

    spoken_hours = set(re.findall(r"\b(\d{1,2})\b", text))
    matches = []
    for iso in offered:
        when = datetime.strptime(iso, "%Y-%m-%d %H:%M")
        if when.strftime("%A").lower() not in text:
            continue
        if spoken_hours and when.strftime("%-I") not in spoken_hours:
            continue
        matches.append(iso)

    return matches[0] if len(matches) == 1 else None


def _question_part(prompt):
    """Drop a leading pleasantry from a configured prompt, keep the question.

    "Happy to schedule an appointment! What service do you need? (e.g. …)"
    becomes "What service do you need? (e.g. …)" — so an acknowledgement can
    be prepended without the customer being greeted twice.
    """
    import re
    parts = re.split(r"(?<=[.!])\s+", prompt.strip())
    for i, part in enumerate(parts):
        if "?" in part:
            return " ".join(parts[i:])
    return prompt


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

    # Check addresses the moment we have them, before asking anything else.
    refusal = _check_addresses(phone, business_id, pending, config, slots)
    if refusal:
        return refusal

    missing = _first_missing_slot(pending, slots)
    if missing:
        prompt = substitute(missing["prompt"], config)

        if first_turn:
            service = pending.get("service")
            when    = pending.get("datetime")

            if service or when:
                # Acknowledge what we understood, so the handoff reads like a
                # conversation continuing rather than a form appearing. Built
                # per-case: gluing "for {datetime}" onto "Happy to help with"
                # produced "Happy to help with for next wednesday".
                if service and when:
                    ack = f"Happy to help with {service} on {when}."
                elif service:
                    ack = f"Happy to help with {service}."
                else:
                    ack = f"Happy to help — you mentioned {when}."
                # The service prompt is itself a greeting; two hellos in one
                # breath is the other half of that bug.
                return f"{ack} {_question_part(prompt)}"

            greeting = substitute(BOOKING.get("greeting", ""), config)
            if greeting and missing["key"] != "service":
                return f"{greeting} {prompt}"
        return prompt

    # All slots filled — parse the datetime before saving. A time already
    # resolved (because the customer picked one we offered) is kept as-is;
    # re-parsing its prose form is how the right answer gets lost again.
    parsed = pending.get("datetime_parsed") or parse_datetime(
        pending["datetime"], config)
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
    # Availability check before we offer to confirm.
    if calendar_sync.is_enabled(config):
        try:
            reason = calendar_sync.slot_rejection_reason(config, parsed)
            if reason:
                alts = calendar_sync.find_alternatives(config, parsed)
                pending.pop("datetime", None)
                pending.pop("datetime_parsed", None)
                # Record what we're about to offer BEFORE saving: anything
                # written into pending after set_state is thrown away.
                pending["_offered"] = list(alts)
                set_state(phone, business_id, "collecting", pending=pending)
                return _unavailable_message(parsed, alts, config, reason=reason)
        except Exception as e:
            log_cal.warning(f"Availability check failed (continuing): {e}")

    set_state(phone, business_id, "confirming", pending=pending)
    return _confirmation_question(pending, config)


# Recognized responses in the confirming state. Kept deliberately small —
# anything else is treated as a possible correction and run through extraction.
AFFIRMATIVE = {"yes", "y", "yep", "yeah", "yup", "correct", "right",
               "confirm", "confirmed", "sounds good", "that's right", "ok", "okay"}
NEGATIVE    = {"no", "n", "nope", "nah", "wrong", "incorrect"}


def _confirmation_question(pending, config):
    """Read the booking back to the customer — every slot, not just two.

    Confirming a subset means the customer approves details they can't see.
    A real transcript showed this failing: the customer specified frosting,
    a filling, a design and writing, all of which were captured correctly —
    but the read-back listed only the cake and the time, so they asked twice
    what happened to the frosting before confirming anyway.
    """
    from datetime import datetime as _dt

    BOOKING = config.get("booking", {})
    service = pending.get("service", "your order")
    parsed  = pending.get("datetime_parsed")

    friendly = _dt.strptime(parsed, "%Y-%m-%d %H:%M").strftime(
        "%A, %B %-d at %-I:%M %p"
    )

    lines = [f"Just to confirm: {service} on {friendly}."]

    # Every extra slot that was actually filled. Skip the bookkeeping keys
    # and skip "none"-style answers, which add nothing for the customer.
    skip_keys   = {"service", "datetime", "datetime_parsed",
                   *INTERNAL_PENDING_KEYS}
    skip_values = {"none", "n/a", "no", "nothing", "-"}
    labels      = question_labels(config)

    for key, value in pending.items():
        if key in skip_keys:
            continue
        if not value or str(value).strip().lower() in skip_values:
            continue
        label = labels.get(key) or humanize(key)
        lines.append(f"{label}: {value}.")

    lines.append("Is that right?")
    return substitute(" ".join(lines), config)

def _unavailable_message(desired_iso, alternatives, config, reason=None):
    """Explain why a slot doesn't work, and offer nearby openings.

    The reason matters: "we're closed then" and "that time is taken" call
    for different replies, and a customer told only "unavailable" will keep
    guessing at times the business never works.
    """
    from datetime import datetime as _dt

    def pretty(iso):
        return _dt.strptime(iso, "%Y-%m-%d %H:%M").strftime("%A, %B %-d at %-I:%M %p")

    desired = pretty(desired_iso)
    hours   = config["business"].get("hours", "")

    if reason == "past":
        lead = f"{desired} has already passed."
    elif reason == "closed":
        lead = f"We're closed then — our hours are {hours}."
    elif reason == "blackout":
        lead = f"We're not available at {desired}."
    else:
        lead = f"Sorry, {desired} is already booked."

    if not alternatives:
        phone_number = config["business"].get("phone", "us")
        return (f"{lead} I couldn't find a nearby opening either — "
                f"give us a call at {phone_number} and we'll sort something out.")

    options = " · ".join(pretty(a) for a in alternatives)
    return f"{lead} I have: {options}. Would any of those work?"

def _check_datetime_now(phone, business_id, pending, extracted, config):
    """If a datetime was just supplied and it won't work, say so immediately.

    Returns a rejection message, or None if the time is fine (or absent).
    Called as soon as the datetime slot fills rather than after every other
    slot — collecting details for a slot that can't happen wastes the
    customer's time and reads as illogical.
    """
    if "datetime" not in extracted or not calendar_sync.is_enabled(config):
        return None

    candidate = parse_datetime(pending["datetime"], config)
    if not candidate:
        return None          # unparseable — the normal fallback handles it

    try:
        reason = calendar_sync.slot_rejection_reason(config, candidate)
        if not reason:
            return None
        alts = calendar_sync.find_alternatives(config, candidate)
        pending.pop("datetime", None)
        pending["_offered"] = list(alts)          # before the save, not after
        set_state(phone, business_id, "collecting", pending=pending)
        return _unavailable_message(candidate, alts, config, reason=reason)
    except Exception as e:
        log_cal.warning(f"Early availability check failed: {e}")
        return None

def handle_booking(phone, message, config, business_id, prefilled=None):
    """Advance the booking flow and log the reply.

    Wraps the real handler so every outgoing scheduler message is visible in
    the logs, the way LLM replies already are. Without this, booking prompts,
    rejections, and confirmations were the one part of the conversation you
    couldn't see without opening a browser.
    """
    reply = _handle_booking_inner(phone, message, config, business_id, prefilled)
    log.debug(f"Reply: {reply!r}")
    return reply

def _handle_booking_inner(phone, message, config, business_id, prefilled=None):
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
        # Slots the intent classifier already found in the triggering
        # message — no point extracting them twice.
        if prefilled:
            pending.update(prefilled)
        else:
            extracted = extract_booking_slots(message, slots, config)
            pending.update(extracted)

        early = _check_datetime_now(phone, business_id, pending,
                                    prefilled or {}, config)
        if early:
            return early

        set_state(phone, business_id, "collecting", pending=pending)
        return _ask_next_or_finalize(phone, business_id, pending, config, slots,
                                     first_turn=True)

    # --- Mid-booking: extract from every message, then re-evaluate ---
    if state == "collecting":
        # An outstanding address read-back is answered before anything else:
        # "yes" here means "that's my address", not a new booking detail, and
        # running it through slot extraction first would lose that meaning.
        awaiting = pending.get("_address_confirm")
        if awaiting:
            pending.pop("_address_confirm", None)

            if text in AFFIRMATIVE:
                # They agreed to the resolved address, so store that rather
                # than the fragment they typed — it's the version with a city
                # on it, and it's what a van needs.
                pending[awaiting["key"]] = awaiting["formatted"]
                checks = pending.get("_checks") or {}
                checks[awaiting["key"]] = awaiting["check"]
                pending["_checks"] = checks
                pending.pop("_address_attempts", None)
                log.info("Customer confirmed %r", awaiting["formatted"])
                set_state(phone, business_id, "collecting", pending=pending)
                return _ask_next_or_finalize(phone, business_id, pending,
                                             config, slots)

            if text in NEGATIVE:
                attempts = pending.get("_address_attempts", 0) + 1
                pending["_address_attempts"] = attempts
                pending.pop(awaiting["key"], None)
                log.info("Customer rejected %r (attempt %d)",
                         awaiting["formatted"], attempts)
                set_state(phone, business_id, "collecting", pending=pending)
                if attempts > ADDRESS_MAX_ATTEMPTS:
                    # Stop asking. Keep what they originally typed and let
                    # the owner sort it out.
                    pending[awaiting["key"]] = awaiting["typed"]
                    checks = pending.get("_checks") or {}
                    checks[awaiting["key"]] = {
                        **awaiting["check"], "status": "unverified",
                        "reason": "customer said the resolved address was wrong",
                    }
                    pending["_checks"] = checks
                    return _ask_next_or_finalize(phone, business_id, pending,
                                                 config, slots)
                return config.get("booking", {}).get(
                    "address_clarify",
                    "No problem — what's the full address, with the city or "
                    "ZIP code?"
                )

            # Neither yes nor no: they probably just typed the address again.
            # Fall through to normal extraction, but keep the slot clear so
            # the new answer lands in it.
            pending.pop(awaiting["key"], None)

        # Did they just accept a time we offered? Settle that before the
        # extractor gets a chance to re-interpret the weekday.
        offered = pending.get("_offered")
        if offered:
            chosen = _match_offered(message, offered)
            if chosen:
                from datetime import datetime as _dt
                pending["datetime"] = _dt.strptime(
                    chosen, "%Y-%m-%d %H:%M").strftime("%A, %B %-d at %-I:%M %p")
                pending["datetime_parsed"] = chosen
                pending.pop("_offered", None)
                log.info("Customer accepted an offered slot: %s", chosen)
                set_state(phone, business_id, "collecting", pending=pending)
                return _ask_next_or_finalize(phone, business_id, pending,
                                             config, slots)

        extracted = extract_booking_slots(
            message, slots, config, already_filled=pending
        )

        # Fallback: if extraction found nothing but we're clearly waiting on
        # a specific slot, treat the whole message as that slot's answer.
        # Handles terse replies ("none", "blue") the extractor may skip.
        if not extracted:
            missing = _first_missing_slot(pending, slots)
            if missing and _looks_like_a_question(message):
                # Absorbing this would file "Do you charge a call-out fee?"
                # as the problem description and move on as though the
                # customer had answered. They asked something; answer it,
                # then ask again for what's still missing.
                log.info("Question mid-booking: %r — answering, not storing",
                         message.strip()[:60])
                answer = _answer_mid_booking(phone, message, config, business_id)
                set_state(phone, business_id, "collecting", pending=pending)
                prompt = substitute(missing["prompt"], config)
                return f"{answer} {prompt}" if answer else prompt
            if missing:
                extracted = {missing["key"]: message.strip()[:200]}
                log.info(f"No slots extracted — "
                      f"treating message as '{missing['key']}'")

        pending.update(extracted)
        if pending.get("service"):
            pending["service"] = _snap_to_catalogue(pending["service"], config)
        early = _check_datetime_now(phone, business_id, pending, extracted, config)
        if early:
            return early
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
    log.warning(f"Unknown state '{state}' for {phone} — resetting.")
    set_state(phone, business_id, "idle", pending={})
    return "Something went wrong on my end. Let's start over — how can I help?"
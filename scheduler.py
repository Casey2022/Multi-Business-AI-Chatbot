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
from config import (substitute, question_labels, humanize,
                    RESERVED_SLOT_KEYS)
from db import get_state, set_state, save_appointment, get_recent_messages
from llm import parse_datetime
from llm import parse_datetime, extract_booking_slots

import logging
from contextvars import ContextVar
import re

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

class StaleTurn(Exception):
    """Raised when this turn's state write is refused as out of date.

    It means another request for the same conversation finished while this
    one was still working, so everything computed here — including the reply
    about to be sent — describes a conversation that has moved on. The only
    safe thing to do is throw the work away.
    """


# The revision this turn read at the start. A ContextVar for the same reason
# the log's turn id is one: it has to follow the call stack through a dozen
# functions without every signature growing an argument.
_turn_revision = ContextVar("booking_revision", default=None)


def _save_state(phone, business_id, state, pending=None):
    """Persist booking state, refusing to overwrite a newer write.

    Every set_state inside a booking turn goes through here. On success the
    turn's held revision moves forward, so several saves in one turn (the
    address check saves, then the slot loop saves) still chain correctly.
    """
    expected = _turn_revision.get()
    revision = set_state(phone, business_id, state, pending=pending,
                         expect_revision=expected)
    if revision is None:
        raise StaleTurn(f"state for {phone} changed under us "
                        f"(held revision {expected})")
    _turn_revision.set(revision)
    return revision


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
            # The service decides how long this booking runs, so it has to
            # travel with every availability question — a two-hour job asked
            # about as a forty-five-minute one gets a yes it shouldn't.
            if not calendar_sync.is_slot_available(config, parsed,
                                                   service=service):
                _save_state(phone, business_id, "idle", pending={})
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
            # .get, not [...]: the simulated backend has no external
            # calendar to name, and a KeyError here would fire AFTER the
            # event was written — telling the customer their booking failed
            # while the slot sat there taken.
            calendar_id = config["calendar"].get("calendar_id")
            sync_status = "synced"
        except Exception as e:
            log.warning(f"Calendar write FAILED: {e}")
            # Reset state so the customer isn't stuck mid-booking, and be
            # honest that nothing was booked.
            _save_state(phone, business_id, "idle", pending={})
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

    # Unguarded on purpose. Everywhere else a stale turn is discarded, but
    # the appointment above is already written to the calendar and the
    # database — the conversation IS over, whoever else wrote while we
    # worked. Refusing here would leave the flow believing it still needs a
    # time for a booking that exists.
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
            _save_state(phone, business_id, "idle", pending={})
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
                _save_state(phone, business_id, "collecting", pending=pending)
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
            _save_state(phone, business_id, "collecting", pending=pending)
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
        _save_state(phone, business_id, "collecting", pending=pending)

    return None


# Openers that mean a customer is asking rather than answering. Kept small
# and literal: "none", "no", "yes" are answers, and a sentence ending in "?"
# is not.
QUESTION_OPENERS = ("what", "when", "where", "why", "how", "who", "can you",
                    "could you", "do you", "does", "are you", "is there",
                    "will you", "would you", "any chance")


# Things a customer says that are not answers to anything: the command that
# starts a booking (they're already in one), and the noises people make at a
# bot that has gone quiet. Filing one of these as a slot value is how
# "appointment" became someone's requested date and time.
NON_ANSWERS = {
    "book", "booking", "appointment", "appointments", "order", "schedule",
    "reschedule", "hi", "hello", "hey", "yo", "hello there", "you there",
    "are you there", "anyone there", "anybody there", "still there",
    "help", "ping", "test",
}


def _is_not_an_answer(message):
    """True if this message can't sensibly be the answer to any question."""
    text = _normalise(message)
    return bool(text) and text in NON_ANSWERS


def _looks_like_a_question(message):
    text = (message or "").strip().lower()
    if not text:
        return False
    if text.endswith("?"):
        return True
    return text.startswith(QUESTION_OPENERS) and len(text.split()) > 2


def _answer_mid_booking(phone, message, config, business_id, channel="sms"):
    """Answer a question asked during booking, using the normal Q&A path.

    Best-effort: if the model is unavailable the booking continues without
    an answer rather than failing. Never lets a question become a slot value.

    channel matters: it picks the guardrails. Hardcoded to "webchat", an SMS
    customer asking a question mid-booking got the web rules — three
    sentences and emoji welcome — instead of the 320-character limit their
    carrier actually enforces. The reply was fine on screen and truncated on
    a phone.
    """
    try:
        from llm import get_llm_reply
        history = get_recent_messages(phone, business_id, limit=6)
        return get_llm_reply(message, history=history, config=config,
                             channel=channel, mid_booking=True)
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


def _drop_restated_service(extracted, config):
    """Remove extra-question answers that merely say the service again.

    The opening sentence gets mined for everything at once, which is usually
    a gift — "book a drain cleaning for Tuesday" fills three slots and saves
    three questions. But "I need a leak fixed" came back as

        {"service": "leak repair", "problem_description": "leak"}

    and that second value is not an answer, it's the first one restated. The
    damage is that the slot now counts as filled, so the customer is never
    asked to describe the problem. The owner gets "Problem description:
    leak", which tells them nothing they didn't already know from the
    service line — and worse, the customer, asked a later question out of
    nowhere, answers the one they were expecting instead. In the
    conversation that found this, "a pipe is leaking" ended up filed under
    "Visibility".

    So: an extra answer whose words are already inside the service name is
    dropped, and the question gets asked properly. Containment is the right
    test here (unlike duration matching, where it was the wrong one) because
    the thing being detected IS a restatement — a fuller description like
    "kitchen sink draining slowly" isn't inside "drain cleaning" and
    survives. Directional on purpose: the service inside a longer answer
    means the customer added something, and that's kept.

    Never touches service, datetime, or the address — only the owner's own
    extra questions, which are the ones a stray fragment can silently fill.
    """
    service = _normalise(extracted.get("service") or "")
    if not service:
        return extracted

    protected = set(RESERVED_SLOT_KEYS) | {ADDRESS_SLOT_KEY}
    kept = {}
    for key, value in extracted.items():
        if key in protected or key.startswith("_"):
            kept[key] = value
            continue
        if _normalise(value) and _normalise(value) in service:
            log.info("Dropping %s=%r — it only restates the service %r; "
                     "asking the question instead", key, value,
                     extracted.get("service"))
            continue
        kept[key] = value
    return kept


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


# "9", "9:30", "10 am", "2pm" — an hour, optional minutes, optional meridiem.
SPOKEN_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b")


def _spoken_times(text):
    """Times named in a customer's reply, as (hour, minute or None, am/pm or None)."""
    out = []
    for hour, minute, meridiem in SPOKEN_TIME.findall(text):
        hour = int(hour)
        if not 1 <= hour <= 12:
            continue                       # a date or a house number, not a time
        out.append((hour, int(minute) if minute else None, meridiem or None))
    return out


def _match_offered(message, offered):
    """Which offered time did the customer just accept? None if genuinely unclear.

    A reply like "Thursday at 9" means the Thursday we just named, not the
    next Thursday on the calendar — so this matches against our own offer,
    deterministically, before the model gets a chance to re-read the weekday.

    The awkward case is a half-hour granularity: offer 9:00, 9:30 and 10:00
    and "Thursday at 9" fits two of them. Treating that as ambiguous and
    giving up is what a machine would do; a person would hear "9" and say
    9 o'clock. So an exact minute match wins, then a time on the hour, then
    the earliest — and only a reply naming no time at all against several
    candidates is left as truly unclear.
    """
    from datetime import datetime

    text = (message or "").lower()
    if not text.strip():
        return None

    spoken = _spoken_times(text)
    ranked = []

    for iso in offered:
        when = datetime.strptime(iso, "%Y-%m-%d %H:%M")
        if when.strftime("%A").lower() not in text:
            continue

        if not spoken:
            ranked.append((2, when, iso))          # weekday only
            continue

        hour12 = when.hour % 12 or 12
        for hour, minute, meridiem in spoken:
            if hour != hour12:
                continue
            if meridiem and meridiem != when.strftime("%p").lower():
                continue
            if minute is not None:
                if minute == when.minute:
                    ranked.append((0, when, iso))  # they said the minutes
            else:
                ranked.append((1 if when.minute == 0 else 3, when, iso))

    if not ranked:
        return None

    ranked.sort(key=lambda row: (row[0], row[1]))
    best = ranked[0][0]
    tied = [row for row in ranked if row[0] == best]

    # No time named and more than one slot that day: genuinely ambiguous,
    # so fall through to normal parsing rather than picking for them.
    if best == 2 and len(tied) > 1:
        return None

    return tied[0][2]


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
        _save_state(phone, business_id, "collecting", pending=pending)
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
            service = pending.get("service")
            reason = calendar_sync.slot_rejection_reason(config, parsed,
                                                         service=service)
            if reason:
                alts = calendar_sync.find_alternatives(config, parsed,
                                                       service=service)
                pending.pop("datetime", None)
                pending.pop("datetime_parsed", None)
                # Record what we're about to offer BEFORE saving: anything
                # written into pending after set_state is thrown away.
                pending["_offered"] = list(alts)
                _save_state(phone, business_id, "collecting", pending=pending)
                return _unavailable_message(parsed, alts, config, reason=reason)
        except Exception as e:
            log_cal.warning(f"Availability check failed (continuing): {e}")

    _save_state(phone, business_id, "confirming", pending=pending)
    return _confirmation_question(pending, config)


# Recognized responses in the confirming state. Kept deliberately small —
# anything else is treated as a possible correction and run through extraction.
AFFIRMATIVE = {"yes", "y", "yep", "yeah", "yup", "correct", "right",
               "confirm", "confirmed", "sounds good", "that's right", "ok", "okay",
               "sure", "perfect", "great", "affirmative", "exactly"}
NEGATIVE    = {"no", "n", "nope", "nah", "wrong", "incorrect", "not"}

# Whole phrases people actually use to agree. Matched as substrings, because
# "thats correct" and "yes, I said that is correct already" are both
# agreement and neither is equal to any single word.
AFFIRMATIVE_PHRASES = (
    "thats it", "that's it", "thats correct", "that's correct",
    "thats right", "that's right", "sounds good", "looks good",
    "looks right", "go ahead", "book it", "all good", "you got it",
    "that works", "works for me", "yes please", "correct",
)
NEGATIVE_PHRASES = (
    "thats wrong", "that's wrong", "not right", "not correct", "not it",
    "no thats", "no that's", "change it", "thats not", "that's not",
)


def _normalise(message):
    """Lowercase, strip punctuation, collapse spaces — for matching only."""
    import string
    text = (message or "").lower()
    text = "".join(ch for ch in text if ch not in string.punctuation or ch == "'")
    return " ".join(text.split())


def _is_negative(message):
    """Does this reply reject what was just read back?

    Checked before the affirmative, because "no, that's correct" is a
    correction and "not right" contains "right".
    """
    text = _normalise(message)
    if not text:
        return False
    if text in NEGATIVE:
        return True
    if any(phrase in text for phrase in NEGATIVE_PHRASES):
        return True
    return text.split()[0] in NEGATIVE


def _is_affirmative(message):
    """Does this reply accept what was just read back?

    Exact membership was the old test, and it failed on everything a real
    customer types: "thats correct", "thats it", "yes, I said that is
    correct already". Now: the whole message, a known phrase anywhere in it,
    or an opening word that means yes.
    """
    text = _normalise(message)
    if not text or _is_negative(message):
        return False
    if text in AFFIRMATIVE:
        return True
    if any(phrase in text for phrase in AFFIRMATIVE_PHRASES):
        return True
    return text.split()[0] in AFFIRMATIVE


# Answers that mean "nothing to add" — but only where the question offered
# that as an option. See _confirmation_question.
SKIPPABLE_ANSWERS = {"none", "n/a", "na", "no", "nope", "nothing", "-", "n"}

# Cues in an owner's prompt that invite a non-answer. Matched loosely and on
# purpose: an owner writes "(or reply 'none')" or "if any" or "optional" in
# whatever words they like, and the cost of missing one is a read-back line
# saying "Notes: none", which is mild. The cost of the opposite mistake —
# treating a real "no" as an absence — is a detail the customer never got to
# check, which is not.
OPT_OUT_CUES = ("none", "n/a", "nothing", "if any", "optional",
                "leave blank", "skip")


def _slots_that_invite_skipping(config):
    """Keys whose prompt offers the customer a way to say 'nothing'."""
    keys = set()
    for question in (config.get("booking", {}).get("extra_questions") or []):
        prompt = (question.get("prompt") or "").lower()
        if any(cue in prompt for cue in OPT_OUT_CUES):
            keys.add(question.get("key"))
    return keys


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

    # Every extra slot that was actually filled. Skip the bookkeeping keys,
    # and skip a "none" only where the question invited one.
    #
    # The subtlety: this used to skip any answer of "none", "no", "nothing"
    # and friends, by value alone. That's right for "Anything else we should
    # know? (or reply 'none')" — reading back "Notes: none." is noise. It's
    # wrong for "Can the issue be clearly seen?", where "no" IS the answer,
    # and hiding it meant the customer confirmed a booking containing an
    # answer they couldn't see — the exact thing this function exists to
    # prevent, per the paragraph above.
    #
    # So the test is the question, not the answer. A prompt that offers a
    # way out treats a "none" as taking it; every other prompt gets its
    # answer read back whatever the answer was.
    skip_keys = {"service", "datetime", "datetime_parsed",
                 *INTERNAL_PENDING_KEYS}
    labels    = question_labels(config)
    optional  = _slots_that_invite_skipping(config)

    for key, value in pending.items():
        if key in skip_keys:
            continue
        if not value:
            continue
        if key in optional and str(value).strip().lower() in SKIPPABLE_ANSWERS:
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
        # Say so NOW, on the message that caused it. Leaving the unparseable
        # value in place meant the slot counted as filled: the flow moved on
        # to the remaining questions and only complained about the date
        # three messages later, by which point the customer had answered two
        # other things and had no idea which reply the error belonged to.
        # That is exactly what happened when someone typed "appointment" at
        # the "when works for you?" prompt.
        log.info("Unparseable datetime %r — clearing it and asking again",
                 pending.get("datetime"))
        pending.pop("datetime", None)
        pending.pop("datetime_parsed", None)
        _save_state(phone, business_id, "collecting", pending=pending)
        BOOKING = config.get("booking", {})
        return substitute(
            BOOKING.get(
                "fallback_after_bad_date",
                "Sorry, I couldn't read that as a date and time. "
                "Try something like 'Tuesday at 3pm'."
            ),
            config
        )

    try:
        service = pending.get("service")
        reason = calendar_sync.slot_rejection_reason(config, candidate,
                                                     service=service)
        if not reason:
            return None
        alts = calendar_sync.find_alternatives(config, candidate,
                                               service=service)
        pending.pop("datetime", None)
        pending["_offered"] = list(alts)          # before the save, not after
        _save_state(phone, business_id, "collecting", pending=pending)
        return _unavailable_message(candidate, alts, config, reason=reason)
    except Exception as e:
        log_cal.warning(f"Early availability check failed: {e}")
        return None

def handle_booking(phone, message, config, business_id, prefilled=None,
                   channel="sms"):
    """Advance the booking flow and log the reply.

    Wraps the real handler so every outgoing scheduler message is visible in
    the logs, the way LLM replies already are. Without this, booking prompts,
    rejections, and confirmations were the one part of the conversation you
    couldn't see without opening a browser.
    """
    try:
        reply = _handle_booking_inner(phone, message, config, business_id,
                                      prefilled, channel=channel)
    except StaleTurn as e:
        # Another request for this conversation finished while we were
        # working, so our reply is about a conversation that has moved on.
        # Sending it is how the customer ends up answering the same question
        # twice. Ask what's actually outstanding now instead.
        log.warning("Discarding a stale turn for %s: %s", phone, e)
        reply = _current_question(phone, business_id, config)
    log.debug(f"Reply: {reply!r}")
    return reply


def _current_question(phone, business_id, config):
    """The question this conversation is waiting on, read fresh.

    Used when a turn is thrown away: whatever we computed is out of date,
    but the database knows where the booking actually stands.
    """
    current = get_state(phone, business_id)
    if current["state"] == "idle":
        return ("Sorry — that took longer than it should have. "
                "What can I help you with?")
    slots   = get_slot_definitions(config)
    missing = _first_missing_slot(current["pending"], slots)
    if missing:
        return substitute(missing["prompt"], config)
    return _confirmation_question(current["pending"], config)

def _handle_booking_inner(phone, message, config, business_id, prefilled=None,
                          channel="sms"):
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

    # Everything this turn writes is checked against the revision we read
    # here. If it moved, this turn lost the race and is discarded.
    _turn_revision.set(current.get("revision", 0))

    # --- Cancel: transversal, checked before anything else ---
    if text in ("cancel", "stop", "nevermind", "never mind"):
        _save_state(phone, business_id, "idle", pending={})
        noun = BOOKING.get("noun", "appointment")
        return substitute(
            BOOKING.get("cancel_reply", f"No problem — {noun} cancelled."),
            config
        )

    # --- Opening turn: greet, then extract from the triggering message ---
    # The trigger itself may carry information ("book a drain cleaning").
    if state == "idle":
        # Slots the intent classifier already found in the triggering
        # message — no point extracting them twice. Filtered first: one
        # sentence answering two questions usually means it only answered
        # one (see _drop_restated_service).
        if prefilled:
            pending.update(_drop_restated_service(prefilled, config))
        else:
            extracted = extract_booking_slots(message, slots, config)
            pending.update(_drop_restated_service(extracted, config))

        early = _check_datetime_now(phone, business_id, pending,
                                    prefilled or {}, config)
        if early:
            return early

        _save_state(phone, business_id, "collecting", pending=pending)
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

            if _is_affirmative(message):
                # They agreed to the resolved address, so store that rather
                # than the fragment they typed — it's the version with a city
                # on it, and it's what a van needs.
                pending[awaiting["key"]] = awaiting["formatted"]
                checks = pending.get("_checks") or {}
                checks[awaiting["key"]] = awaiting["check"]
                pending["_checks"] = checks
                pending.pop("_address_attempts", None)
                log.info("Customer confirmed %r", awaiting["formatted"])
                _save_state(phone, business_id, "collecting", pending=pending)
                return _ask_next_or_finalize(phone, business_id, pending,
                                             config, slots)

            if _is_negative(message):
                attempts = pending.get("_address_attempts", 0) + 1
                pending["_address_attempts"] = attempts
                pending.pop(awaiting["key"], None)
                log.info("Customer rejected %r (attempt %d)",
                         awaiting["formatted"], attempts)
                _save_state(phone, business_id, "collecting", pending=pending)
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

            # Neither yes nor no. Only treat it as a replacement address if
            # it plausibly contains one — "you just listed the address" is a
            # complaint, and filing it as the address (which is what the
            # terse-reply fallback did) turns one missed cue into two.
            if any(ch.isdigit() for ch in message):
                pending.pop(awaiting["key"], None)
            else:
                pending["_address_confirm"] = awaiting
                _save_state(phone, business_id, "collecting", pending=pending)
                return (f"Sorry — I want to be sure before I book it. Is "
                        f"{awaiting['formatted']} the right address? "
                        f"Yes or no is fine.")

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
                _save_state(phone, business_id, "collecting", pending=pending)
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
                answer = _answer_mid_booking(phone, message, config,
                                             business_id, channel=channel)
                _save_state(phone, business_id, "collecting", pending=pending)
                prompt = substitute(missing["prompt"], config)
                return f"{answer} {prompt}" if answer else prompt
            if missing and _is_not_an_answer(message):
                # A nudge or the booking command itself. Storing it would
                # fill a slot with a word the customer never meant as an
                # answer; the polite thing is to repeat what we asked.
                log.info("Non-answer %r mid-booking — re-asking '%s'",
                         message.strip()[:40], missing["key"])
                return substitute(missing["prompt"], config)
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
        _save_state(phone, business_id, "collecting", pending=pending)
        return _ask_next_or_finalize(phone, business_id, pending, config, slots)
    # --- Confirming: read-back accepted, rejected, or corrected ---
    if state == "confirming":
        if _is_affirmative(message):
            return _finalize_booking(phone, business_id, pending, config)

        # Not a plain yes — see if they're correcting something
        # ("no, 2pm instead" / "make it Thursday").
        extracted = extract_booking_slots(
            message, slots, config, already_filled=pending
        )
        if extracted:
            pending.update(extracted)
            pending.pop("datetime_parsed", None)   # force a re-parse
            _save_state(phone, business_id, "collecting", pending=pending)
            return _ask_next_or_finalize(phone, business_id, pending, config, slots)

        if _is_negative(message):
            pending.pop("datetime", None)
            pending.pop("datetime_parsed", None)
            _save_state(phone, business_id, "collecting", pending=pending)
            return "No problem — what date and time would work better?"

        # Unclear response — ask again rather than guessing.
        return _confirmation_question(pending, config)

    # --- Unknown state: reset gracefully ---
    log.warning(f"Unknown state '{state}' for {phone} — resetting.")
    _save_state(phone, business_id, "idle", pending={})
    return "Something went wrong on my end. Let's start over — how can I help?"
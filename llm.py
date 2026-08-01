# llm.py — Claude integration, system prompt assembly, and date parsing.
#
# Three responsibilities:
#   1. build_system_prompt: assembles the per-request system prompt from config
#   2. get_llm_reply: runs the full LLM+RAG path for a customer message
#   3. parse_datetime: structured extraction of dates from natural language
#
# Config is passed explicitly per-request — no module-level CONFIG global.

import os
import re
from anthropic import Anthropic
from config import substitute
from rag import retrieve

# ---------------------------------------------------------------------------
# Module-level constants (not business-specific — safe at import time)
# ---------------------------------------------------------------------------

MODEL = "claude-haiku-4-5-20251001"

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


# ---------------------------------------------------------------------------
# System prompt assembly
# ---------------------------------------------------------------------------

def extract_booking_slots(message, slots, config, already_filled=None):
    """Extract any booking slot values present in a customer message.

    Returns a dict of {slot_key: extracted_value} for slots found in this
    message. Slots not mentioned are omitted entirely (not set to null) so
    merging never overwrites a previously-filled slot with nothing.

    This function EXTRACTS ONLY. It never saves, never confirms, never
    decides the booking is complete — the state machine owns all of that.

    `already_filled` lets the prompt tell the model what we have, so a
    correction ("actually make it Thursday") updates the right slot.
    """
    import json

    business = config["business"]
    already_filled = already_filled or {}

    slot_lines = "\n".join(
        f'- "{s["key"]}": {s["description"]}' for s in slots
    )
    filled_text = ""
    if already_filled:
        filled_text = (
            "\nAlready collected (include a key ONLY if this message changes it):\n"
            + "\n".join(f'- "{k}": {v!r}' for k, v in already_filled.items())
        )

    prompt = f"""You extract structured booking information from a customer message
for {business['name']}.

Slots to look for:
{slot_lines}
{filled_text}

Rules:
- Respond with ONLY a JSON object. No preamble, no markdown fences.
- Include a key ONLY if this message clearly provides or changes that value.
- Omit keys the message doesn't mention. Do not guess or invent values.
- For "datetime", copy the customer's own phrasing (e.g. "next Friday at 3pm").
- If the message provides nothing, respond with: {{}}
- If the customer is CORRECTING a value that's already collected, return the
  COMPLETE updated value including any parts they didn't change. Example: if
  "next Wednesday at 2pm" is already collected and they say "actually
  Thursday", return "Thursday at 2pm" — not just "Thursday".

Examples:
Message: "I'd like a dozen vanilla cupcakes with chocolate frosting"
{{"service": "dozen vanilla cupcakes", "customization": "chocolate frosting"}}

Message: "next Wednesday at 9am"
{{"datetime": "next Wednesday at 9am"}}

Message: "actually make it Thursday instead"
{{"datetime": "Thursday"}}

Message: "sounds good, thanks"
{{}}

Message: "{message}"
"""
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # Strip markdown fences if the model adds them despite instructions.
        raw = raw.replace("```json", "").replace("```", "").strip()

        extracted = json.loads(raw)
        if not isinstance(extracted, dict):
            print(f"[llm] Slot extraction returned non-dict: {raw!r}")
            return {}

        # Keep only known slot keys with non-empty values — the model
        # occasionally invents a key or returns null despite instructions.
        valid_keys = {s["key"] for s in slots}
        cleaned = {
            k: str(v).strip()
            for k, v in extracted.items()
            if k in valid_keys and v not in (None, "", "null")
        }
        print(f"[llm] Slots extracted: {cleaned}")
        return cleaned

    except json.JSONDecodeError as e:
        print(f"[llm] Slot extraction JSON error: {e} | raw={raw!r}")
        return {}
    except Exception as e:
        print(f"[llm] Slot extraction error: {e}")
        return {}

def build_system_prompt(config, channel="sms"):

    from datetime import datetime
    today_str = datetime.now().strftime("%A, %B %d, %Y")

    """Construct the system prompt from a business config dict.

    `channel` selects which channel-specific guardrails to append to the
    universal ones. Currently "sms" or "voice"; defaults to "sms".
    Called per-request so every request gets the right business's prompt.
    """
    business = config["business"]
    bot      = config["bot"]
    services = config["services"]
    faq      = config.get("faq", [])

    services_text = "\n".join(f"- {s}" for s in services)

    faq_text = ""
    if faq:
        faq_lines = [
            f"Q: {item['question']}\nA: {item['answer']}"
            for item in faq
        ]
        faq_text = "\n\nFrequently asked questions:\n" + "\n\n".join(faq_lines)

    # Describe the booking process so the LLM answers consistently with it
    # and never contradicts what the business actually collects.
    booking = config.get("booking", {})
    booking_text = ""
    if booking:
        noun = booking.get("noun", "appointment")
        extras = booking.get("extra_questions", [])
        extras_text = ""
        if extras:
            asked = "; ".join(q["prompt"] for q in extras)
            extras_text = (f" During booking we also ask: {asked} — so customers "
                           f"don't need to provide these details in advance.")
        booking_text = (
            f"\n\nBooking: customers can start a {noun} by texting 'book' or "
            f"'{noun}' as a short command. The booking flow — a separate system, "
            f"not you — collects the service, date/time, and any details "
            f"below.{extras_text}\n"
            f"CRITICAL: You cannot place, schedule, confirm, or cancel {noun}s "
            f"yourself. You have no access to the {noun} system. If a customer "
            f"describes what they want, acknowledge it warmly and tell them to "
            f"text '{noun}' to start — do NOT collect booking details (dates, "
            f"times, names) yourself, and NEVER state or imply that a {noun} "
            f"has been placed, confirmed, or scheduled. A {noun} only exists "
            f"once the booking flow has run."
        )
    # Combine universal guardrails with channel-specific ones.
    # The isinstance check keeps backwards-compat with older YAML configs
    # that had guardrails as a single string rather than a dict.
    guardrails = bot.get("guardrails", {})
    if isinstance(guardrails, str):
        guardrails_text = guardrails
    else:
        universal        = guardrails.get("universal", "")
        channel_specific = guardrails.get(channel, "")
        guardrails_text  = f"{universal}\n\n{channel_specific}".strip()

    return f"""You are an SMS assistant for {business['name']}.
               Today is {today_str}.

    

Business facts:
- Name: {business['name']}
- Phone: {business['phone']}
- Address: {business['address']}
- Hours: {business['hours']}
- Service area: {business.get('service_area', 'N/A')}

Services we offer:
{services_text}
{faq_text}
{booking_text}

Persona: {bot['persona']}

Behavioral rules: {guardrails_text}"""


# ---------------------------------------------------------------------------
# Main LLM + RAG reply path
# ---------------------------------------------------------------------------

def get_llm_reply(message, history=None, config=None, channel="sms"):
    """Send the customer's message to Claude with full context and return reply.

    Context assembled per-request:
      1. System prompt built from config (business facts, persona, guardrails)
      2. Retrieved document chunks from RAG (if any are close enough)
      3. Recent conversation history from the database
      4. The current message

    `config` is required — passing None will raise a clear error rather than
    silently serving the wrong business.
    """
    if config is None:
        raise ValueError("get_llm_reply requires a config dict — "
                         "did app.py forget to pass it?")

    # Build the system prompt fresh for this request's channel and business.
    system_for_call = build_system_prompt(config, channel=channel)

    # RAG retrieval: find document chunks relevant to this specific message.
    # retrieve() now takes config so it queries the right business collection.
    retrieved_chunks = retrieve(message, config)

    if retrieved_chunks:
        context_block = "\n\n".join(
            f"[from business documents, relevance={1 - dist:.2f}]\n{chunk}"
            for chunk, dist in retrieved_chunks
        )
        effective_system = (
            f"{system_for_call}\n\n"
            f"--- RELEVANT BUSINESS DOCUMENT EXCERPTS ---\n"
            f"Use these excerpts to answer the customer accurately. "
            f"If they contain the answer, use it; if not, follow the "
            f"general guardrails (give the phone number, don't speculate).\n\n"
            f"{context_block}"
        )
    else:
        effective_system = system_for_call

    # Build the messages list: history first, then the current message.
    # history.copy() avoids mutating the caller's list.
    messages = history.copy() if history else []
    messages.append({"role": "user", "content": message})

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=200,
            system=effective_system,
            messages=messages,
        )
        reply = response.content[0].text.strip()
        print(f"[llm] Claude replied: {reply!r}")
        return reply

    except Exception as e:
        print(f"[llm] ERROR calling Claude: {e}")
        return ("Sorry, I'm having trouble right now. "
                "Please try again or call us directly.")


# ---------------------------------------------------------------------------
# Structured date extraction
# ---------------------------------------------------------------------------

def parse_datetime(user_input):
    """Extract a datetime from a natural-language string using Claude.

    CRITICAL: the prompt must state today's date. The API call has no clock —
    without an explicit anchor, the model infers "today" from whatever dates
    appear in the examples, which caused a real bug (booked June instead of
    July because the examples were written in June).
    """
    from datetime import datetime, timedelta

    now = datetime.now()
    today_str = now.strftime("%A, %B %d, %Y")  # e.g. "Sunday, July 19, 2026"

    # Compute example dates dynamically so they can never go stale.
    tomorrow  = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    # Next Tuesday: days until Tuesday (weekday 1), minimum 1 day ahead.
    days_ahead = (1 - now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    next_tuesday = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    prompt = f"""Today is {today_str}.

Extract the date and time from the user's message, interpreting relative
dates ("tomorrow", "next Tuesday") against today's date above.
Reply with ONLY a datetime in this exact format: YYYY-MM-DD HH:MM
If no valid date/time can be determined, reply with exactly: NONE

Examples (relative to today):
User: "next Tuesday at 3pm" -> {next_tuesday} 15:00
User: "tomorrow morning" -> {tomorrow} 09:00
User: "whenever" -> NONE

User: "{user_input}"
"""
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=20,
            messages=[{"role": "user", "content": prompt}]
        )
        result = response.content[0].text.strip()
        print(f"[llm] Date parse result: {result!r}")

        if result == "NONE":
            return None

        datetime.strptime(result, "%Y-%m-%d %H:%M")
        return result

    except Exception as e:
        print(f"[llm] Date parse error: {e}")
        return None
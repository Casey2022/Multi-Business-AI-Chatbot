# conversation_eval.py — scripted conversations, run against the real booking flow.
#
#   python3 conversation_eval.py              # every scenario
#   python3 conversation_eval.py offer        # scenarios matching "offer"
#   python3 conversation_eval.py -v           # print every transcript, not just failures
#
# Why this exists: the booking flow's bugs live in the seam between the LLM
# extractor and the state machine, and neither half is wrong on its own. A
# unit test with a stubbed extractor can't see them, because the stub encodes
# what I assume the model does. So the extractor here is REAL — that's the
# thing under test — while the calendar, the database and the geocoder are
# faked so a run is repeatable and costs nothing but tokens. The calendar
# is the REAL simulated backend (calendar_sim) rather than a fixture, so the
# harness exercises the same availability code the sandbox demo will — and a
# fixture can't drift away from the rules while nobody is looking.
#
# The scripts are taken from real transcripts in the messages table, not
# invented. Customers answer two slots at once, say "pipe leak" when the
# config says "leak repair", accept an offered time in shorthand, and ask
# questions in the middle of the form. Guessing at that would have produced
# a politer, less useful test suite.
#
# Assertions are PROPERTIES, not exact strings. "The bot never says the same
# sentence twice in a row" survives rewording; "reply == 'What service do you
# need?'" breaks the first time someone improves the copy.

import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from logging_setup import setup_logging
setup_logging(level="WARNING")          # the transcript is the output, not the log

import db
db.DB_PATH = Path(tempfile.mkdtemp()) / "eval.db"      # never touch the real one

import geocode
from config import load_config
from db import init_db, set_state
import scheduler


# ---------------------------------------------------------------------------
# A calendar that isn't Google
# ---------------------------------------------------------------------------
#
# First real use of the backend facade: the scheduler asks calendar_sync for
# availability exactly as it does in production, and gets a module that
# answers from a list in memory. No network, no service account, same answers
# every run.

def _seed_busy(scenario, dates):
    """Fill the temp database with the bookings a scenario needs.

    The simulated calendar reads busy time out of the appointments table, so
    "this slot is taken" is expressed here the way it is in production: by
    there being an appointment. No fixture calendar to keep in step with the
    real rules.
    """
    from db import get_connection, save_appointment

    conn = get_connection()
    conn.execute("DELETE FROM appointments")      # each scenario starts clean
    conn.commit()
    conn.close()

    for slot in scenario.get("busy", []):
        save_appointment("eval_seed", "existing job", slot.format(**dates), 1)

    # A blocked day is a day with no room left in it: book every hour the
    # business is open. Bob's 45-minute buffer does the rest.
    for day in scenario.get("busy_dates", []):
        date = day.format(**dates)
        for hour in range(9, 18):
            save_appointment("eval_seed", "existing job",
                             f"{date} {hour:02d}:00", 1)


# ---------------------------------------------------------------------------
# Checks — each one is a defect from notes/conversation_defects.md, inverted
# ---------------------------------------------------------------------------

def no_repeated_reply(t):
    """The bot never sends the identical sentence twice in a row.

    A second ask should acknowledge what was understood and narrow the
    question. Repeating the first ask word for word tells the customer
    nothing landed, even when it did.
    """
    bots = [m for who, m in t.exchange if who == "bot"]
    for a, b in zip(bots, bots[1:]):
        if a.strip() == b.strip():
            return f"said this twice running: {a[:70]!r}"


def service_in_catalog(t):
    """A booked service resembles something the business actually offers."""
    if not t.booked:
        return None
    service = " ".join((t.booked.get("service") or "").lower().split())
    offered = [" ".join(s.lower().split()) for s in t.config.get("services", [])]
    if service not in offered:
        close = [s for s in offered if s in service or service in s]
        hint = f" (closest: {close[0]!r})" if close else ""
        return (f"booked service {t.booked['service']!r} is not one of "
                f"{t.config.get('services')}{hint}")


def _offered_times(text):
    """Every 'Wednesday, September 23 at 9:00 AM' in a reply, as (month, day, hour, minute)."""
    import re
    from datetime import datetime
    out = []
    for m in re.finditer(r"\w+day, (\w+) (\d+) at (\d+):(\d\d) (AM|PM)", text):
        month = datetime.strptime(m.group(1), "%B").month
        hour = int(m.group(3)) % 12 + (12 if m.group(5) == "PM" else 0)
        out.append((month, int(m.group(2)), hour, int(m.group(4))))
    return out


def offer_honoured(t):
    """A time the bot offered and the customer accepted is the time it books.

    Two ways this goes wrong, and the quiet one is worse. Loudly: the bot
    refuses the slot it just proposed. Silently: it books a different date
    entirely — the customer said "Wednesday at 9" meaning the Wednesday on
    offer, and the bot resolved that against the calendar instead of against
    its own sentence. Nobody notices until a van turns up a week early.
    """
    from datetime import datetime

    offered = []
    for i, (who, text) in enumerate(t.exchange):
        if who == "bot" and "I have:" in text:
            offered = _offered_times(text)
            if i + 2 < len(t.exchange):
                reply = t.exchange[i + 2][1].lower()
                if reply.startswith("sorry") and "already booked" in reply:
                    return ("offered alternatives, the customer picked one, and "
                            f"it was refused: {t.exchange[i + 2][1][:70]!r}")

    if not offered or not t.booked:
        return None

    when = datetime.strptime(t.booked["when"], "%Y-%m-%d %H:%M")
    got = (when.month, when.day, when.hour, when.minute)
    if got not in offered:
        pretty = ", ".join(f"{m}/{d} {h:02d}:{mi:02d}" for m, d, h, mi in offered)
        return (f"booked {when:%b %d at %H:%M} — not one of the times it "
                f"offered ({pretty})")


def question_not_absorbed(t):
    """A customer's mid-booking question never becomes a slot's value."""
    asked = [m.strip() for who, m in t.exchange
             if who == "cust" and m.strip().endswith("?")]
    if not asked:
        return None

    stored = {str(v).strip() for v in (t.pending or {}).values()}
    if t.booked:
        stored |= {str(v).strip() for v in (t.booked.get("details") or {}).values()}
        stored.add(str(t.booked.get("service") or "").strip())

    for question in asked:
        if question in stored:
            return f"stored a question as an answer: {question!r}"
        # A question that got absorbed and then corrected leaves nothing in
        # the final state — but the confirmation it appeared in is evidence.
        for who, text in t.exchange:
            if who != "bot":
                continue
            if "just to confirm" not in text.lower():
                continue
            if question.rstrip("?") in text:
                return (f"read a question back as a booking detail: "
                        f"{question!r}")


def no_stacked_greeting(t):
    """One hello per reply, and no 'Happy to help with for next Tuesday'."""
    for who, text in t.exchange:
        if who != "bot":
            continue
        if "with for" in text.lower():
            return f"broken acknowledgement: {text[:70]!r}"
        if text.lower().count("happy to") > 1:
            return f"two greetings in one reply: {text[:70]!r}"


def booking_completed(t):
    """The conversation ends in a booking."""
    if not t.booked:
        return "never reached a booking"




def answer_not_duplicated(t):
    """One customer sentence shouldn't be filed as the answer to two slots.

    'pipe leak' arriving as both the service AND the problem description
    isn't wrong exactly, but it means the flow asked a question it already
    had the answer to, and the owner reads the same words twice.
    """
    values = {}
    source = dict(t.pending or {})
    if t.booked:
        source.update(t.booked.get("details") or {})
        source["service"] = t.booked.get("service")
    for key, value in source.items():
        if key.startswith("_") or not value:
            continue
        text = " ".join(str(value).lower().split())
        values.setdefault(text, []).append(key)
    for text, keys in values.items():
        if len(keys) > 1:
            return f"{text!r} stored in {len(keys)} slots: {', '.join(keys)}"


def question_gets_a_real_reply(t):
    """A question mid-booking gets something new, not a form step replayed.

    The bot can't be required to know the answer. It can be required not to
    pretend the question never happened — which is what replaying a sentence
    it has already sent amounts to.
    """
    for i, (who, text) in enumerate(t.exchange):
        if who != "cust" or not text.strip().endswith("?"):
            continue
        if i + 1 >= len(t.exchange):
            continue
        reply = t.exchange[i + 1][1].strip()
        earlier = [m.strip() for w, m in t.exchange[:i] if w == "bot"]
        if reply in earlier:
            return (f"answered {text!r} by replaying an earlier line: "
                    f"{reply[:60]!r}")


def no_command_advice_mid_booking(t):
    """Mid-booking, the bot must never tell them to text a command to start.

    They have started. On 2026-09-16 a customer three questions into a
    booking asked "Can you come out tomorrow?" and was told to "text
    'appointment' and our booking system will walk you through it" — advice
    that was both wrong and, to someone already answering its questions,
    slightly insulting. The Q&A prompt didn't know a booking was in
    progress; now it does.
    """
    started = False
    for who, text in t.exchange:
        if who == "cust":
            started = True
            continue
        if not started:
            continue
        low = text.lower()
        for phrase in ("text 'appointment'", 'text "appointment"',
                       "text 'book'", "text 'order'", "texting 'book'",
                       "to start the booking", "start a booking by texting"):
            if phrase in low:
                return f"told a customer mid-booking to {phrase}: {text[:70]!r}"


def bad_date_rejected_immediately(t):
    """An unreadable date is queried on the message that caused it.

    The complaint used to arrive three questions later, after the flow had
    moved on to other slots — so the customer saw "I couldn't read that as a
    date" directly after answering something that wasn't a date at all, and
    had no way to tell which of their messages was the problem.
    """
    marker = "couldn't read that as a date"
    for i, (who, text) in enumerate(t.exchange):
        if who != "bot" or marker not in text.lower():
            continue
        # The customer message this complains about is the one immediately
        # before it. Find what the bot asked before THAT.
        if i < 2:
            continue
        asked = t.exchange[i - 2][1].lower()
        if "when" not in asked and "date" not in asked and "time" not in asked:
            return (f"complained about a date in reply to {t.exchange[i-1][1]!r}, "
                    f"which answered {asked[:60]!r}")


CHECKS = {f.__name__: f for f in (
    no_repeated_reply, service_in_catalog, offer_honoured,
    question_not_absorbed, no_stacked_greeting, booking_completed,
    answer_not_duplicated, question_gets_a_real_reply,
    no_command_advice_mid_booking, bad_date_rejected_immediately,
)}


# ---------------------------------------------------------------------------
# Scenarios — every script below is a real customer's, from the transcripts
# ---------------------------------------------------------------------------

ALWAYS = ["no_repeated_reply", "no_stacked_greeting", "question_not_absorbed",
          "answer_not_duplicated", "question_gets_a_real_reply",
          "no_command_advice_mid_booking", "bad_date_rejected_immediately"]

SCENARIOS = [
    {
        "name": "the booking command typed mid-booking (2026-09-16)",
        "why":  "Asked when they wanted the visit, the customer typed "
                "'appointment' — the word that starts a booking, not an "
                "answer to anything. It was filed as the requested date and "
                "time, which made the slot look answered, so the flow moved "
                "on and only complained about the date three questions "
                "later.",
        "script": ["I'd like to schedule something", "leak repair",
                   "1738 William St, Rochester NY", "appointment",
                   "Monday at 11", "a pipe is leaking", "no", "yes"],
        "checks": ALWAYS,
    },
    {
        "name": "nudging a bot that has gone quiet (2026-09-16)",
        "why":  "'Hello?' mid-booking was answered with advice to text "
                "'appointment' to start booking — which they were already "
                "doing — and the nudge itself risks being filed as a slot "
                "answer.",
        "script": ["book", "drain cleaning", "1738 William St, Rochester NY",
                   "Hello?", "Can you come out Monday at 11?",
                   "a pipe is leaking", "no", "yes"],
        "checks": ALWAYS,
    },
    {
        "name": "short service answer — 'pipe leak' (2026-09-15)",
        "why":  "Answered with a service the config words differently. Both "
                "this and the address were filed elsewhere while service "
                "stayed empty, and the same prompt went out three times.",
        "script": ["book", "pipe leak", "1738 William St, Rochester NY",
                   "Monday at 11", "yes"],
        "checks": ALWAYS + ["service_in_catalog"],
    },
    {
        "name": "accepting an offered alternative (2026-09-15)",
        "why":  "The bot offered Wednesday the 23rd, the customer said "
                "'Wednesday at 9', and it resolved against the calendar "
                "instead of its own offer.",
        "busy_dates": ["{d0}", "{d1}", "{d2}", "{d3}", "{d4}", "{d5}",
                       "{d6}", "{d7}"],
        "script": ["book", "drain cleaning", "12 Elm St, Rochester NY",
                   "{plus2day} at 1", "{offer}", "clogged sink", "yes"],
        "checks": ALWAYS + ["offer_honoured"],
    },
    {
        "name": "booking opener carries the date (2026-09-10)",
        "why":  "'Happy to help with for next wednesday. Happy to schedule an "
                "appointment!' — two greetings and a broken preposition.",
        "script": ["I'd like to book an appointment for next wednesday",
                   "drain cleaning", "12 Elm St, Rochester NY", "yes"],
        "checks": ALWAYS,
    },
    {
        "name": "question asked mid-booking (2026-08-02)",
        "why":  "A customer asked two real questions during the form and got "
                "the same prompt back three times.",
        "script": ["book", "drain cleaning", "12 Elm St, Rochester NY",
                   "Tuesday at 2", "Do you charge a call-out fee?",
                   "clogged sink", "yes"],
        "checks": ALWAYS,
    },
    {
        "name": "agreeing without saying the word yes (2026-09-16)",
        "why":  "A real customer answered the address read-back with "
                "'thats it' and the confirmation with 'thats correct'. "
                "Neither was recognised: the first got filed AS the address, "
                "the second made the bot repeat itself three times.",
        "script": ["book", "leak repair", "522 Penbrooke Drive", "thats it",
                   "Tuesday at 2", "leaky pipe", "thats correct"],
        "checks": ALWAYS + ["booking_completed"],
    },
    {
        "name": "pushing back on the read-back (2026-09-16)",
        "why":  "'you just listed the address' is a complaint, not an "
                "address — it must not be stored as one.",
        "script": ["book", "leak repair", "522 Penbrooke Drive",
                   "you just listed the address",
                   "yes", "Tuesday at 2", "leaky pipe", "yes"],
        "checks": ALWAYS,
    },
    {
        "name": "everything in one breath",
        "why":  "Customers volunteer several slots at once; the flow should "
                "not re-ask for what it already has.",
        "script": ["I need drain cleaning at 12 Elm St, Rochester NY on "
                   "Tuesday at 2, the kitchen sink is clogged", "yes"],
        "checks": ALWAYS + ["service_in_catalog"],
    },
]


# ---------------------------------------------------------------------------
# Running one
# ---------------------------------------------------------------------------

class Transcript:
    def __init__(self, config):
        self.config = config
        self.exchange = []
        self.booked = None
        self.pending = {}


def _dates():
    """Concrete near-future dates, so scripts don't drift with the calendar."""
    from datetime import datetime, timedelta
    now = datetime.now()
    plus2 = now + timedelta(days=2)
    out = {
        "plus2": plus2.strftime("%Y-%m-%d"),
        "plus2day": plus2.strftime("%A"),
    }
    for n in range(0, 10):
        out[f"d{n}"] = (now + timedelta(days=n)).strftime("%Y-%m-%d")
    return out


def run(scenario, config, verbose=False):
    from db import get_state

    dates = _dates()
    _seed_busy(scenario, dates)

    phone = f"eval_{abs(hash(scenario['name'])) % 10**8}"
    set_state(phone, 1, "idle", pending={})

    t = Transcript(config)
    booked = {}

    import db as _db
    original_save = _db.save_appointment
    def capture(phone_, service, when, business_id, details=None, **kw):
        booked.update(service=service, when=when, details=details)
        return original_save(phone_, service, when, business_id,
                             details=details, **kw)
    _db.save_appointment = capture
    scheduler.save_appointment = capture

    try:
        for line in scenario["script"]:
            if line == "{offer}":
                # Answer using the bot's own last offer, the way a customer
                # would: name the weekday and hour it just proposed.
                last = t.exchange[-1][1]
                line = _shorthand_for_first_offer(last) or "yes"
            else:
                line = line.format(**dates)

            t.exchange.append(("cust", line))
            reply = scheduler.handle_booking(phone, line, config, 1)
            t.exchange.append(("bot", reply))

        t.pending = get_state(phone, 1)["pending"]
        t.booked = booked or None
    finally:
        _db.save_appointment = original_save
        scheduler.save_appointment = original_save

    failures = []
    for name in scenario["checks"]:
        problem = CHECKS[name](t)
        if problem:
            failures.append((name, problem))
    return t, failures


def _shorthand_for_first_offer(reply):
    """Turn 'I have: Wednesday, September 23 at 9:00 AM · …' into 'Wednesday at 9'."""
    import re
    m = re.search(r"I have:\s*(\w+day), \w+ \d+ at (\d+):(\d\d) (AM|PM)", reply)
    if not m:
        return None
    return f"{m.group(1)} at {m.group(2)}"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose = "-v" in sys.argv
    repeat = 1
    for flag in sys.argv[1:]:
        if flag.startswith("--repeat="):
            repeat = int(flag.split("=", 1)[1])

    init_db()
    config = load_config("config/bobs_plumbing.yaml")
    config.setdefault("calendar", {})["provider"] = "simulated"
    config["calendar"]["enabled"] = True
    # load_config stamps the business id only when given a business_id; the
    # harness loads the YAML directly, so set it by hand. calendar_sim needs
    # it to scope its busy query, and refuses to answer without it.
    config.setdefault("business", {})["id"] = 1

    # The address check has its own tests; here it just gets out of the way.
    geocode.check_service_area = lambda addr, cfg, business_id=None: {
        "status": "inside", "miles": 3.0, "formatted": addr, "address": addr,
        "reason": "stubbed for the conversation harness", "locality": "Rochester",
        "state": "NY", "customer_can_fix": False,
    }

    chosen = [s for s in SCENARIOS
              if not args or any(a.lower() in s["name"].lower() for a in args)]
    if not chosen:
        print("no scenarios matched"); return 1

    failed = 0
    for scenario in chosen:
        # Run it `repeat` times and keep the worst result. The extractor is a
        # language model: the same script produced a clean booking on one run
        # and filed the customer's "yes" as the service on the next. A
        # scenario that passes four times out of five is not passing.
        runs = [run(scenario, config) for _ in range(repeat)]
        clean = sum(1 for _, f in runs if not f)
        t, failures = next(((t, f) for t, f in runs if f), runs[0])

        mark = "FAIL" if failures else "pass"
        if repeat > 1:
            mark += f"  [{clean}/{repeat} clean]"
        print(f"\n{'─'*72}\n{mark}  {scenario['name']}")
        if failures or verbose:
            print(f"      {scenario['why']}\n")
            import textwrap
            for who, text in t.exchange:
                label = "cust" if who == "cust" else "BOT "
                body = " ".join(text.split())
                for i, line in enumerate(textwrap.wrap(body, 92) or [""]):
                    print(f"      {label if i == 0 else '    '}  {line}")
            print()
        for name, problem in failures:
            print(f"      ✗ {name}: {problem}")
        failed += bool(failures)

    print(f"\n{'─'*72}")
    print(f"{len(chosen) - failed}/{len(chosen)} scenarios clean")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

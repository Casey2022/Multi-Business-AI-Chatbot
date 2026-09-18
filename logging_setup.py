# logging_setup.py — one place that decides where log lines go.
#
# Why this exists at all: print() writes to stdout and forgets. There's no
# level, no timestamp, no way to turn the chatty lines off in production or
# on while debugging, and on a container the output is gone the moment the
# process restarts. Logging keeps the same one-line-per-event habit but
# makes each line answer three extra questions: when, how bad, and which
# part of the system said it.
#
# Logger names are the old print prefixes, unchanged — a line that read
#     [rag] Opening collection 'bobs_plumbing'...
# still reads
#     2026-09-11 14:02:07 INFO    a3f9 [rag] Opening collection 'bobs_plumbing'...
# so every grep that worked before still works.

import logging
import os
import re
import sys
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from secrets import token_hex

# ---------------------------------------------------------------------------
# Turn id — the thing that makes a log readable after the fact
# ---------------------------------------------------------------------------
#
# One inbound customer message fans out into a dozen log lines from five
# modules, and under gunicorn two customers interleave. Without a shared id
# per turn, reading the log means guessing which [rag] line belongs to which
# [scheduler] line. A ContextVar carries the id down the call stack without
# every function having to accept and forward it.

_turn_id: ContextVar[str] = ContextVar("turn_id", default="----")


def new_turn(turn_id=None):
    """Start a new turn, returning its id. Call once per inbound message."""
    value = turn_id or token_hex(2)
    _turn_id.set(value)
    return value


def current_turn():
    """The id of the turn being handled, or '----' outside one."""
    return _turn_id.get()


class _TurnFilter(logging.Filter):
    """Attach the current turn id to every record, so the format can use it."""

    def filter(self, record):
        record.turn = _turn_id.get()
        return True


# ---------------------------------------------------------------------------
# Keeping customers out of the log stream
# ---------------------------------------------------------------------------
#
# The policy: **structure at INFO, content at DEBUG.**
#
# An INFO line says what happened and what shape it had — "a message of 34
# characters arrived for Bob's Plumbing and was answered by the scheduler".
# A DEBUG line says what the message was. Production runs at INFO, so the
# words a customer typed, the address they gave and the number they texted
# from never reach it.
#
# This isn't caution for its own sake. Logs on a hosted platform go to a
# third party, are retained on their schedule rather than ours, and are
# readable by anyone with dashboard access. The conversation itself is
# already stored in the database, behind a login, scoped to one business —
# which is a much better place for it than a log aggregator.
#
# The masking below is the second layer: a net for lines nobody thought
# about, and for the ones where an identifier is genuinely useful. It keeps
# the last four digits of a phone number, because "the customer ending
# 0123" is how a real support conversation goes, and drops the rest.
#
# Set LOG_PII=true to see everything, which is the right setting on a
# laptop where the only customer is you.

LOG_PII = os.getenv("LOG_PII", "false").lower() == "true"

# Deliberately narrow. A loose "digits and separators" pattern also matches
# a timestamp — the first version turned "2026-09-18 16:00" into
# "…1816:00", corrupting the log it was supposed to be protecting. A
# redaction filter that mangles ordinary data is worse than none, because
# you stop trusting the whole file. So: an explicit + and 10-15 digits, or
# the 3-3-4 grouping a North American number actually has.
_PHONE = re.compile(r"""
    (?<!\w)
    (?:
        \+\d{10,15}                              # +15855550123
      | \(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}        # (585) 555-0123, 585-555-0123
    )
    (?!\w)
""", re.X)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def _mask_phone(match):
    digits = re.sub(r"\D", "", match.group(0))
    if len(digits) < 7:                 # too short to be a phone number
        return match.group(0)
    return f"…{digits[-4:]}"


def _mask_email(match):
    local, _, domain = match.group(0).partition("@")
    return f"{local[0]}…@{domain}" if local else match.group(0)


class _RedactFilter(logging.Filter):
    """Mask identifiers in the formatted message, unless LOG_PII is on.

    Works on the rendered text rather than the arguments, so it catches a
    phone number wherever it turns up — an f-string, a dict repr, an
    exception message — instead of only where someone remembered.
    """

    def filter(self, record):
        if LOG_PII:
            return True
        try:
            text = record.getMessage()
        except Exception:               # pragma: no cover — never break logging
            return True
        masked = _EMAIL.sub(_mask_email, _PHONE.sub(_mask_phone, text))
        if masked != text:
            record.msg = masked
            record.args = ()
        return True


def scrub(value):
    """A log-safe stand-in for something a customer typed.

    Returns the value itself when LOG_PII is on. Otherwise a note of its
    shape — enough to see that something arrived and how big it was,
    without putting the words in the log.
    """
    if LOG_PII:
        return repr(value)
    text = str(value or "")
    return f"<{len(text)} chars>" if text else "<empty>"


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

FORMAT   = "%(asctime)s %(levelname)-7s %(turn)s [%(name)s] %(message)s"
DATEFMT  = "%Y-%m-%d %H:%M:%S"

# Third-party libraries that log at INFO far more than we want to read.
# Deliberately NOT including werkzeug: its INFO lines are the dev server URL,
# the debugger PIN, and per-request access logs — the things you actually
# want while developing. Quiet it in production via LOG_LEVEL if it ever
# gets in the way.
NOISY = ("httpx", "httpcore", "urllib3", "chromadb", "chromadb.telemetry",
         "googleapiclient", "google", "google_auth_httplib2", "openai",
         "posthog")


def setup_logging(level=None, log_dir="logs", log_file="app.log"):
    """Configure root logging for the whole app. Safe to call more than once.

    level defaults to $LOG_LEVEL, else INFO. Set LOG_LEVEL=DEBUG to see the
    chatty per-request lines (raw LLM output, RAG distances) without
    touching any code.

    Console output always happens. File output is best-effort: on a
    read-only or ephemeral container filesystem, failing to open a log file
    must not stop the server from serving.
    """
    root = logging.getLogger()

    # Idempotent — a second call (module re-import, gunicorn preload) should
    # not double every line.
    if getattr(root, "_configured_by_us", False):
        return root

    level = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    root.setLevel(level)

    formatter  = logging.Formatter(FORMAT, datefmt=DATEFMT)
    turnfilter = _TurnFilter()

    # Flask's reloader runs this twice in one process tree; clearing first
    # keeps handlers from stacking.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    redactor = _RedactFilter()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(turnfilter)
    console.addFilter(redactor)
    root.addHandler(console)

    try:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        # Rotating, because an unbounded log file is a disk-full incident
        # waiting for a quiet weekend.
        rotating = RotatingFileHandler(
            directory / log_file, maxBytes=5_000_000, backupCount=3,
            encoding="utf-8",
        )
        rotating.setFormatter(formatter)
        rotating.addFilter(turnfilter)
        rotating.addFilter(redactor)
        root.addHandler(rotating)
        file_note = str(directory / log_file)
    except OSError as e:
        file_note = f"disabled ({e.__class__.__name__}: {e})"

    for name in NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)

    root._configured_by_us = True
    logging.getLogger("logging").info(
        "Logging ready — level=%s file=%s pii=%s", level, file_note,
        "shown (LOG_PII=true)" if LOG_PII else "masked"
    )
    return root

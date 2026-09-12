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

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(turnfilter)
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
        root.addHandler(rotating)
        file_note = str(directory / log_file)
    except OSError as e:
        file_note = f"disabled ({e.__class__.__name__}: {e})"

    for name in NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)

    root._configured_by_us = True
    logging.getLogger("logging").info(
        "Logging ready — level=%s file=%s", level, file_note
    )
    return root

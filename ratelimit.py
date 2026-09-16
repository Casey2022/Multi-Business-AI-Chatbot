# ratelimit.py — fixed-window counters, kept in SQLite.
#
# Why this exists: /webchat calls Claude and Voyage on every message, and it
# is a public endpoint. Without a limit, one person with a loop spends money
# that isn't theirs. A sandbox demo makes that a certainty rather than a
# risk, but the exposure is already real on the deployed site.
#
# Fixed windows rather than a sliding log: a sliding window needs a row per
# request, and this needs to be cheap enough to run before every message on
# a free-tier container. The known weakness is the boundary — someone can
# spend a full window's allowance at 10:59:59 and another at 11:00:00. For
# protecting an API budget that is fine; for anything adversarial it would
# not be.
#
# SQLite rather than memory because gunicorn workers don't share memory and
# the container restarts. A counter that resets whenever the process does is
# not a limit.

import logging
import os
import time

log = logging.getLogger("ratelimit")


def _limit(name, default):
    """Read a limit from the environment, falling back to a sane default."""
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        log.warning("%s is not a number — using %s", name, default)
        return default


# Per browser session. The generous per-minute number keeps a real
# conversation comfortable; the daily number is what stops a loop.
WEBCHAT_PER_MINUTE = _limit("WEBCHAT_PER_MINUTE", 12)
WEBCHAT_PER_DAY    = _limit("WEBCHAT_PER_DAY", 120)

# Per IP, across every session it opens — otherwise a script just mints a
# new session_id for each request and the per-session limit means nothing.
WEBCHAT_IP_PER_MINUTE = _limit("WEBCHAT_IP_PER_MINUTE", 30)
WEBCHAT_IP_PER_DAY    = _limit("WEBCHAT_IP_PER_DAY", 400)

# Login attempts, per IP and per account named.
LOGIN_PER_MINUTE = _limit("LOGIN_PER_MINUTE", 5)
LOGIN_PER_HOUR   = _limit("LOGIN_PER_HOUR", 30)


def hit(bucket, limit, window_seconds):
    """Count one event against a bucket. Returns (allowed, seconds_until_reset).

    Increments first and checks the result, so two workers can't both read
    the same count and both decide they're clear. Attempts made after the
    limit is reached keep incrementing, which makes the counter a record of
    pressure rather than of served requests — the more useful number when
    someone is hammering an endpoint.

    Fails OPEN. If the counter is unavailable the request goes through: a
    broken limiter should not take the site down. The outer protections —
    a daily geocode cap, Anthropic's own limits, and the platform's — are
    still there.
    """
    if limit <= 0:
        return True, 0

    now = int(time.time())
    window_start = now - (now % window_seconds)
    resets_in = window_start + window_seconds - now

    # The window length belongs in the key. Without it, the per-minute and
    # per-day counters for the same caller share a row whenever their
    # boundaries coincide — which they do every midnight UTC, for a minute.
    bucket = f"{bucket}|{window_seconds}"

    try:
        from db import get_connection
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO rate_limits (bucket, window_start, count)
                VALUES (?, ?, 1)
                ON CONFLICT(bucket, window_start) DO UPDATE SET count = count + 1
                """,
                (bucket, window_start),
            )
            row = conn.execute(
                "SELECT count FROM rate_limits WHERE bucket = ? AND window_start = ?",
                (bucket, window_start),
            ).fetchone()
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log.warning("Rate limiter unavailable (%s) — allowing", e)
        return True, 0

    used = row["count"] if row else 1
    if used > limit:
        log.info("Rate limit hit: %s (%d/%d in %ds window)",
                 bucket, used, limit, window_seconds)
        return False, resets_in
    return True, resets_in


def check_all(checks):
    """Apply several limits at once. Returns (allowed, seconds_until_reset).

    Every counter is incremented even after one refuses, so a caller can't
    dodge the daily budget by tripping the per-minute one first.
    """
    allowed = True
    wait = 0
    for bucket, limit, window in checks:
        ok, resets_in = hit(bucket, limit, window)
        if not ok:
            allowed = False
            wait = max(wait, resets_in)
    return allowed, wait


def webchat(slug, session_id, ip):
    """Limits for one inbound web chat message."""
    return check_all([
        (f"webchat:{slug}:{session_id}", WEBCHAT_PER_MINUTE, 60),
        (f"webchat:{slug}:{session_id}", WEBCHAT_PER_DAY, 86400),
        (f"webchat-ip:{ip}",             WEBCHAT_IP_PER_MINUTE, 60),
        (f"webchat-ip:{ip}",             WEBCHAT_IP_PER_DAY, 86400),
    ])


def login_attempt(ip, email):
    """Limits for one admin login attempt."""
    return check_all([
        (f"login-ip:{ip}",       LOGIN_PER_MINUTE, 60),
        (f"login-ip:{ip}",       LOGIN_PER_HOUR, 3600),
        (f"login-user:{email}",  LOGIN_PER_MINUTE, 60),
        (f"login-user:{email}",  LOGIN_PER_HOUR, 3600),
    ])

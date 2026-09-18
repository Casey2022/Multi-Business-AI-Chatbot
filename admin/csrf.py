# admin/csrf.py — proof that a POST came from our own page.
#
# The attack this stops: an owner is signed in to the portal, and visits any
# other page in the same browser. That page contains a form posting to
#
#     https://…/admin/appointment/41/cancel
#
# and submits it with JavaScript. The browser attaches the session cookie
# because that's what browsers do, the request arrives fully authenticated,
# and the appointment is cancelled — and deleted from the owner's Google
# Calendar. The owner never sees anything. Every login_required and
# require_business_access check passes, because the request genuinely is
# from a logged-in user with access; it just isn't from a page we served.
#
# SameSite=Lax on the session cookie (set in app.py) blocks most of this in
# a current browser, and is not enough on its own: it's a browser
# behaviour, not our rule, and it doesn't cover an older browser or a
# request from a sibling subdomain. A token is the part we control.
#
# Written by hand rather than adding Flask-WTF, because requirements.txt is
# deliberately direct dependencies only and this is forty lines. The shape
# is the standard one: a random value in the session, the same value in a
# hidden field, compared with a constant-time comparison.

import logging
from secrets import compare_digest, token_urlsafe

from flask import abort, request, session

from admin import admin_bp

log_sec = logging.getLogger("security")

FIELD = "_csrf_token"
SESSION_KEY = "csrf_token"

# Endpoints that legitimately receive a POST from outside our own pages.
# Listed by endpoint name rather than path so a renamed route can't silently
# fall out of the list — and kept short, because everything on it is a hole
# by definition. Both are protected by something else: Twilio signs its
# webhooks, and the demo mint is rate-limited and creates only a throwaway.
EXEMPT = {
    "sms_reply",      # Twilio posts this; verified by request signature
    "webchat_reply",  # JSON from the chat widget; rate-limited per session and IP
}

# demo_start is deliberately NOT exempt. It's public, but it signs the
# caller in as the new demo's owner — so a forced POST to it would log a
# real owner out of their own account and into a sandbox. That's the same
# session-swap confusion we just fixed, except triggered by someone else.
# The picker mints a token into the visitor's session when it renders, so
# an anonymous visitor can still start a demo.


def token():
    """The token for this session, minted on first use."""
    if SESSION_KEY not in session:
        session[SESSION_KEY] = token_urlsafe(32)
    return session[SESSION_KEY]


@admin_bp.app_context_processor
def csrf_for_templates():
    """Make csrf_token() callable from any template."""
    return {"csrf_token": token}


@admin_bp.before_app_request
def check():
    """Reject a state-changing request that didn't come from one of our forms.

    before_app_request, not before_request: the blueprint's own hook would
    only cover /admin, and the app has POST routes outside it. A check that
    protects most of the app is the kind of gap nobody notices until it
    matters.
    """
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    if request.endpoint in EXEMPT:
        return

    sent = request.form.get(FIELD) or request.headers.get("X-CSRF-Token", "")
    expected = session.get(SESSION_KEY, "")

    # compare_digest rather than ==, so the comparison doesn't leak how much
    # of the token was right through how long it took to say no.
    if not expected or not sent or not compare_digest(sent, expected):
        log_sec.warning(
            "Rejected a %s to %s with %s CSRF token",
            request.method, request.path,
            "no" if not sent else "a bad")
        abort(400, "This form has expired. Reload the page and try again.")

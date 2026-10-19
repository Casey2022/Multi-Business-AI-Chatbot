"""Owners asking the operator for help: the Help page's back end.

Added 2026-10-12. The receptionist hands a customer to the owner when it's
out of its depth; this is the same hand-off one level up, owner to operator.

What makes a request useful is the context the owner wouldn't think to
give. "The menu thing is broken" can't be acted on; "Crosstown, on
/admin/business/3/menu, error a3f9c2d1, version 5d2834d" can. So the app
attaches that itself (context_for), and the Help page shows the owner
exactly what it attached. Customer data is never attached: no
conversations, no appointments.

The operator hears about it two ways: the Support inbox in the portal
(always), and an email (when SMTP is configured; see email_settings). The
email is sent in the background and can only fail quietly: it is recorded
on the request, and the request is filed either way.
"""

import logging
import os
import re
import secrets
import smtplib
import subprocess
import threading
from email.message import EmailMessage

log = logging.getLogger("support")

MAX_MESSAGE = 2000
MAX_CONTACT = 200

_PAGE = re.compile(r"^/[A-Za-z0-9/_.\-]{0,199}$")
_ERROR_ID = re.compile(r"^[0-9a-f]{8}$")
_EMAIL = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[A-Za-z]{2,}$")

_version = None


def app_version():
    """The commit this server is running: Render sets RENDER_GIT_COMMIT;
    locally, ask git once. "unknown" rather than an error."""
    global _version
    if _version is None:
        commit = os.environ.get("RENDER_GIT_COMMIT", "")[:7]
        if not commit:
            try:
                commit = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                    text=True, timeout=3,
                    cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip()
            except Exception:
                commit = ""
        _version = commit or "unknown"
    return _version


def new_error_id():
    """A short reference for one server error: shown on the error page,
    written in the log beside the traceback, attached to a Help request."""
    return secrets.token_hex(4)


def clean_page(raw):
    """The page the owner came from, as a bare path, or None.

    It arrives in the query string, so it's untrusted: only a path on this
    site (no scheme, no host, no "//", no query) is kept. That also makes the
    "Back to where you were" link safe to render."""
    raw = (raw or "").strip().split("?")[0].split("#")[0]
    if not _PAGE.match(raw) or raw.startswith("//") or ".." in raw:
        return None
    return raw


def clean_error_id(raw):
    raw = (raw or "").strip().lower()
    return raw if _ERROR_ID.match(raw) else None


def looks_like_email(text):
    return bool(text) and len(text) <= MAX_CONTACT and bool(_EMAIL.match(text))


def check_form(message, contact, needs_contact_field):
    """(message, contact, errors) from the Help form, cleaned."""
    message = (message or "").replace("\r\n", "\n").strip()
    contact = (contact or "").strip() if needs_contact_field else ""
    errors = []
    if not message:
        errors.append("Tell us what's going on. A sentence or two is plenty.")
    elif len(message) > MAX_MESSAGE:
        errors.append(f"That's a bit long. Keep it under {MAX_MESSAGE} characters "
                      f"(it's {len(message)}).")
    if contact and not looks_like_email(contact):
        errors.append("That email address doesn't look right. Leave it blank if "
                      "you don't need a reply.")
    return message, contact or None, errors


def context_for(business, page, error_id):
    """What the app attaches to a request. Shown to the owner before they send."""
    return {
        "business": (business or {}).get("name"),
        "business_id": (business or {}).get("id"),
        "page": page,
        "error_id": error_id,
        "version": app_version(),
    }


def reply_address(row):
    """Where a reply should go: the owner's login email, or the address a
    demo visitor left. A demo's login email is made up, so it's never used."""
    if row.get("is_demo"):
        return row.get("contact") if looks_like_email(row.get("contact") or "") else None
    email = row.get("user_email") or ""
    return email if looks_like_email(email) else None


def file_request(user, business, message, contact, page, error_id, inbox_url=None,
                 background=True):
    """Save a request and tell the operator. Returns the new id."""
    from db import add_support_request
    is_demo = bool((business or {}).get("is_demo"))
    request_id = add_support_request(
        business_id=(business or {}).get("id"),
        business_name=(business or {}).get("name"),
        user_email=(user or {}).get("email"),
        contact=contact if is_demo else None,
        is_demo=is_demo,
        message=message,
        context=context_for(business, page, error_id))
    # No message text and no email address in the log: the inbox has both.
    log.info("Support request #%s filed (business %s%s)", request_id,
             (business or {}).get("id"), ", demo" if is_demo else "")
    if background:
        threading.Thread(target=alert_operator, args=(request_id, inbox_url),
                         daemon=True).start()
    else:
        alert_operator(request_id, inbox_url)
    return request_id


# --- the email alert ----------------------------------------------------------

SMTP = smtplib.SMTP          # replaced in tests


def email_settings():
    """SMTP settings from the environment, or None when alerts aren't set up.

    SUPPORT_ALERT_EMAIL  who gets told (the operator)
    SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM (= SMTP_USER)
    With Gmail: smtp.gmail.com, and an app password, not the account password.
    """
    to = os.environ.get("SUPPORT_ALERT_EMAIL", "").strip()
    host = os.environ.get("SMTP_HOST", "").strip()
    if not (to and host):
        return None
    user = os.environ.get("SMTP_USER", "").strip()
    return {"to": to, "host": host,
            "port": int(os.environ.get("SMTP_PORT", "587") or 587),
            "user": user, "password": os.environ.get("SMTP_PASSWORD", ""),
            "from": os.environ.get("SMTP_FROM", "").strip() or user or to}


def alert_email(row, settings, inbox_url=None):
    ctx = row.get("context") or {}
    who = ("a demo visitor" if row.get("is_demo") else row.get("user_email")) or "someone"
    msg = EmailMessage()
    # A demo visitor's message is feedback from the demo round (2026-10-19):
    # its own subject, so it can be told apart from an owner's request.
    msg["Subject"] = (f"Demo feedback #{row['id']} ({row.get('business_name') or 'demo'})"
                      if row.get("is_demo") else
                      f"Help request #{row['id']} from {row.get('business_name') or who}")
    msg["From"] = settings["from"]
    msg["To"] = settings["to"]
    reply_to = reply_address(row)
    if reply_to:
        msg["Reply-To"] = reply_to
    lines = [f"{who} at {row.get('business_name') or 'no business'} asked for help:", "",
             row["message"], "",
             f"Page:      {ctx.get('page') or '—'}",
             f"Error ref: {ctx.get('error_id') or '—'}",
             f"Version:   {ctx.get('version') or '—'}",
             f"Reply to:  {reply_to or 'no address (demo visitor left none)'}"]
    if inbox_url:
        lines += ["", f"Inbox: {inbox_url}"]
    msg.set_content("\n".join(lines))
    return msg


def alert_operator(request_id, inbox_url=None):
    """Email the operator about one request. Never raises; records what happened."""
    from db import get_support_request, set_support_alert
    try:
        settings = email_settings()
        if not settings:
            set_support_alert(request_id, "email not set up")
            return "email not set up"
        row = get_support_request(request_id)
        msg = alert_email(row, settings, inbox_url)
        with SMTP(settings["host"], settings["port"], timeout=15) as server:
            server.starttls()
            if settings["user"]:
                server.login(settings["user"], settings["password"])
            server.send_message(msg)
        set_support_alert(request_id, "emailed")
        log.info("Support request #%s: operator emailed", request_id)
        return "emailed"
    except Exception as e:
        status = f"email failed: {type(e).__name__}"
        log.warning("Support request #%s: %s", request_id, status)
        try:
            set_support_alert(request_id, status)
        except Exception:
            pass
        return status

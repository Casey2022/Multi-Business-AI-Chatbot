"""Tell a customer their appointment moved or was cancelled.

One step, called from every place a booking changes after it was made: the
owner's "Move" and "Cancel" buttons in the portal, and reconcile.py when the
owner moved or deleted the event in Google Calendar. Called from the change,
not from a button, so no path can forget it.

Delivery depends on how the customer reached us:
- web chat (recipient "web_..."): the notice waits in the `notifications`
  outbox until their chat window collects it (it checks every few seconds;
  see /webchat/<slug>/updates in app.py);
- a phone number: SMS, once Twilio is set up. Until then it's recorded as
  not sent, with that reason, and this is where the SMS sender plugs in.
  Texting also needs the customer's consent, collected at booking.
- anything else (e.g. eval ids): not sent, "no way to reach this customer".

Everything is recorded, sent or not, and the appointment page shows it.
Added 2026-09-28.
"""

import logging
import re
from datetime import datetime

import clock
from db import add_notification

log = logging.getLogger("notify")

WHEN_FORMAT = "%A, %B %-d at %-I:%M %p"
_PHONE = re.compile(r"^\+?\d[\d\s().-]{6,}$")


def channel_for(recipient):
    """(channel, reason-if-unreachable) for an appointment's contact."""
    recipient = recipient or ""
    if recipient.startswith("web_"):
        return "webchat", None
    if _PHONE.match(recipient):
        return "sms", "SMS isn't set up yet"
    return "none", "no way to reach this customer"


def _pretty(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%d %H:%M").strftime(WHEN_FORMAT)
    except (TypeError, ValueError):
        return iso


def message_for(appt, config, kind, new_datetime=None):
    """The words the customer reads. Times are the business's local time,
    which is how appointment times are stored."""
    business = config.get("business", {})
    name = appt.get("customer_name")
    noun = config.get("booking", {}).get("noun", "appointment")
    phone = business.get("phone")
    greeting = f"Hi {name}, it's {business.get('name', 'us')}." if name else \
        f"Hi, it's {business.get('name', 'us')}."
    what = f"your {appt.get('service')} {noun}".replace("  ", " ")
    reach = (f"reply here or call us at {phone}" if phone else "reply here")
    if kind == "moved":
        return (f"{greeting} We've moved {what} from "
                f"{_pretty(appt.get('datetime'))} to {_pretty(new_datetime)}. "
                f"If that time doesn't work, {reach}.")
    return (f"{greeting} {what[0].upper() + what[1:]} on "
            f"{_pretty(appt.get('datetime'))} has been cancelled. To rebook "
            f"or ask about it, {reach}.")


def notify_change(appt, config, kind, new_datetime=None):
    """Record (and where possible deliver) a notice. Returns (status, reason).

    `appt` is the appointment as it was BEFORE the change, so a move can say
    where it moved from. kind is "moved" or "cancelled".

    Never raises: a notification problem must not undo or block the change
    the owner just made. It says so in the log and in the returned status.
    """
    try:
        now = clock.business_now(config)
        created = now.isoformat(timespec="seconds")
        recipient = appt.get("phone") or ""
        channel, reason = channel_for(recipient)

        # Nothing to tell someone about a booking that's already behind them.
        affected = new_datetime if kind == "moved" else appt.get("datetime")
        try:
            passed = (datetime.strptime(appt.get("datetime"), "%Y-%m-%d %H:%M") < now
                      and (kind != "moved" or
                           datetime.strptime(affected, "%Y-%m-%d %H:%M") < now))
        except (TypeError, ValueError):
            passed = False
        if passed:
            channel, reason = channel, "the appointment had already passed"

        body = message_for(appt, config, kind, new_datetime)
        status = "pending" if (channel == "webchat" and not reason) else "not_sent"
        add_notification(appt["business_id"], appt["id"], recipient, channel,
                         kind, body, status, created, reason=reason)
        # No names or message text in the log: the notice quotes both.
        log.info("Appointment #%s %s: notice %s via %s%s", appt["id"], kind,
                 status, channel, f" ({reason})" if reason else "")
        return status, reason
    except Exception:
        log.exception("Couldn't record a notice for appointment #%s",
                      appt.get("id"))
        return "not_sent", "an error on our side"


def status_line(status, reason):
    """A short phrase for the owner's flash message."""
    if status == "pending":
        return "The customer will see a notice in their chat."
    return f"The customer wasn't notified: {reason}."

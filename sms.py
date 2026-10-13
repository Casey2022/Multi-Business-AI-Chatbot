"""sms.py — what a reply becomes when it goes out as a text message.

Added 2026-10-12 with the text-message simulator on the demo page. Used by
the real Twilio adapter (/sms) and the simulator alike, so the simulator
shows exactly what a customer's phone would get.

Three things a text has that a chat bubble doesn't:

1. An alphabet. Texts are sent in GSM-7 (160 characters a segment) unless
   one character isn't in it, and then the WHOLE message goes as Unicode
   (70 a segment). The bot writes curly quotes, em dashes and "…", none of
   which are GSM-7, so a 150-character reply quietly became 3 segments
   instead of 1. `for_sms` swaps those for their plain equivalents. Emoji
   are left alone: the business's own prompt allows "a sparing emoji", and
   that's a choice with a cost, which the simulator shows.
2. A price per segment, both ways (see COST_*).
3. A phone number as the customer's identity. The simulator invents one
   per visit in 555-xxxx, a range used for fiction, and `is_simulated`
   makes sure nothing ever tries to text it for real.
"""

import math
import re

# GSM 03.38: the basic table, and the extension table (each counts as 2).
_GSM_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
_GSM_EXTENDED = set("^{}\\[~]|€\f")

# What the bot writes that isn't GSM-7, and what a text says instead.
_PLAIN = {
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "—": "-", "–": "-", "‒": "-", "―": "-", "‐": "-", "‑": "-",
    "…": "...", "•": "-", "·": "-", "→": "->",
    " ": " ", " ": " ", " ": " ", "​": "",
}
_PLAIN_RE = re.compile("|".join(map(re.escape, _PLAIN)))

# Cents per segment, from Twilio's US pricing (checked 2026-10-12): $0.0083
# a segment plus a carrier fee of up to $0.005 out / about $0.0035 in. An
# estimate for the demo, not a bill.
COST_OUT_CENTS = 1.3
COST_IN_CENTS = 1.2

SIM_PREFIX = "+1555"


def for_sms(text):
    """The reply as it should be sent: plain punctuation, trimmed."""
    return _PLAIN_RE.sub(lambda m: _PLAIN[m.group(0)], text or "").strip()


def segments(text):
    """{"encoding", "segments", "units", "because"} for one outgoing text.

    units is GSM-7 septets (extension characters count 2) or UTF-16 code
    units (an emoji counts 2); because is the first character that forced
    Unicode, or None."""
    text = text or ""
    because = next((c for c in text if c not in _GSM_BASIC and c not in _GSM_EXTENDED), None)
    if because is None:
        units = len(text) + sum(1 for c in text if c in _GSM_EXTENDED)
        count = 1 if units <= 160 else math.ceil(units / 153)
        encoding = "GSM-7"
    else:
        units = len(text.encode("utf-16-le")) // 2
        count = 1 if units <= 70 else math.ceil(units / 67)
        encoding = "Unicode"
    return {"encoding": encoding, "segments": max(count, 1) if text else 0,
            "units": units, "because": because}


def is_simulated(number):
    """True for the simulator's invented numbers: never texted for real."""
    return bool(re.fullmatch(r"\+1555\d{7}", number or ""))

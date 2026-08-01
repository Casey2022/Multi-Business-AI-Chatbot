# phone_utils.py — normalize phone numbers to a canonical format.
# Run every incoming phone through normalize() before using it as a key.

import phonenumbers


def normalize(raw_phone, default_region="US"):
    """Convert any phone number string to canonical E.164 format (+15551234567).

    This is a *format conversion*, not validation. Even fictional numbers
    (e.g., 555 area codes used in testing) get normalized to E.164 if they
    parse as phone-number-shaped strings. Validation is a separate concern;
    handle it elsewhere if you need it.

    Examples:
        "+15551234567"       -> "+15551234567"
        "15551234567"        -> "+15551234567"
        "(555) 123-4567"     -> "+15551234567"
        "555.123.4567"       -> "+15551234567"
        "unknown"            -> "unknown"  (passes through unchanged)
        ""                   -> ""         (passes through unchanged)
        "not a phone"        -> "not a phone" (parse error, passes through)
    """
    if not raw_phone or raw_phone == "unknown":
        return raw_phone

    try:
        parsed = phonenumbers.parse(raw_phone, default_region)
        # Format to E.164 regardless of whether is_valid_number agrees.
        # Validation is a separate concern from format normalization.
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        # Truly unparseable input — return as-is rather than throw away data.
        return raw_phone
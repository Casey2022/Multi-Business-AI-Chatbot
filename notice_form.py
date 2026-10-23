"""notice_form.py — the owner's form for booking notice.

Added 2026-10-22 (notes/booking_notice.md, "an owner page"). The notice
each service needs has lived in booking.notice in the YAML since
2026-10-20, the only copy: the booking flow refuses a time inside it, the
knowledge base shows a generated line from it, and check_notice takes its
numbers from it. Owners could only read those lines. Now they edit them
here, the way policy_form.py edits cancellation rules:

- A change is a DRAFT: it's in the "Unpublished changes" banner, Preview
  answers uses it, Discard undoes it, and customers (and the booking flow)
  get it only after Publish. Stored as config overrides: "notice_draft"
  (pending) and "notice" (published). load_config applies only the
  published one. Publish rebuilds the index with it, because the
  generated lines are part of each section's chunk.
- Owners edit how much notice each service needs, an order-by cutoff, the
  section that shows the line, and how much each variant needs. Which
  words name a variant ("gluten free") and which words name the service
  in a question stay with the operator: they decide what the code matches,
  and they're copied through untouched.
"""

import copy
import re

DRAFT_FIELD = "notice_draft"
LIVE_FIELD = "notice"
MAX_HOURS = 24 * 365
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def table(config):
    return dict(((config or {}).get("booking") or {}).get("notice") or {})


def draft(business_id):
    """The pending table (a dict, maybe empty), or None (no draft)."""
    from db import get_config_overrides
    value = get_config_overrides(business_id).get(DRAFT_FIELD)
    return value if isinstance(value, dict) else None


def with_draft(config, business_id):
    """A copy of config with the draft table in place (Preview answers,
    Publish). The same config if no draft."""
    pending = draft(business_id) if business_id is not None else None
    if pending is None:
        return config
    out = copy.deepcopy(config)
    out.setdefault("booking", {})["notice"] = copy.deepcopy(pending)
    import services
    import section_links
    # A draft saved under an older service name or section title.
    return section_links.follow(services.apply(out), business_id)


def apply_live(config, value):
    """load_config's half: the published table in place of the YAML's."""
    if isinstance(value, dict):
        config.setdefault("booking", {})["notice"] = value


def save_draft(business_id, new_table, updated_by=None):
    from db import set_config_override, mark_documents_dirty
    set_config_override(business_id, DRAFT_FIELD, new_table, updated_by=updated_by)
    mark_documents_dirty(business_id)


def publish_draft(business_id, updated_by=None):
    """Make the draft the live table. True if there was a draft."""
    from db import set_config_override, clear_config_override
    pending = draft(business_id)
    if pending is None:
        return False
    set_config_override(business_id, LIVE_FIELD, pending, updated_by=updated_by)
    clear_config_override(business_id, DRAFT_FIELD)
    return True


def discard_draft(business_id):
    from db import clear_config_override
    clear_config_override(business_id, DRAFT_FIELD)


def describe(config):
    """What customers are told, a line per service ([] when none)."""
    import policy
    lines = []
    for service, entry in table(config).items():
        if not isinstance(entry, dict):
            continue
        line = f"{_cap(service)}: at least {policy.span(float(entry.get('hours') or 0))}"
        if entry.get("order_by"):
            days = entry.get("order_by_days") or []
            line += (f", ordered by {_clock(entry['order_by'])} on "
                     + ", ".join(d.capitalize() + "s" for d in days) if days else
                     f", ordered by {_clock(entry['order_by'])} every day")
        for v in entry.get("variants") or []:
            if isinstance(v, dict):
                line += (f"; {policy._variant_name(v)}: at least "
                         f"{policy.span(float(v.get('hours') or 0))}")
        lines.append(line + ".")
    return lines


def waiting_lines(live_config, business_id):
    """The banner's line for a pending notice change ([] when none)."""
    if draft(business_id) is None:
        return []
    return ([] if describe(live_config) == describe(with_draft(live_config, business_id))
            else ["booking notice: changed"])


# ---------------------------------------------------------------------------
# Table ⇄ form
# ---------------------------------------------------------------------------

def _cap(text):
    return text[:1].upper() + text[1:]


def _clock(hhmm):
    import policy
    from datetime import datetime
    return policy.clock_time(datetime.strptime(hhmm, "%H:%M"))


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "service"


def _amount(hours):
    """(amount, unit) for the form: whole days as days, anything else hours."""
    if not hours:
        return "", "hours"
    hours = float(hours)
    if hours >= 24 and hours % 24 == 0:
        return f"{hours / 24:g}", "days"
    return f"{hours:g}", "hours"


def _entry_for(service, notice):
    """The table's key and entry for one of the business's services."""
    from scheduling import same_service
    for key, entry in notice.items():
        if isinstance(entry, dict) and same_service(key, service):
            return key, entry
    return None, None


def rows(config):
    """One row per service: what the form shows. Services the business
    offers first, then any table entry naming none of them (kept, so it
    isn't lost, and the owner can clear it)."""
    notice = table(config)
    seen, out = set(), []
    names = [s for s in (config.get("services") or []) if isinstance(s, str)]
    for name in names + [k for k in notice if k not in names]:
        key, entry = _entry_for(name, notice)
        if key in seen and key is not None:
            continue
        if key is not None:
            seen.add(key)
        entry = entry or {}
        amount, unit = _amount(entry.get("hours"))
        variants = []
        for v in entry.get("variants") or []:
            if isinstance(v, dict):
                v_amount, v_unit = _amount(v.get("hours"))
                import policy
                variants.append({"name": policy._variant_name(v),
                                 "words": ", ".join(v.get("words") or []),
                                 "amount": v_amount, "unit": v_unit})
        out.append({"name": key or name, "slug": slug(key or name), "amount": amount,
                    "unit": unit, "order_by": entry.get("order_by") or "",
                    "order_by_days": list(entry.get("order_by_days") or []),
                    "section": entry.get("section") or "", "variants": variants})
    return out


def _hours(form, prefix, label, errors):
    """Hours from an amount and a unit, None when blank."""
    raw = (form.get(f"{prefix}_amount") or "").strip()
    if not raw:
        return None
    try:
        amount = float(raw)
    except ValueError:
        errors.append(f"{label}: \"{raw}\" isn't a number")
        return None
    hours = amount * (24 if form.get(f"{prefix}_unit") == "days" else 1)
    if hours < 0 or hours > MAX_HOURS:
        errors.append(f"{label}: give between 0 and 365 days")
        return None
    return hours


def parse(form, config):
    """(new table, errors) from the posted form. Blank or 0 hours: the
    service needs no notice. What the form doesn't show (the words that
    name a service or a variant) is carried over from the current table."""
    current = table(config)
    errors, new = [], {}
    for row in rows(config):
        p = slug(row["name"])
        label = _cap(row["name"])
        _key, old = _entry_for(row["name"], current)
        old = old or {}
        hours = _hours(form, p, label, errors)
        order_by = (form.get(f"{p}_order_by") or "").strip()
        if order_by and not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", order_by):
            errors.append(f"{label}: \"{order_by}\" isn't a time")
            order_by = ""
        if not hours and not order_by:
            continue                                   # no notice for this one
        entry = {k: v for k, v in old.items()
                 if k not in ("hours", "order_by", "order_by_days", "section", "variants")}
        entry["hours"] = hours or 0
        if order_by:
            entry["order_by"] = order_by.zfill(5)
            days = [d for d in DAYS if form.get(f"{p}_day_{d}")]
            if days and len(days) < 7:
                entry["order_by_days"] = days
        section = (form.get(f"{p}_section") or "").strip()
        if section:
            entry["section"] = section
        variants = []
        for i, v in enumerate(old.get("variants") or []):
            if not isinstance(v, dict):
                continue
            import policy
            v_label = _cap(policy._variant_name(v))
            v_hours = _hours(form, f"{p}_v{i}", v_label, errors)
            if v_hours is None:
                continue                               # cleared: dropped
            # Shorter than the service's own: it would never apply (the
            # longest wins), so it's a mistake. Equal is harmless.
            if v_hours < entry["hours"]:
                errors.append(f"{v_label}: that's shorter than {row['name']}'s own "
                              f"notice, so it would never apply. Clear it instead")
                continue
            variants.append(dict(v, hours=v_hours))
        if variants:
            entry["variants"] = variants
        new[row["name"]] = entry
    return new, errors


# Notice stated in a section's own words: a second copy of a number the
# table holds, which could disagree (one_source_test.py).
_STATES_NOTICE = re.compile(
    r"\b\d+\s*(?:hours?|days?|weeks?)['’]?\s+(?:of\s+)?(?:advance\s+)?notice\b"
    r"|\bnotice of\s+\d+\s*(?:hours?|days?|weeks?)\b", re.I)


def sections_stating_notice(sections):
    """Titles of knowledge-base sections that state a notice period themselves."""
    return [s["title"].split(" (")[0] for s in sections
            if _STATES_NOTICE.search(s["body"] or "")]

"""policy_form.py — the owner's form for their cancellation rules.

Added 2026-10-17 (owner-edit safeguard 4: forms, not prose, for rule-like
policies). The rules have lived in policies.cancellation in each YAML
since 2026-10-03, and policy.py does every sum from them; owners could
only read them. Now they edit them here, under three decisions (Casey,
2026-10-17):

- A change is a DRAFT, like a menu or section change: it's in the
  "Unpublished changes" banner, Preview answers uses it, Discard undoes
  it, and customers hear it only after Publish. Stored as config
  overrides: "cancellation_draft" (pending) and "cancellation" (published,
  when it differs from the YAML). load_config applies only the published
  one.
- Owners edit the windows (how much notice) and what each one costs, the
  deposit amounts and the notes. The kinds of order the rules tell apart
  (Sunrise: custom and wedding cakes) and the wording words ("appointment",
  "cancelled or moved") stay with the operator: they're copied through
  untouched.
- A business with no rules can create simple ones (no charge, a % charge,
  or the full price). Its knowledge base may already describe cancelling
  in prose, a second copy that could disagree (one_source_test.py), so
  the form names those sections.

The last window is always "with less notice than that" (at_least_hours
0): the owner can't leave a gap between windows that a late cancellation
would fall through.
"""

import copy
import re

DRAFT_FIELD = "cancellation_draft"
LIVE_FIELD = "cancellation"
REMOVED = {"removed": True}
MAX_WINDOWS = 5
MAX_SERVICES = 6
MAX_NOTES = 5
MAX_NOTE = 300
MAX_HOURS = 24 * 365

# What can happen in a window. Deposit outcomes only make sense where the
# rules take deposits.
OUTCOMES = {
    "no_charge": "No charge",
    "charge_percent": "Charge a percentage of the price",
    "charge_full_price": "Charge the full price",
    "refund_deposit": "Refund the deposit in full",
    "keep_percent": "Keep a percentage of the price, refund the rest",
    "forfeit_deposit": "Keep the whole deposit",
}
WITH_PERCENT = {"charge_percent", "keep_percent"}
DEPOSIT_ONLY = {"refund_deposit", "keep_percent", "forfeit_deposit"}

_CANCELLING = re.compile(r"\b(cancel\w*|refund\w*|forfeit\w*|no[- ]shows?)\b", re.I)


def outcomes_for(policy):
    """The outcome choices this business's rules allow, in display order."""
    deposits = bool((policy or {}).get("deposits"))
    return [k for k in OUTCOMES if deposits or k not in DEPOSIT_ONLY]


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------

def draft(business_id):
    """The pending rules: a policy dict, REMOVED, or None (no draft)."""
    from db import get_config_overrides
    value = get_config_overrides(business_id).get(DRAFT_FIELD)
    return value if isinstance(value, dict) else None


def with_draft(config, business_id):
    """A copy of config with the draft rules in place (Preview answers,
    Publish, the owner assistant's checks). The same config if no draft."""
    pending = draft(business_id) if business_id is not None else None
    if pending is None:
        return config
    out = copy.deepcopy(config)
    policies = out.setdefault("policies", {})
    if pending.get("removed"):
        policies.pop("cancellation", None)
    else:
        policies["cancellation"] = pending
    return out


def apply_live(config, value):
    """load_config's half: the published override onto the YAML's rules."""
    if not isinstance(value, dict):
        return
    if value.get("removed"):
        (config.get("policies") or {}).pop("cancellation", None)
    else:
        config.setdefault("policies", {})["cancellation"] = value


def save_draft(business_id, policy, updated_by=None):
    from db import set_config_override, mark_documents_dirty
    set_config_override(business_id, DRAFT_FIELD, policy if policy is not None else REMOVED,
                        updated_by=updated_by)
    mark_documents_dirty(business_id)


def publish_draft(business_id, updated_by=None):
    """Make the draft rules the live ones. True if there was a draft."""
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


def waiting_lines(live_config, business_id):
    """The banner's line for a pending rules change ([] when none)."""
    import policy
    pending = draft(business_id)
    if pending is None:
        return []
    before = policy.cancellation(live_config)
    if pending.get("removed"):
        return ["cancellation rules: removed"] if before else []
    if not before:
        return ["cancellation rules: new"]
    after = with_draft(live_config, business_id)
    return ([] if policy.policy_text(live_config) == policy.policy_text(after)
            else ["cancellation rules: changed"])


# ---------------------------------------------------------------------------
# Rules ⇄ form
# ---------------------------------------------------------------------------

def new_policy(config):
    """Starting rules for a business that has none, in its own words for a
    booking ("appointment", "visit")."""
    noun = ((config or {}).get("booking") or {}).get("noun") or "appointment"
    return {"title": f"Cancelling {_article(noun)} {noun}", "what": noun,
            "action": "cancelled", "acting": "cancelling",
            "before": f"the {noun}", "price": f"the price of the {noun}",
            "windows": [{"at_least_hours": 24, "any": "no_charge"},
                        {"at_least_hours": 0, "any": {"charge_percent": 50}}],
            "notes": []}


def _article(word):
    return "an" if word[:1].lower() in "aeiou" else "a"


def kinds(policy):
    """The kinds the rules tell apart: deposit kinds (the operator's), or
    the services the owner gave their own rules plus everything else."""
    import policy as policy_mod
    return policy_mod._kinds(policy)


def slug(kind):
    """A kind as a form field name part: "cut and colour" → "cut_and_colour".
    By name, not position, because ticking a service adds a column between
    showing the form and saving it."""
    return re.sub(r"[^a-z0-9]+", "_", str(kind).lower()).strip("_") or "x"


def service_names(config):
    """The business's services as they're named (Settings), for the
    "different rules for" choices: "Haircut (45 min)" → "haircut"."""
    names = []
    for s in (config or {}).get("services") or []:
        name = " ".join(re.sub(r"\s*\(.*?\)", "", str(s)).split()).lower()
        if name and name not in names:
            names.append(name)
    return names


def can_pick_services(policy):
    """Services can have their own rules unless the kinds are deposit
    kinds (Sunrise's cakes), which are the operator's."""
    return not (policy or {}).get("deposits")


def _spec_row(spec):
    """An outcome as (type, percent text)."""
    if isinstance(spec, dict):
        if "charge_percent" in spec:
            return "charge_percent", f"{spec['charge_percent']:g}"
        if "keep_percent" in spec:
            return "keep_percent", f"{spec['keep_percent']:g}"
    return str(spec), ""


def _hours_row(hours):
    """(amount, unit) as an owner would say it: 168 → (7, days), but 24
    and 48 stay hours ("24 hours' notice", not "1 days")."""
    hours = int(hours)
    if hours >= 72 and hours % 24 == 0:
        return str(hours // 24), "days"
    return str(hours), "hours"


def rows(policy):
    """The form's rows for a policy: {"kinds", "deposits", "windows",
    "last", "notes"}. Windows are the ones with some notice, longest
    first; "last" is the "less notice than that" window."""
    import policy as policy_mod
    ks = kinds(policy)
    windows = policy_mod._windows(policy)
    explicit = [w for w in windows if w["at_least_hours"] > 0]
    last = next((w for w in windows if w["at_least_hours"] == 0), None)

    def outcomes(window):
        return [dict(zip(("type", "percent"), _spec_row(
                    window.get(k, window.get("any")) if window else "no_charge")),
                     kind=k, slug=slug(k), label=policy_mod.kind_label(policy, k))
                for k in ks]
    deposits = []
    for k in ks:
        d = ((policy.get("deposits") or {}).get(k)) or {}
        if "percent" in d or "min_percent" in d:
            deposits.append({"kind": k, "field": "percent" if "percent" in d else "min_percent",
                             "value": f"{d.get('percent', d.get('min_percent')):g}",
                             "when": d.get("when", "")})
    return {
        "kinds": ks,
        "services": list(policy.get("services") or []),
        "deposits": deposits,
        "windows": [{"amount": a, "unit": u, "outcomes": outcomes(w)}
                    for w in explicit for a, u in [_hours_row(w["at_least_hours"])]],
        "last": {"outcomes": outcomes(last)},
        "notes": "\n".join(policy.get("notes") or []),
    }


def _int(text):
    text = (text or "").strip()
    return int(text) if re.fullmatch(r"\d{1,6}", text) else None


def _percent(text):
    text = (text or "").strip().rstrip("%").strip()
    if not re.fullmatch(r"\d{1,3}(?:\.\d{1,2})?", text):
        return None
    value = float(text)
    return (int(value) if value.is_integer() else value) if 0 < value <= 100 else None


def _outcome(form, prefix, policy, label, errors):
    """One outcome from the form, or None with an error added."""
    kind_type = form.get(f"{prefix}_type", "")
    if kind_type not in outcomes_for(policy):
        errors.append(f"{label}: choose what happens")
        return None
    if kind_type in WITH_PERCENT:
        pct = _percent(form.get(f"{prefix}_pct"))
        if pct is None:
            errors.append(f"{label}: the percentage needs to be between 1 and 100")
            return None
        return {kind_type: pct}
    return kind_type


def parse(form, base, available_services=()):
    """(new policy, errors) from the posted form. `base` is the rules as
    they are (the draft, the live ones, or new_policy): its words, and any
    deposit kinds, are copied through, since owners don't edit those. Where
    there are no deposits, the services ticked (from available_services,
    the business's own list) get their own outcomes; a service ticked just
    now starts with the same outcomes as everything else."""
    errors = []
    policy = {k: copy.deepcopy(v) for k, v in base.items()
              if k not in ("windows", "notes", "deposits", "services")}
    if can_pick_services(base) and available_services:
        chosen = [s for s in available_services if form.get(f"svc_{slug(s)}")]
        if len(chosen) > MAX_SERVICES:
            errors.append(f"Give at most {MAX_SERVICES} services their own rules")
            chosen = chosen[:MAX_SERVICES]
        if chosen:
            policy["services"] = chosen
    elif base.get("services"):
        policy["services"] = list(base["services"])
    # Deposit kinds come from the deposits, which are copied back below.
    ks = kinds(base) if base.get("deposits") else kinds(policy)
    known = set(kinds(base))

    deposits = copy.deepcopy(base.get("deposits") or {})
    for i, k in enumerate(kinds(base) if base.get("deposits") else []):
        d = deposits.get(k)
        if not d or not ("percent" in d or "min_percent" in d):
            continue
        field = "percent" if "percent" in d else "min_percent"
        pct = _percent(form.get(f"dep{i}_value"))
        if pct is None:
            errors.append(f"Deposit for {k}: the percentage needs to be between 1 and 100")
        else:
            d[field] = pct
        when = " ".join((form.get(f"dep{i}_when") or "").split())[:80]
        if when:
            d["when"] = when
        else:
            d.pop("when", None)
    if deposits:
        policy["deposits"] = deposits

    windows, seen = [], set()
    for i in range(MAX_WINDOWS):
        amount_text = (form.get(f"w{i}_amount") or "").strip()
        if not amount_text:
            continue
        amount = _int(amount_text)
        unit = form.get(f"w{i}_unit", "hours")
        label = f"Window {len(windows) + 1}"
        if amount is None or amount == 0 or unit not in ("hours", "days"):
            errors.append(f"{label}: the notice needs to be a whole number of hours or days, more than 0")
            continue
        hours = amount * (24 if unit == "days" else 1)
        if hours > MAX_HOURS:
            errors.append(f"{label}: that's more than a year's notice")
            continue
        if hours in seen:
            errors.append(f"{label}: two windows can't have the same notice")
            continue
        seen.add(hours)
        windows.append(_window(form, f"w{i}", hours, ks, base, label, errors, known))
    if not windows:
        errors.append("Add at least one window with some notice (for example, 24 hours)")
    windows.append(_window(form, "wlast", 0, ks, base, "With less notice than that", errors,
                           known))
    policy["windows"] = sorted((w for w in windows if w), key=lambda w: -w["at_least_hours"])

    notes = [" ".join(line.split()) for line in (form.get("notes") or "").splitlines()]
    notes = [n for n in notes if n]
    if len(notes) > MAX_NOTES:
        errors.append(f"Keep it to {MAX_NOTES} notes")
    if any(len(n) > MAX_NOTE for n in notes):
        errors.append(f"Each note can be up to {MAX_NOTE} characters")
    policy["notes"] = notes[:MAX_NOTES]
    return policy, errors


def _window(form, prefix, hours, ks, base, label, errors, known=()):
    """One window: the same outcome for every kind is stored once as "any".
    A service ticked just now (no column on the page yet) takes the
    outcome of everything else: the last kind."""
    specs = {}
    order = ks[-1:] + ks[:-1]                 # everything else first, to copy from
    for k in order:
        field = f"{prefix}_k_{slug(k)}"
        if k not in known and not form.get(f"{field}_type") and k != ks[-1]:
            specs[k] = specs[ks[-1]]
            continue
        spec = _outcome(form, field, base, label + (f" ({k})" if len(ks) > 1 else ""), errors)
        if spec is None:
            return None
        specs[k] = spec
    specs = {k: specs[k] for k in ks}
    window = {"at_least_hours": hours}
    if len({repr(s) for s in specs.values()}) == 1:
        window["any"] = next(iter(specs.values()))
    else:
        window.update(specs)
    return window


def sections_about_cancelling(sections):
    """Titles of knowledge-base sections that describe cancelling in prose:
    a second copy of the rules, which could disagree with them."""
    return [s["title"].split("(")[0].strip() for s in sections
            if _CANCELLING.search(s["title"] + " " + s["body"])]

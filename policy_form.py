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
import json
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
    By name, not position, because ticking a service adds a card."""
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


def service_choices(config, policy):
    """The services that can have their own rules: the business's own
    list, plus any that already have rules under a name since changed (so
    they're shown, and kept unless unticked). None where kinds are the
    operator's (deposits)."""
    if not can_pick_services(policy):
        return []
    names = service_names(config)
    return names + [s for s in (policy or {}).get("services") or [] if s not in names]


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


# The page shows one card per kind (each service with its own rules, then
# everything else; Sunrise: each cake), each with its OWN windows, because
# that's how an owner thinks of them: "colour needs 48 hours, everything
# else 24" (Casey, 2026-10-17: a service's rules spread over shared rows
# were confusing). The engine keeps one shared list of windows with an
# outcome per kind, so split() takes a kind's own windows out of it and
# merge() puts the cards back together. Nothing in policy.py changed.

def _same(a, b):
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def split(policy, kind):
    """One kind's own rules from the shared windows: ([(hours, spec)],
    last spec), longest first, keeping only the notice at which this
    kind's outcome changes. Belmont with colour at 48 hours shares windows
    48/24/0; a haircut's own are just 24 (no charge) and less (50%)."""
    import policy as policy_mod
    windows = policy_mod._windows(policy)
    if not windows:
        return [], "no_charge"

    def spec(w):
        return copy.deepcopy(w.get(kind, w.get("any", "no_charge")))
    zero = [w for w in windows if w["at_least_hours"] == 0]
    last = spec(zero[0] if zero else windows[-1])
    explicit = [(w["at_least_hours"], spec(w)) for w in windows if w["at_least_hours"] > 0]
    kept = [(h, s) for i, (h, s) in enumerate(explicit)
            if not _same(s, explicit[i + 1][1] if i + 1 < len(explicit) else last)]
    if not kept and explicit:
        kept = [explicit[-1]]                 # the form needs one window with notice
    return kept, last


def merge(per_kind, ks):
    """The shared windows the engine reads, from each kind's own (split's
    reverse). Every kind's notice becomes a window; at each one, a kind
    gets the outcome of its own longest window that the notice reaches. A
    window that changes nothing for anyone is left out."""
    def at(kind, hours):
        own, last = per_kind[kind]
        return next((s for h, s in own if h <= hours), last)
    notice = sorted({h for k in ks for h, _ in per_kind[k][0]}, reverse=True)
    shared = [(h, {k: at(k, h) for k in ks}) for h in notice]
    shared.append((0, {k: per_kind[k][1] for k in ks}))
    kept = [row for i, row in enumerate(shared[:-1])
            if not all(_same(row[1][k], shared[i + 1][1][k]) for k in ks)]
    if not kept and len(shared) > 1:
        kept = [shared[-2]]
    windows = []
    for hours, specs in kept + [shared[-1]]:
        window = {"at_least_hours": hours}
        if all(_same(specs[k], specs[ks[0]]) for k in ks):
            window["any"] = specs[ks[0]]
        else:
            window.update(specs)
        windows.append(window)
    return windows


def card_key(policy, kind):
    """A card's field-name prefix: "gen" for everything else (or a
    business's only kind), "k_<slug>" for a service or a deposit kind. By
    name, not position: ticking a service adds a card."""
    if not policy.get("deposits") and kind == policy.get("what", "order"):
        return "gen"
    return "k_" + slug(kind)


def _cap(text):
    text = str(text)
    return text[:1].upper() + text[1:]


def card_title(policy, kind, alone=False):
    """"Colour", "Custom cake"; everything else is "Every other
    appointment", or "Every appointment" when no service has its own."""
    what = policy.get("what", "order")
    if policy.get("deposits") or kind != what:
        return _cap(kind)
    return f"Every {what}" if alone else f"Every other {what}"


def _outcome_row(spec):
    return dict(zip(("type", "percent"), _spec_row(spec)))


def rows(policy, choices=()):
    """The page's cards for a policy: {"cards", "services", "what",
    "choices", "notes"}. Where services can have their own rules, there's a card for
    every service in `choices` (the business's own list), hidden unless
    it's ticked, and an unticked one starts as a copy of everything else,
    so ticking it can open it at once. A card is {"key", "kind", "title",
    "title_alone", "title_others", "service", "shown", "slug", "deposit",
    "windows": [{"amount", "unit", "type", "percent"}], "last"}."""
    what = policy.get("what", "order")
    current = list(policy.get("services") or [])
    by_service = can_pick_services(policy)

    def card(kind, shown, source):
        own, last = split(policy, source)
        d = ((policy.get("deposits") or {}).get(kind)) or {}
        deposit = None
        if "percent" in d or "min_percent" in d:
            deposit = {"field": "percent" if "percent" in d else "min_percent",
                       "value": f"{d.get('percent', d.get('min_percent')):g}",
                       "when": d.get("when", "")}
        return {"key": card_key(policy, kind), "kind": kind, "slug": slug(kind),
                "title": card_title(policy, kind, alone=not current),
                "title_alone": card_title(policy, kind, alone=True),
                "title_others": card_title(policy, kind),
                "service": by_service and kind != what, "shown": shown, "deposit": deposit,
                "windows": [dict(amount=a, unit=u, **_outcome_row(s))
                            for h, s in own for a, u in [_hours_row(h)]],
                "last": _outcome_row(last)}

    if by_service:
        names = [s for s in choices if s != what]
        names += [s for s in current if s not in names]   # renamed since: still shown
        cards = [card(what, True, what)]
        cards += [card(s, s in current, s if s in current else what) for s in names]
    else:
        names = []
        cards = [card(k, True, k) for k in kinds(policy)]
    return {"cards": cards, "services": current, "what": what,
            "choices": [{"name": s, "slug": slug(s)} for s in names],
            "notes": "\n".join(policy.get("notes") or [])}


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
    the business's own list) get their own card; a hidden card's fields are
    ignored. Each card is read on its own, then merge() makes the shared
    windows the engine reads."""
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

    deposits = copy.deepcopy(base.get("deposits") or {})
    for k, d in deposits.items():
        if not d or not ("percent" in d or "min_percent" in d):
            continue
        key = card_key(base, k)
        field = "percent" if "percent" in d else "min_percent"
        pct = _percent(form.get(f"{key}_dep_value"))
        if pct is None:
            errors.append(f"Deposit for {k}: the percentage needs to be between 1 and 100")
        else:
            d[field] = pct
        when = " ".join((form.get(f"{key}_dep_when") or "").split())[:80]
        if when:
            d["when"] = when
        else:
            d.pop("when", None)
    if deposits:
        policy["deposits"] = deposits

    ks = kinds(policy)
    general = [k for k in ks if card_key(policy, k) == "gen"]
    per_kind = {}
    for k in general + [k for k in ks if k not in general]:
        key = card_key(policy, k)
        if key != "gen" and f"{key}_last_type" not in form and general and general[0] in per_kind:
            per_kind[k] = per_kind[general[0]]    # ticked, but its card wasn't on the page
            continue
        own = _card(form, key, card_title(policy, k, alone=len(ks) == 1), base, errors)
        if own is not None:
            per_kind[k] = own
    policy["windows"] = (merge(per_kind, ks) if len(per_kind) == len(ks)
                         else copy.deepcopy(base.get("windows") or []))

    notes = [" ".join(line.split()) for line in (form.get("notes") or "").splitlines()]
    notes = [n for n in notes if n]
    if len(notes) > MAX_NOTES:
        errors.append(f"Keep it to {MAX_NOTES} notes")
    if any(len(n) > MAX_NOTE for n in notes):
        errors.append(f"Each note can be up to {MAX_NOTE} characters")
    policy["notes"] = notes[:MAX_NOTES]
    return policy, errors


def _card(form, key, title, base, errors):
    """One card's own rules, ([(hours, spec)] longest first, last spec), or
    None with errors added (named by the card: "Colour, window 2: ...")."""
    own, seen, ok = [], set(), True
    for i in range(MAX_WINDOWS):
        prefix = f"{key}_w{i}"
        amount_text = (form.get(f"{prefix}_amount") or "").strip()
        if not amount_text:
            continue
        amount = _int(amount_text)
        unit = form.get(f"{prefix}_unit", "hours")
        label = f"{title}, window {len(own) + 1}"
        if amount is None or amount == 0 or unit not in ("hours", "days"):
            errors.append(f"{label}: the notice needs to be a whole number of hours or days, more than 0")
            ok = False
            continue
        hours = amount * (24 if unit == "days" else 1)
        if hours > MAX_HOURS:
            errors.append(f"{label}: that's more than a year's notice")
            ok = False
            continue
        if hours in seen:
            errors.append(f"{label}: two windows can't have the same notice")
            ok = False
            continue
        seen.add(hours)
        spec = _outcome(form, prefix, base, label, errors)
        if spec is None:
            ok = False
            continue
        own.append((hours, spec))
    if not own:
        errors.append(f"{title}: add at least one window with some notice (for example, 24 hours)")
        ok = False
    last = _outcome(form, f"{key}_last", base, f"{title}, with less notice than that", errors)
    if last is None or not ok:
        return None
    return sorted(own, key=lambda w: -w[0]), last


def sections_about_cancelling(sections):
    """Titles of knowledge-base sections that describe cancelling in prose:
    a second copy of the rules, which could disagree with them."""
    return [s["title"].split("(")[0].strip() for s in sections
            if _CANCELLING.search(s["title"] + " " + s["body"])]

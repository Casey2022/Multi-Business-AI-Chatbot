"""services.py — a business's services, each with a key that never changes.

Five things point at a service by its name: its booking notice
(booking.notice), its length (calendar.service_durations), the booking
questions asked of it (booking.extra_questions[].services), and its own
cancellation rules (policies.cancellation: "services" and each window's
outcome). Owners rename services on Settings. Before 2026-10-23 a rename
broke every one of those links without a word: "custom birthday cakes"
renamed "custom cakes" lost its 72 hours' notice (journey_eval "rename:
custom cakes keep their notice", 0/3: tomorrow's cake was booked), and
"colour" renamed "all-over colour" lost its two hours, its colour-history
question and its own cancellation rules. Only a startup log line said so.

So each service has a key, fixed when it's first listed, and remembers the
names it has had ("aka"). load_config calls apply() after the owner's
overrides: every link is rewritten to the service's current name, whatever
name it was saved under, so everything that reads the config keeps
comparing names and gets the right answer. config["services"] stays a list
of names.

A service in the YAML is a plain name; its key is made from it. The
Settings editor saves {"key", "name", "aka"} rows, so a renamed row keeps
its key and adds its old name to aka.
"""

import re


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_") or "service"


def _norm(text):
    return " ".join(str(text or "").lower().split())


def catalogue(raw):
    """[{"key", "name", "aka": [...]}] from config["services"]: plain names
    (the YAML, or an override saved before keys) or saved rows."""
    out, keys = [], set()
    for item in raw or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            key = str(item.get("key") or "").strip() or slug(name)
            aka = [str(a) for a in item.get("aka") or [] if str(a).strip()]
        else:
            name, key, aka = str(item).strip(), slug(item), []
        if not name:
            continue
        base, n = key, 2
        while key in keys:
            key, n = f"{base}_{n}", n + 1
        keys.add(key)
        out.append({"key": key, "name": name, "aka": aka})
    return out


def resolve(ref, cat):
    """The current name of the service `ref` names (its name, a name it
    had, or its key), or None for something the business doesn't offer."""
    want = _norm(ref)
    if not want:
        return None
    for s in cat:
        if want == _norm(s["name"]) or want in (_norm(a) for a in s["aka"]) \
                or slug(ref) == s["key"]:
            return s["name"]
    return None


def _rename(name, cat):
    return resolve(name, cat) or name


def apply(config):
    """Point every link at the current names; config["services"] becomes
    plain names, config["service_catalogue"] the rows. Safe to run twice."""
    # Once applied, "services" is plain names: the rows (with the old
    # names) are kept in service_catalogue, and a second run reads those.
    cat = config.get("service_catalogue") or catalogue(config.get("services"))
    if not cat:
        return config
    config["services"] = [s["name"] for s in cat]
    config["service_catalogue"] = cat

    cal = config.get("calendar") or {}
    if isinstance(cal.get("service_durations"), dict):
        cal["service_durations"] = {_rename(k, cat): v
                                    for k, v in cal["service_durations"].items()}
    booking = config.get("booking") or {}
    if isinstance(booking.get("notice"), dict):
        booking["notice"] = {_rename(k, cat): v for k, v in booking["notice"].items()}
    for q in booking.get("extra_questions") or []:
        if isinstance(q, dict) and q.get("services"):
            q["services"] = [_rename(s, cat) for s in q["services"]]
    rules = (config.get("policies") or {}).get("cancellation")
    if isinstance(rules, dict) and rules.get("services"):
        old = list(rules["services"])
        rules["services"] = [_rename(s, cat) for s in old]
        for window in rules.get("windows") or []:
            for name in old:
                if name in window and _rename(name, cat) != name:
                    window[_rename(name, cat)] = window.pop(name)
    return config


def rows(config, blank=3):
    """The Settings editor: a row per service (name, key), then `blank`
    empty rows to add one."""
    cat = config.get("service_catalogue") or catalogue(config.get("services"))
    return [{"index": i, "key": s["key"], "name": s["name"]} for i, s in enumerate(cat)] + \
        [{"index": len(cat) + i, "key": "", "name": ""} for i in range(blank)]


def parse(form, current, field="services"):
    """The Settings rows read back into saved rows, or (None, []) when the
    form doesn't carry them. A row keeps its key (posted, hidden); a
    renamed one adds its old name to aka. An emptied row is a removed
    service. Returns (rows, errors)."""
    if f"{field}.posted" not in form:
        return None, []
    cat = current.get("service_catalogue") or catalogue(current.get("services"))
    by_key = {s["key"]: s for s in cat}
    out, errors, seen = [], [], set()
    for i in range(len(cat) + 20):
        name = " ".join((form.get(f"{field}[{i}].name") or "").split())
        key = (form.get(f"{field}[{i}].key") or "").strip()
        if not name:
            continue
        if _norm(name) in seen:
            errors.append(f"{name!r} is listed twice")
            continue
        seen.add(_norm(name))
        was = by_key.get(key)
        if was:
            aka = list(was["aka"])
            if _norm(was["name"]) != _norm(name) and was["name"] not in aka:
                aka.append(was["name"])
            out.append({"key": key, "name": name, "aka": aka})
        else:
            out.append({"key": slug(name), "name": name, "aka": []})
    if not out and not errors:
        errors.append("List at least one service: with none, nothing can be booked")
    return catalogue(out), errors


def same_as_file(saved, base_services):
    """True when saved rows say exactly what the YAML's names do (no
    renames, nothing added or removed): the override can be cleared."""
    return [(s["key"], s["name"], s["aka"]) for s in catalogue(saved)] == \
        [(s["key"], s["name"], s["aka"]) for s in catalogue(base_services)]

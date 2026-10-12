"""menu_form.py — the owner's menu editor: form rows ⇄ menu entries.

The page shows one card per menu item. A price is ONE text box, typed the
way an owner would write it on a board:

    48            a fixed price            → 48
    $3.50         the same                 → 3.5
    from 110      a starting price         → {from: 110}
    110+          the same
    20 to 60      a range (also 20-60)     → {from: 20, to: 60}
    quoted after a trial   anything else   → {quote: "quoted after a trial"}

Saved edits go into the draft menu (menu.save_draft), like a photo's: what
customers are told changes only at Publish. Everything here is pure
(form in, entry and errors out), so it's tested without a server.
"""

import re

import menu

MAX_ROWS = 40
MAX_NAME = 80
MAX_NOTE = 160
BLANK_ROWS = 2          # empty rows under each table, for adding

_MONEY = r"\$?\s*(\d{1,6}(?:,\d{3})*(?:\.\d{1,2})?)"


def _num(text):
    return float(text.replace(",", ""))


def parse_price_text(text):
    """(value for the YAML, error or None). Empty text is (None, None)."""
    t = " ".join((text or "").split())
    if not t:
        return None, None
    m = re.fullmatch(_MONEY, t)
    if m:
        return _clean(_num(m.group(1))), None
    m = re.fullmatch(r"(?:from|starting at|starts at)\s+" + _MONEY + r"\+?", t, re.I) \
        or re.fullmatch(_MONEY + r"\s*\+", t)
    if m:
        return {"from": _clean(_num(m.group(1)))}, None
    m = re.fullmatch(_MONEY + r"\s*(?:-|–|to)\s*" + _MONEY, t, re.I)
    if m:
        low, high = _num(m.group(1)), _num(m.group(2))
        if high < low:
            return None, f"“{t}”: the second price is lower than the first"
        return {"from": _clean(low), "to": _clean(high)}, None
    if re.search(r"\d", t) and not re.search(r"[a-z]{3,}", t, re.I):
        return None, f"“{t}” isn't a price this understands (try 48, from 110, or 20 to 60)"
    return {"quote": t[:MAX_NOTE]}, None


def _clean(x):
    return int(x) if float(x).is_integer() else round(x, 2)


def _shown(x):
    x = float(x)
    return str(int(x)) if x.is_integer() else f"{x:.2f}"


def price_to_text(value):
    """The text box's contents for a stored price (the reverse of the above)."""
    if value is None:
        return ""
    if isinstance(value, dict):
        if value.get("quote"):
            return str(value["quote"])
        low = _shown(value.get("from", 0))
        if value.get("to") is not None:
            return f"{low} to {_shown(value['to'])}"
        return f"from {low}"
    return _shown(value)


# ---------------------------------------------------------------------------
# Item → rows for the page
# ---------------------------------------------------------------------------

def item_rows(entry):
    """What the form shows for one stored menu entry (a raw YAML dict)."""
    entry = entry or {}
    options = entry.get("options") or {}
    notes = entry.get("notes") or {}
    rows = [{"original": name, "name": name, "price": price_to_text(p),
             "note": notes.get(name, ""), "order": i + 1}
            for i, (name, p) in enumerate(options.items())]
    add_rows = []
    for add_name, spec in (entry.get("add_ons") or {}).items():
        if not isinstance(spec, dict) or "prices" not in spec:
            spec = {"prices": {"any": spec}}
        for on, p in (spec.get("prices") or {}).items():
            add_rows.append({"name": add_name, "on": on, "price": price_to_text(p),
                             "each": bool(spec.get("each"))})
    packs = [{"count": c, "price": price_to_text(p)}
             for c, p in (entry.get("packs") or {}).items()]
    return {
        "item": entry.get("item", ""),
        "aliases": ", ".join(entry.get("aliases") or []),
        "section": entry.get("section", ""),
        "options": rows + [{"original": "", "name": "", "price": "", "note": "",
                            "order": len(rows) + k + 1} for k in range(BLANK_ROWS)],
        "add_ons": add_rows + [{"name": "", "on": "any", "price": "", "each": False}
                               for _ in range(BLANK_ROWS)],
        "each": price_to_text(entry.get("each")),
        "each_note": notes.get("each", ""),
        "sold_by": entry.get("sold_by") or "",
        "minimum": entry.get("minimum") or "",
        "packs": packs + [{"count": "", "price": ""} for _ in range(BLANK_ROWS)],
        "by_piece": entry.get("each") is not None,
    }


# ---------------------------------------------------------------------------
# Form → item
# ---------------------------------------------------------------------------

def _rows(form, prefix, fields):
    out = []
    for i in range(MAX_ROWS):
        row = {f: (form.get(f"{prefix}{i}_{f}") or "").strip() for f in fields}
        if not any(row.values()) and form.get(f"{prefix}{i}_name") is None \
                and form.get(f"{prefix}{i}_count") is None:
            continue
        row["remove"] = form.get(f"{prefix}{i}_remove") == "yes"
        out.append(row)
    return out


def _int(text, label, errors):
    if not text:
        return None
    try:
        value = int(text)
    except ValueError:
        errors.append(f"{label} must be a whole number")
        return None
    if value < 1:
        errors.append(f"{label} must be at least 1")
        return None
    return value


def parse_item_form(form, other_item_names=()):
    """(entry for the YAML, errors). `form` is a dict-like of the submitted
    fields; `other_item_names` are the menu's other items, which this one
    can't share a name with."""
    errors = []
    name = " ".join((form.get("item") or "").split())[:MAX_NAME]
    if not name:
        errors.append("Give the item a name")
    elif name.lower() in {n.lower() for n in other_item_names}:
        errors.append(f"There's already an item called “{name}”")
    entry = {"item": name}
    aliases = [a.strip()[:MAX_NAME] for a in (form.get("aliases") or "").split(",") if a.strip()]
    if aliases:
        entry["aliases"] = aliases
    section = (form.get("section") or "").strip()
    if section:
        entry["section"] = section

    # Options, with renames remembered so add-ons and notes follow them.
    options, notes, renamed, seen, removed = {}, {}, {}, set(), set()
    # Shown in the order the owner set ("Order" numbers, or the ↑↓ buttons
    # that renumber them): the menu lists options in this order everywhere.
    option_rows = _rows(form, "opt", ("original", "name", "price", "note", "order"))
    def _position(pair):
        i, row = pair
        try:
            return (float(row["order"]), i)
        except (TypeError, ValueError):
            return (float("inf"), i)
    option_rows = [row for _i, row in sorted(enumerate(option_rows), key=_position)]
    for row in option_rows:
        if row["remove"] or (not row["name"] and not row["price"]):
            if row["original"]:
                removed.add(row["original"])
            continue
        if not row["name"]:
            errors.append(f"A price ({row['price']}) has no option name")
            continue
        opt = " ".join(row["name"].split())[:MAX_NAME]
        if opt.lower() in seen:
            errors.append(f"“{opt}” is listed twice")
            continue
        seen.add(opt.lower())
        value, problem = parse_price_text(row["price"])
        if problem:
            errors.append(f"{opt}: {problem}")
            continue
        if value is None:
            errors.append(f"{opt} has no price")
            continue
        options[opt] = value
        if row["note"]:
            notes[opt] = row["note"][:MAX_NOTE]
        if row["original"] and row["original"] != opt:
            renamed[row["original"]] = opt
    if options:
        entry["options"] = options

    # Sold by the piece.
    each, problem = parse_price_text(form.get("each"))
    if problem:
        errors.append(f"Price each: {problem}")
    if each is not None:
        entry["each"] = each
        sold_by = _int((form.get("sold_by") or "").strip(), "“Only in multiples of”", errors)
        minimum = _int((form.get("minimum") or "").strip(), "“Minimum”", errors)
        if sold_by:
            entry["sold_by"] = sold_by
        if minimum:
            entry["minimum"] = minimum
        packs = {}
        for row in _rows(form, "pack", ("count", "price")):
            if row["remove"] or (not row["count"] and not row["price"]):
                continue
            count = _int(row["count"], "A pack size", errors)
            value, problem = parse_price_text(row["price"])
            if problem:
                errors.append(f"Pack of {row['count']}: {problem}")
            elif count and value is None:
                errors.append(f"Pack of {count} has no price")
            elif count:
                packs[count] = value
        if packs:
            entry["packs"] = packs
        if form.get("each_note", "").strip():
            notes["each"] = form["each_note"].strip()[:MAX_NOTE]
    elif any((form.get(k) or "").strip() for k in ("sold_by", "minimum")):
        errors.append("“Only in multiples of” and “Minimum” need a price each")

    if notes:
        entry["notes"] = notes

    # Extras: one row per (extra, which option) price.
    add_ons = {}
    for row in _rows(form, "add", ("name", "on", "price")):
        if row["remove"] or (not row["name"] and not row["price"]):
            continue
        if not row["name"]:
            errors.append(f"An extra's price ({row['price']}) has no name")
            continue
        extra = " ".join(row["name"].split())[:MAX_NAME]
        if row["on"] in removed:
            continue            # its option was removed, so its price goes too
        on = renamed.get(row["on"], row["on"]) or "any"
        if on != "any" and on not in options:
            errors.append(f"{extra}: priced for “{on}”, which isn't an option any more")
            continue
        value, problem = parse_price_text(row["price"])
        if problem:
            errors.append(f"{extra}: {problem}")
            continue
        if value is None:
            errors.append(f"{extra} has no price")
            continue
        add_ons.setdefault(extra, {"prices": {}})["prices"][on] = value
    # "each" ticked on any row of an extra applies to the extra.
    for i in range(MAX_ROWS):
        if form.get(f"add{i}_each") == "yes":
            extra = " ".join((form.get(f"add{i}_name") or "").split())[:MAX_NAME]
            if extra in add_ons:
                add_ons[extra]["each"] = True
    if add_ons:
        entry["add_ons"] = add_ons

    if "options" not in entry and "each" not in entry:
        errors.append("Give it at least one priced option, or a price each")
    if not errors:
        errors.extend(menu.problems({"menu": [entry]}))
    return entry, errors


def replace_item(menu_list, index, entry, expected_name):
    """A new menu list with item `index` replaced (None deletes it), or
    (None, reason) when the item there isn't the one the page showed, so a
    stale page can't overwrite someone else's change."""
    items = list(menu_list or [])
    if index < 0 or index >= len(items) or items[index].get("item") != expected_name:
        return None, ("The menu changed since this page was loaded. Reload "
                      "the page and make the change again.")
    if entry is None:
        del items[index]
    else:
        items[index] = entry
    return items, None


def form_rows(form):
    """The rows the page shows, from a submitted form (to re-show it with
    the owner's typing intact after an error)."""
    def rows(prefix, fields, blank):
        out = [r for r in ({f: (form.get(f"{prefix}{i}_{f}") or "") for f in fields}
                           for i in range(MAX_ROWS))
               if any(v for k, v in r.items() if k != "order")]
        return out + [dict(blank) for _ in range(BLANK_ROWS)]
    add = []
    for i in range(MAX_ROWS):
        r = {f: (form.get(f"add{i}_{f}") or "") for f in ("name", "on", "price")}
        if any(v for k, v in r.items() if k != "on"):
            r["each"] = form.get(f"add{i}_each") == "yes"
            add.append(r)
    return {
        "item": form.get("item", ""), "aliases": form.get("aliases", ""),
        "section": form.get("section", ""),
        "options": rows("opt", ("original", "name", "price", "note", "order"),
                        {"original": "", "name": "", "price": "", "note": "", "order": ""}),
        "add_ons": add + [{"name": "", "on": "any", "price": "", "each": False}
                          for _ in range(BLANK_ROWS)],
        "each": form.get("each", ""), "each_note": form.get("each_note", ""),
        "sold_by": form.get("sold_by", ""), "minimum": form.get("minimum", ""),
        "packs": rows("pack", ("count", "price"), {"count": "", "price": ""}),
        "by_piece": bool((form.get("each") or "").strip()),
    }

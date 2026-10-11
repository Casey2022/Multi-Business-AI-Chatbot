"""menu.py — a business's menu as data, and the menu_price tool.

Why this exists (2026-10-08/10). Price SUMS were never the problem; every
total in rag_eval passed. What failed, every run and through two prompt
rules, was pricing something the menu doesn't list: "30 wings are $40!"
(there is no 30), and a Sicilian priced with the large's topping price
(none was given). Both wrong answers used real menu prices on the wrong
thing, which no prompt instruction fixed and no number check could catch.
So, as with notice periods and cancellation windows (policy.py), the facts
moved into data and the matching into code:

- `menu:` in the business YAML is the only copy of its menu prices.
- Each knowledge-base section keeps everything else about the item; its
  price line is generated from the menu (attach_prices), so retrieval and
  the owner's page show prices without a second copy.
- `menu_price` takes what the customer asked for IN THEIR OWN WORDS ("30
  wings", "large", "2 toppings") and matches it here. Something that isn't
  on the menu comes back as not listed, with what is.
"""

import copy
import re
from itertools import product

# Words that say nothing about which option: "a 10 piece order of wings".
_FILLER = {"a", "an", "the", "of", "with", "and", "on", "order", "orders",
           "size", "sized", "piece", "pieces", "pc", "pcs", "count", "ct",
           "inch", "inches", "in", "one", "please", "for", "some", "just"}
_NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                 "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
                 "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
                 "dozen": 12}


def money(amount):
    text = f"${amount:,.2f}"
    return text[:-3] if text.endswith(".00") else text


# ---------------------------------------------------------------------------
# The data
# ---------------------------------------------------------------------------

def items(config):
    """The menu as a list of dicts (empty when the business has none)."""
    raw = (config or {}).get("menu") or []
    out = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("item"):
            continue
        add_ons = {}
        for name, spec in (entry.get("add_ons") or {}).items():
            spec = spec if isinstance(spec, dict) else {"prices": {"any": spec}}
            add_ons[str(name)] = {"each": bool(spec.get("each")),
                                  "prices": {str(k): float(v) for k, v in
                                             (spec.get("prices") or {}).items()}}
        out.append({
            "item": str(entry["item"]),
            "aliases": [str(a) for a in (entry.get("aliases") or [])],
            "section": str(entry.get("section") or ""),
            "options": {str(k): float(v) for k, v in
                        (entry.get("options") or {}).items()},
            "add_ons": add_ons,
        })
    return out


def problems(config):
    """What's wrong with a menu block, as sentences (empty when it's fine)."""
    found = []
    for it in items(config):
        if not it["options"]:
            found.append(f"{it['item']}: no options")
        for name, spec in it["add_ons"].items():
            for option in spec["prices"]:
                if option != "any" and option not in it["options"]:
                    found.append(f"{it['item']} / {name}: priced for "
                                 f"{option!r}, which isn't an option")
        for price in list(it["options"].values()) + [
                p for s in it["add_ons"].values() for p in s["prices"].values()]:
            if price < 0:
                found.append(f"{it['item']}: a negative price")
    return found


# ---------------------------------------------------------------------------
# The price lines the knowledge base shows
# ---------------------------------------------------------------------------

def _article(option):
    return "an" if option[:1].lower() in "aeio" or option[:2] in ("18", "8-", "11") else "a"


def render_item(it):
    """One item's prices as plain sentences, e.g. "Pizza prices: 10-inch
    personal $11, …. Topping: $1.75 each on a 10-inch personal, …"."""
    parts = [f"{it['item'].capitalize()} prices: "
             + ", ".join(f"{o} {money(p)}" for o, p in it["options"].items()) + "."]
    for name, spec in it["add_ons"].items():
        each = " each" if spec["each"] else " extra"
        prices = spec["prices"]
        if "any" in prices:
            parts.append(f"{name.capitalize()}: {money(prices['any'])}{each} on any "
                         f"{it['item']}.")
            continue
        listed = [f"{money(p)}{each} on {_article(o)} {o}" for o, p in prices.items()]
        line = f"{name.capitalize()}: " + ", ".join(listed)
        missing = [o for o in it["options"] if o not in prices]
        if missing:
            line += (f"; not available on {_article(missing[0])} " + ", ".join(missing)
                     if len(prices) > 1 else
                     f" only (not available on any other {it['item']})")
        parts.append(line + ".")
    return " ".join(parts)


def render_menu(config):
    """Every item's price lines (for checks and the owner's page)."""
    return "\n".join(render_item(it) for it in items(config))


def _title(chunk):
    first = (chunk or "").lstrip().splitlines()[0] if chunk and chunk.strip() else ""
    return first.lstrip("#").strip()


def price_lines_for(title, config):
    """The generated price lines for a section with this title ("" if none)."""
    lowered = (title or "").lower()
    return " ".join(render_item(it) for it in items(config)
                    if it["section"] and lowered.startswith(it["section"].lower()))


def attach_prices_to_markdown(text, config):
    """A seed document with each section's price lines added at the end of
    the section, BEFORE it's cut into chunks (boot ingestion).

    Attaching after chunking (first version, 2026-10-10) put the price line
    on every piece of a section that chunk_text had split, straight after a
    sentence cut in half ("…stops taking orders. Trays / Party tray prices:
    …"), and Friday at five started failing. Added to the section first, it
    lands once, at the section's end, wherever the cut falls."""
    if not items(config):
        return text
    blocks = re.split(r"(?=^## )", text, flags=re.M)
    out = []
    for block in blocks:
        if block.startswith("## "):
            lines = price_lines_for(block.splitlines()[0][3:], config)
            if lines:
                block = block.rstrip() + "\n" + lines + "\n\n"
        out.append(block)
    return "".join(out)


def attach_prices(chunks, config):
    """Each section chunk with its menu items' price lines added: for
    sections from the database, which are one chunk each (Publish, the
    owner's preview). Boot cuts the seed into chunks, so it adds the lines
    before cutting (attach_prices_to_markdown)."""
    if not items(config):
        return list(chunks)
    out = []
    for chunk in chunks:
        lines = price_lines_for(_title(chunk), config)
        out.append(f"{chunk.rstrip()}\n{lines}" if lines else chunk)
    return out


# ---------------------------------------------------------------------------
# Matching what the customer said
# ---------------------------------------------------------------------------

def _words(text):
    text = (text or "").lower().replace("-", " ").replace('"', " inch ")
    text = re.sub(r"(\d+)\s*(?:x|×)\s*(\d+)", r"\1x\2", text)
    tokens = re.findall(r"\d+x\d+|\d+(?:\.\d+)?|[a-z]+", text)
    out = []
    for t in tokens:
        t = str(_NUMBER_WORDS[t]) if t in _NUMBER_WORDS else t
        if t.endswith("s") and not t.isdigit() and len(t) > 3 and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


def _numbers(words):
    return {w for w in words if re.fullmatch(r"\d+(?:\.\d+)?|\d+x\d+", w)}


def find_item(name, config):
    wanted = set(_words(name))
    for it in items(config):
        names = [it["item"], *it["aliases"]]
        if any(set(_words(n)) and set(_words(n)) <= wanted for n in names):
            return it
    return None


def match_option(it, asked):
    """(option name or None, reason). The customer's words must agree with
    the option on every number, and every other word must be in the option's
    name. One option for any variety ("any sub") matches unless a number
    disagrees."""
    item_words = set()
    for n in [it["item"], *it["aliases"]]:
        item_words |= set(_words(n))
    words = [w for w in _words(asked) if w not in _FILLER and w not in item_words]
    numbers, others = _numbers(words), [w for w in words if w not in _numbers(words)]
    options = list(it["options"])
    if not words:
        return (options[0], "only option") if len(options) == 1 else (None, "which")
    hits = []
    for option in options:
        own = set(_words(option))
        if numbers and not numbers <= own:
            continue
        if others and not set(others) <= own and len(options) > 1:
            continue
        hits.append(option)
    if len(hits) == 1:
        return hits[0], "matched"
    if len(hits) > 1:
        return None, "which"
    return None, "not listed"


def _cheapest_combination(it, count):
    """For options that are counts ("10 wings"): the cheapest way to make
    exactly `count` from them, as [(option, how many)], or None."""
    sized = {}
    for option, price in it["options"].items():
        nums = [int(float(n)) for n in _numbers(_words(option)) if "x" not in n]
        if len(nums) != 1:
            return None
        sized[option] = (nums[0], price)
    best = None
    limits = [range(0, count // size + 1) for size, _ in sized.values()]
    for combo in product(*limits):
        total = sum(n * sized[o][0] for n, o in zip(combo, sized))
        if total != count:
            continue
        cost = sum(n * sized[o][1] for n, o in zip(combo, sized))
        if best is None or cost < best[0]:
            best = (cost, [(o, n) for n, o in zip(combo, sized) if n])
    return best


def _next_size_up(it, count):
    sized = []
    for option, price in it["options"].items():
        nums = [int(float(n)) for n in _numbers(_words(option)) if "x" not in n]
        if len(nums) == 1 and nums[0] > count:
            sized.append((nums[0], option, price))
    return min(sized) if sized else None


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------

def menu_price(config, order):
    """Price what the customer asked for. `order` is a list of
    {"item", "asked_for", "quantity", "add_ons": [{"name", "count"}]}.

    Returns {"lines", "total"} when everything is on the menu, otherwise
    {"not_listed": [...], "listed": {...}, ...} saying what isn't, what is,
    and (for counts like wings) the combinations that make it. The rule
    comes first, as policy.py's results do."""
    lines, total, not_listed, listed, suggestions, ask = [], 0.0, [], {}, [], []
    for entry in order or []:
        it = find_item(entry.get("item", ""), config)
        if it is None:
            not_listed.append(f"{entry.get('item')!r} isn't on the menu")
            listed["menu items"] = [i["item"] for i in items(config)]
            continue
        asked = str(entry.get("asked_for") or "")
        quantity = max(1, int(entry.get("quantity") or 1))
        option, why = match_option(it, asked)
        if option is None:
            listed[it["item"]] = {o: money(p) for o, p in it["options"].items()}
            if why == "which":
                ask.append(f"which {it['item']}: " + ", ".join(it["options"]))
                continue
            not_listed.append(f"{it['item']} doesn't come as {asked!r}; that "
                              f"isn't on the menu")
            nums = [int(float(n)) for n in _numbers(_words(asked)) if "x" not in n]
            if len(nums) == 1:
                combo = _cheapest_combination(it, nums[0])
                if combo:
                    cost, parts = combo
                    suggestions.append(
                        " + ".join(f"{n} x {o}" if n > 1 else o for o, n in parts)
                        + f" makes {nums[0]} for {money(cost)}")
                up = _next_size_up(it, nums[0])
                if up:
                    suggestions.append(f"the next size up is {up[1]} for {money(up[2])}")
            continue
        price = it["options"][option]
        line_total = price * quantity
        label = _label(it, option)
        lines.append(f"{quantity} x {label} at {money(price)}"
                     if quantity > 1 else f"{label}: {money(price)}")
        for add in entry.get("add_ons") or []:
            name = str(add.get("name") or "")
            count = max(1, int(add.get("count") or 1))
            spec = _find_add_on(it, name)
            if spec is None:
                not_listed.append(f"no price is listed for {name!r} on {it['item']}")
                continue
            add_name, rule = spec
            each = rule["prices"].get(option, rule["prices"].get("any"))
            if each is None:
                not_listed.append(f"{add_name} isn't available on the {option} "
                                  f"(no price is listed for it)")
                listed[f"{add_name} (for)"] = list(rule["prices"])
                continue
            n = count if rule["each"] else 1
            per = f" (on each of the {quantity})" if quantity > 1 else ""
            lines.append(f"{n} x {add_name} on the {option} at {money(each)}{per}"
                         if n > 1 else f"{add_name} on the {option}: {money(each)}{per}")
            line_total += each * n * quantity
        total += line_total
    if not_listed or ask:
        result = {}
        if not_listed:
            result["not_listed"] = not_listed
            result["say"] = ("Say plainly, before anything else, that this "
                             "isn't on the menu or isn't available; don't give "
                             "it a price. Then offer what is listed.")
        if ask:
            result["ask"] = ask
        result["listed"] = listed
        if suggestions:
            result["options_that_work"] = suggestions
        if lines:
            result["priced_so_far"] = lines
        return result
    return {"lines": lines, "total": money(total),
            "say": "These are the menu prices; the total is before any "
                   "delivery fee or tax."}


def _label(it, option):
    """"18-inch large pizza", but "10 wings", not "10 wings wings"."""
    item_words = set()
    for n in [it["item"], *it["aliases"]]:
        item_words |= set(_words(n))
    return option if set(_words(option)) & item_words else f"{option} {it['item']}"


def _find_add_on(it, name):
    wanted = set(_words(name)) - _FILLER
    for add_name, rule in it["add_ons"].items():
        own = set(_words(add_name))
        if wanted and (wanted <= own or own <= wanted):
            return add_name, rule
    return None


def tool(config):
    names = [it["item"] for it in items(config)]
    return {
        "name": "menu_price",
        "description": (
            "Look up menu prices and add them up. Pass what the customer asked "
            "for in their own words: don't translate it to a menu size first "
            "(\"30 wings\" stays \"30 wings\"). The tool matches it against "
            "the menu and says if it isn't listed."),
        "input_schema": {
            "type": "object",
            "properties": {
                "order": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item": {"type": "string", "enum": names},
                            "asked_for": {"type": "string", "description":
                                "the size, quantity or kind in the customer's "
                                "words, e.g. \"large\", \"30\", \"chicken\""},
                            "quantity": {"type": "integer", "description":
                                "how many of this item (1 unless they said)"},
                            "add_ons": {"type": "array", "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "count": {"type": "integer"}},
                                "required": ["name", "count"]}},
                        },
                        "required": ["item", "asked_for", "quantity", "add_ons"],
                    },
                },
            },
            "required": ["order"],
        },
    }


def mentions_item(message, config):
    """Does the message name something on the menu (an item, an alias, or a
    word from an option's name like "large" or "antipasto")?"""
    said = set(_words(message))
    generic = _FILLER | {"tray", "salad", "sub", "square", "pan", "roll", "any"}
    for it in items(config):
        for name in [it["item"], *it["aliases"]]:
            if set(_words(name)) and set(_words(name)) <= said:
                return True
        for option in it["options"]:
            for w in _words(option):
                if w.isalpha() and len(w) >= 4 and w not in generic and w in said:
                    return True
    return False


PROMPT_RULE = (
    "Menu prices come from menu_price, not from the excerpts or memory: call "
    "it with each item, the size or quantity in the customer's own words, "
    "how many, and any extras, and answer from its result. If it says "
    "something isn't listed, your first sentence says so; then offer what it "
    "lists (and any combination it gives). Never price something it didn't "
    "price. Delivery fees and minimums are in the excerpts, not the menu.")


# ---------------------------------------------------------------------------
# Menu changes waiting to be published
# ---------------------------------------------------------------------------
#
# A menu change (from a photo) is a draft until Publish, like a section edit:
# the menu_price tool and the knowledge base keep using the published menu,
# so customers never see half of a change. Stored as config overrides:
# "menu_draft" (pending) and "menu" (published, when it differs from the
# YAML). load_config applies only "menu".

DRAFT_FIELD = "menu_draft"


def draft_menu(business_id):
    from db import get_config_overrides
    value = get_config_overrides(business_id).get(DRAFT_FIELD)
    return value if isinstance(value, list) else None


def with_draft(config, business_id):
    """A copy of config with the draft menu in place (for the owner's preview
    and for Publish). The same config if there's no draft."""
    draft = draft_menu(business_id) if business_id is not None else None
    if draft is None:
        return config
    out = copy.deepcopy(config)
    out["menu"] = draft
    return out


def save_draft(business_id, menu_list, updated_by=None):
    from db import set_config_override, mark_documents_dirty
    set_config_override(business_id, DRAFT_FIELD, menu_list, updated_by=updated_by)
    mark_documents_dirty(business_id)


def publish_draft(business_id, updated_by=None):
    """Make the draft menu the live one. True if there was a draft."""
    from db import set_config_override, clear_config_override
    draft = draft_menu(business_id)
    if draft is None:
        return False
    set_config_override(business_id, "menu", draft, updated_by=updated_by)
    clear_config_override(business_id, DRAFT_FIELD)
    return True


def discard_draft(business_id):
    from db import clear_config_override
    clear_config_override(business_id, DRAFT_FIELD)


def differences(old_config, new_config):
    """Plain lines saying how two menus differ ("18-inch large pizza: $22 →
    $25", "new: calzone"), for the owner's banner."""
    before = {it["item"]: it for it in items(old_config)}
    lines = []
    for it in items(new_config):
        was = before.get(it["item"])
        if was is None:
            lines.append(f"new on the menu: {it['item']} ("
                         + ", ".join(f"{o} {money(p)}" for o, p in it["options"].items())
                         + ")")
            continue
        for option, price in it["options"].items():
            old = was["options"].get(option)
            if old is None:
                lines.append(f"new: {_label(it, option)} {money(price)}")
            elif old != price:
                lines.append(f"{_label(it, option)}: {money(old)} → {money(price)}")
        for name, spec in it["add_ons"].items():
            old_spec = was["add_ons"].get(name, {"prices": {}})
            for option, price in spec["prices"].items():
                old = old_spec["prices"].get(option)
                where = "any" if option == "any" else option
                if old is None:
                    lines.append(f"new: {name} on {where} {money(price)}")
                elif old != price:
                    lines.append(f"{name} on {where}: {money(old)} → {money(price)}")
    return lines

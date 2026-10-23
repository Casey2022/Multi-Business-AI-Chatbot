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

ENABLED_FIELD = "menu_enabled"


def has_items(config):
    """Whether config carries any menu items at all, switch or no switch."""
    return any(isinstance(e, dict) and e.get("item")
               for e in (config or {}).get("menu") or [])


def enabled(config):
    """Whether this business keeps prices on a menu (the Settings switch).

    A plumber or a contractor quotes per job, so a Menu page would only be
    an empty tab. Never set means: on exactly when there is a menu."""
    flag = (config or {}).get(ENABLED_FIELD)
    if flag is None:
        return has_items(config)
    return bool(flag)


def _kinds(raw):
    """An add-on's kinds ("pepperoni", "mushroom": each a topping) as
    {group or "": [names]}, in order. Written in the YAML as a list, or as
    groups ({"Meats": [...], "Vegetables": [...]})."""
    if isinstance(raw, dict):
        return {str(g): [str(k) for k in (v or [])] for g, v in raw.items()}
    if isinstance(raw, list):
        return {"": [str(k) for k in raw]}
    return {}


def kind_names(rule):
    """Every kind an add-on has, in order."""
    return [k for names in (rule.get("kinds") or {}).values() for k in names]


def items(config):
    """The menu as a list of dicts (empty when the business has none, or has
    the menu switched off — then nothing anywhere quotes from it).

    Each item: item, aliases, section, options {name: price}, add_ons
    {name: {"each", "prices": {option or "any": price}}}, notes {option: text},
    and for things sold by the piece: each (price), sold_by (only in multiples
    of), minimum, packs {count: price}. A price is a dict from parse_price."""
    if not enabled(config):
        return []
    raw = (config or {}).get("menu") or []
    out = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("item"):
            continue
        add_ons = {}
        for name, spec in (entry.get("add_ons") or {}).items():
            if not isinstance(spec, dict) or "prices" not in spec:
                spec = {"prices": {"any": spec}}
            add_ons[str(name)] = {"each": bool(spec.get("each")),
                                  "prices": {str(k): parse_price(v) for k, v in
                                             (spec.get("prices") or {}).items()},
                                  "kinds": _kinds(spec.get("kinds")),
                                  "kinds_section": str(spec.get("kinds_section") or "")}
        out.append({
            "item": str(entry["item"]),
            "aliases": [str(a) for a in (entry.get("aliases") or [])],
            "section": str(entry.get("section") or ""),
            "options": {str(k): parse_price(v) for k, v in
                        (entry.get("options") or {}).items()},
            "add_ons": add_ons,
            "notes": {str(k): str(v) for k, v in (entry.get("notes") or {}).items()},
            "each": parse_price(entry["each"]) if entry.get("each") is not None else None,
            "sold_by": int(entry["sold_by"]) if entry.get("sold_by") else None,
            "minimum": int(entry["minimum"]) if entry.get("minimum") else None,
            "packs": {int(k): parse_price(v) for k, v in (entry.get("packs") or {}).items()},
        })
    return out


# ---------------------------------------------------------------------------
# Prices: fixed, starting at, a range, or by quote
# ---------------------------------------------------------------------------

def parse_price(value):
    """{"low", "high", "quote"}: 48 → $48; {from: 110} → from $110 (high
    None); {from: 20, to: 60} → $20 to $60; {quote: "after a trial"} → no
    figure, said as written."""
    if isinstance(value, dict):
        if value.get("quote"):
            return {"low": None, "high": None, "quote": str(value["quote"])}
        low = float(value.get("from", value.get("low", 0)))
        high = value.get("to", value.get("high"))
        return {"low": low, "high": float(high) if high is not None else None,
                "quote": None}
    number = float(value)
    return {"low": number, "high": number, "quote": None}


def is_fixed(price):
    return price["quote"] is None and price["low"] == price["high"]


def price_text(price):
    if price["quote"]:
        return price["quote"]
    if price["high"] is None:
        return f"from {money(price['low'])}"
    if price["low"] == price["high"]:
        return money(price["low"])
    return f"{money(price['low'])} to {money(price['high'])}"


def _scaled(price, n):
    if price["quote"]:
        return dict(price)
    return {"low": price["low"] * n,
            "high": price["high"] * n if price["high"] is not None else None,
            "quote": None}


def _total(prices):
    """The sum of several prices, as a price (a quote anywhere → no figure)."""
    if len(prices) == 1:
        return dict(prices[0])
    quotes = [p["quote"] for p in prices if p["quote"]]
    if quotes:
        return {"low": None, "high": None,
                "quote": "partly priced separately (" + "; ".join(quotes) + ")"}
    low = sum(p["low"] for p in prices)
    high = (None if any(p["high"] is None for p in prices)
            else sum(p["high"] for p in prices))
    return {"low": low, "high": high, "quote": None}


def problems(config):
    """What's wrong with a menu block, as sentences (empty when it's fine)."""
    found = []
    for it in items(config):
        if not it["options"] and it["each"] is None:
            found.append(f"{it['item']}: no options and no price each")
        for name, spec in it["add_ons"].items():
            for option in spec["prices"]:
                if option != "any" and option not in it["options"]:
                    found.append(f"{it['item']} / {name}: priced for "
                                 f"{option!r}, which isn't an option")
        for option in it["notes"]:
            if option not in it["options"] and option != "each":
                found.append(f"{it['item']}: a note for {option!r}, which isn't an option")
        prices = (list(it["options"].values()) + list(it["packs"].values())
                  + [p for s in it["add_ons"].values() for p in s["prices"].values()]
                  + ([it["each"]] if it["each"] else []))
        for p in prices:
            if p["quote"]:
                continue
            if p["low"] < 0 or (p["high"] is not None and p["high"] < p["low"]):
                found.append(f"{it['item']}: a price below zero or a range backwards")
        if (it["sold_by"] or it["minimum"] or it["packs"]) and it["each"] is None:
            found.append(f"{it['item']}: sold_by / minimum / packs need a price each")
    return found


# ---------------------------------------------------------------------------
# The price lines the knowledge base shows
# ---------------------------------------------------------------------------

def _article(option):
    return "an" if option[:1].lower() in "aeio" or option[:2] in ("18", "8-", "11") else "a"


def _per_piece_text(it):
    parts = [f"{price_text(it['each'])} each"]
    for count, p in sorted(it["packs"].items()):
        parts.append(f"{price_text(p)} for {count}")
    line = " or ".join(parts)
    if it["sold_by"]:
        line += (f", sold only in multiples of {it['sold_by']} "
                 f"({price_text(_scaled(it['each'], it['sold_by']))} for {it['sold_by']})")
    if it["minimum"]:
        line += f", minimum {it['minimum']}"
    return line


def render_item(it):
    """One item's prices as plain sentences, e.g. "Pizza prices: 10-inch
    personal $11, …. Topping: $1.75 each on a 10-inch personal, …"."""
    name = it["item"].capitalize()
    parts = []
    if it["each"] is not None:
        parts.append(f"{name} prices: {_per_piece_text(it)}"
                     + (f" ({it['notes']['each']})" if "each" in it["notes"] else "") + ".")
    if it["options"]:
        listed = []
        for o, p in it["options"].items():
            text = f"{o} {price_text(p)}"
            if o in it["notes"]:
                text += f" ({it['notes'][o]})"
            listed.append(text)
        parts.append((f"{name} prices: " if it["each"] is None else "Also: ")
                     + ", ".join(listed) + ".")
    for add_name, spec in it["add_ons"].items():
        each = " each" if spec["each"] else " extra"
        prices = spec["prices"]
        if "any" in prices:
            parts.append(f"{add_name.capitalize()}: {price_text(prices['any'])}{each} "
                         f"on any {it['item']}.")
            continue
        listed = [f"{price_text(p)}{each} on {_article(o)} {o}" for o, p in prices.items()]
        line = f"{add_name.capitalize()}: " + ", ".join(listed)
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


def section_core(title):
    """A section title without its "(Topics: …)" tail, for matching."""
    return " ".join((title or "").split("(")[0].lower().split())


def price_lines_for(title, config):
    """The generated price lines for a section with this title ("" if none).
    Matched on the whole title before "(Topics…)": a prefix match put the
    colour prices on "Colour Deposits and Lateness" too."""
    core = section_core(title)
    lines = [render_item(it) for it in items(config)
             if it["section"] and section_core(it["section"]) == core]
    lines += [kinds_line(add_name, rule) for it in items(config)
              for add_name, rule in it["add_ons"].items()
              if rule["kinds"] and section_core(rule["kinds_section"]) == core]
    return " ".join(lines)


def kinds_line(add_name, rule):
    """ "Toppings: meats: pepperoni, sausage; vegetables: mushroom, …." The
    menu's kinds are the only list (the Toppings prose doesn't repeat it)."""
    groups = [(f"{g.lower()}: " if g else "") + ", ".join(names)
              for g, names in rule["kinds"].items() if names]
    title = add_name if add_name.endswith("s") else add_name + "s"
    return f"{title.capitalize()}: " + "; ".join(groups) + "."


def section_lines_for(title, config):
    """Everything generated under a section: its price lines (the menu) and
    its notice line (booking.notice, policy.notice_line_for). Both tables
    are the only copy of their numbers, so the prose doesn't state them.
    (Opening hours aren't here: every prompt states them, from the weekly
    table, and a line in the index would go stale the moment an owner saved
    new hours, until the next Publish; journey_eval, 2026-10-23.)"""
    # Notice first: it reads on from the section's own text, which explains
    # it (Crosstown: "past the order-by time, pizzas … can still be ordered").
    # After the prices, it followed a list of trays, and 2 runs in 9 offered
    # those trays for tonight (journey "Friday at five", 2026-10-20; 6/6 with
    # the old prose).
    import policy
    return " ".join(part for part in (policy.notice_line_for(title, config),
                                      price_lines_for(title, config)) if part)


def _has_generated(config):
    return bool(items(config)) or bool(((config or {}).get("booking") or {}).get("notice"))


def attach_prices_to_markdown(text, config):
    """A seed document with each section's price lines added at the end of
    the section, BEFORE it's cut into chunks (boot ingestion).

    Attaching after chunking (first version, 2026-10-10) put the price line
    on every piece of a section that chunk_text had split, straight after a
    sentence cut in half ("…stops taking orders. Trays / Party tray prices:
    …"), and Friday at five started failing. Added to the section first, it
    lands once, at the section's end, wherever the cut falls. The notice
    line goes with it (section_lines_for)."""
    if not _has_generated(config):
        return text
    blocks = re.split(r"(?=^## )", text, flags=re.M)
    out = []
    for block in blocks:
        if block.startswith("## "):
            lines = section_lines_for(block.splitlines()[0][3:], config)
            if lines:
                block = block.rstrip() + "\n" + lines + "\n\n"
        out.append(block)
    return "".join(out)


def attach_prices(chunks, config):
    """Each section chunk with its menu items' price lines added: for
    sections from the database, which are one chunk each (Publish, the
    owner's preview). Boot cuts the seed into chunks, so it adds the lines
    before cutting (attach_prices_to_markdown). The notice line goes with
    it (section_lines_for)."""
    if not _has_generated(config):
        return list(chunks)
    out = []
    for chunk in chunks:
        lines = section_lines_for(_title(chunk), config)
        out.append(f"{chunk.rstrip()}\n{lines}" if lines else chunk)
    return out


# ---------------------------------------------------------------------------
# Matching what the customer said
# ---------------------------------------------------------------------------

def _words(text):
    text = (text or "").lower().replace("-", " ").replace('"', " inch ")
    text = text.replace("'", "")
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


def _counts(words):
    return [int(float(w)) for w in words if re.fullmatch(r"\d+(?:\.\d+)?", w)]


def find_item(name, config):
    """The item the words name: an exact item name first, then the item
    whose name or alias matches the most words ("mini cupcakes" is the mini
    cupcake, not the standard one)."""
    menu_items = items(config)
    for it in menu_items:
        if it["item"].lower() == (name or "").strip().lower():
            return it
    wanted = set(_words(name))
    best, best_size = None, 0
    for it in menu_items:
        for n in [it["item"], *it["aliases"]]:
            own = set(_words(n))
            if own and own <= wanted and len(own) > best_size:
                best, best_size = it, len(own)
    return best


def _item_words(it):
    words = set()
    for n in [it["item"], *it["aliases"]]:
        words |= set(_words(n))
    return words


def match_option(it, asked):
    """(option name or None, reason). The customer's words must agree with
    the option on every number, and every other word must be in the option's
    name. One option for any variety ("any sub") matches unless a number
    disagrees."""
    words = [w for w in _words(asked) if w not in _FILLER and w not in _item_words(it)]
    numbers, others = _numbers(words), [w for w in words if w not in _numbers(words)]
    options = list(it["options"])
    if not options:
        return None, "not listed"
    if not words:
        return (options[0], "only option") if len(options) == 1 else (None, "which")
    hits = []
    for option in options:
        own = set(_words(option))
        if numbers and not all(n in own or _in_range(n, option) for n in numbers):
            continue
        if others and not set(others) <= own and len(options) > 1:
            continue
        hits.append(option)
    if len(hits) == 1:
        return hits[0], "matched"
    if len(hits) > 1:
        return None, "which"
    return None, "not listed"


def _in_range(number, option):
    """"10" is within "for 8-12 people"."""
    try:
        n = float(number)
    except ValueError:
        return False
    return any(float(a) <= n <= float(b)
               for a, b in re.findall(r"(\d+)\s*[-–]\s*(\d+)", option))


def _sized_options(it):
    """{option: (count, price)} when every option is a fixed count ("10
    wings"), else None."""
    sized = {}
    for option, price in it["options"].items():
        nums = [n for n in _counts(_words(option))]
        if len(nums) != 1 or not is_fixed(price):
            return None
        sized[option] = (nums[0], price["low"])
    return sized


def _cheapest_combination(it, count):
    """For options that are counts ("10 wings"): the cheapest way to make
    exactly `count` from them, as (cost, [(option, how many)]), or None."""
    sized = _sized_options(it)
    if not sized:
        return None
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
    sized = _sized_options(it) or {}
    bigger = [(size, option, price) for option, (size, price) in sized.items()
              if size > count]
    return min(bigger) if bigger else None


def _per_piece(it, count):
    """Price `count` of a by-the-piece item: (price, lines) or (None, problem,
    suggestions)."""
    each = it["each"]
    if it["minimum"] and count < it["minimum"]:
        return None, (f"{it['item']} has a minimum of {it['minimum']}"), [
            f"{it['minimum']} for {price_text(_scaled(each, it['minimum']))}"]
    if it["sold_by"] and count % it["sold_by"]:
        step = it["sold_by"]
        lower, upper = (count // step) * step, (count // step + 1) * step
        options = [f"{n} for {price_text(_scaled(each, n))}" for n in (lower, upper) if n]
        return None, (f"{it['item']} is sold only in multiples of {step}, so "
                      f"{count} isn't an option"), options
    # Packs ("a dozen for $35") are used wherever they're cheaper.
    best_cost, best_parts = None, None
    pack_sizes = sorted(it["packs"])
    def search(i, left, parts, cost):
        nonlocal best_cost, best_parts
        if i == len(pack_sizes):
            total = cost + (each["low"] * left if each["quote"] is None else 0)
            if best_cost is None or total < best_cost:
                best_cost, best_parts = total, parts + ([("each", left)] if left else [])
            return
        size = pack_sizes[i]
        for n in range(left // size + 1):
            search(i + 1, left - n * size,
                   parts + ([(size, n)] if n else []),
                   cost + n * it["packs"][size]["low"])
    if is_fixed(each) and all(is_fixed(p) for p in it["packs"].values()):
        search(0, count, [], 0.0)
        lines = []
        for part, n in best_parts:
            if part == "each":
                lines.append(f"{n} x {it['item']} at {price_text(each)} each")
            else:
                lines.append(f"{n} x {part} for {price_text(it['packs'][part])}"
                             if n > 1 else f"{part} for {price_text(it['packs'][part])}")
        return {"low": best_cost, "high": best_cost, "quote": None}, lines, []
    total = _scaled(each, count)
    return total, [f"{count} x {it['item']} at {price_text(each)} each"], []


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------

def menu_price(config, order):
    """Price what the customer asked for. `order` is a list of
    {"item", "asked_for", "quantity", "add_ons": [{"name", "count"}]}.

    Returns {"lines", "total"} when everything is on the menu, otherwise
    {"not_listed": [...], "listed": {...}, ...} saying what isn't, what is,
    and the ways that work (combinations, the next size up, the nearest
    allowed quantity). The rule comes first, as policy.py's results do."""
    lines, totals, not_listed, listed, suggestions, ask, notes = [], [], [], {}, [], [], []
    for entry in order or []:
        it = find_item(entry.get("item", ""), config)
        if it is None:
            not_listed.append(f"{entry.get('item')!r} isn't on the menu")
            listed["menu items"] = [i["item"] for i in items(config)]
            continue
        asked = str(entry.get("asked_for") or "")
        quantity = max(1, int(entry.get("quantity") or 1))

        # The size is matched without the toppings named in it: "large
        # pepperoni" is the large, with pepperoni.
        sized = asked
        for _, kind in kinds_in(asked, it):
            sized = re.sub(r"\b" + r"\s+".join(map(re.escape, _words(kind))) + r"\b", " ",
                           sized, flags=re.I)
        option, why = match_option(it, sized) if it["options"] else (None, "not listed")
        if option is None and it["each"] is not None:
            # By the piece: a number they said is how many, "dozen" is 12.
            said = _counts([w for w in _words(asked) if w not in _item_words(it)])
            count = (said[0] * (said[1] if len(said) > 1 else 1)) if said else 1
            count *= quantity if said else 1
            if not said:
                count = quantity
            if count in it["packs"] and quantity == 1 and not said:
                pass
            price, detail, ways = _per_piece(it, count)
            if price is None:
                not_listed.append(detail)
                listed[it["item"]] = _per_piece_text(it)
                suggestions.extend(ways)
                continue
            lines.extend(detail)
            if "each" in it["notes"]:
                notes.append(f"{it['item']}: {it['notes']['each']}")
            totals.append(price)
            continue

        if option is None and len([n for n in _counts(_words(asked))]) >= 2:
            # "two dozen" passed as asked_for: the smaller number is how many.
            nums = sorted(_counts(_words(asked)))
            trial = re.sub(r"\b%d\b" % nums[0], "", " ".join(_words(asked)))
            option2, why2 = match_option(it, trial)
            if option2:
                option, why, quantity = option2, why2, quantity * nums[0]

        if option is None:
            listed[it["item"]] = {o: price_text(p) for o, p in it["options"].items()}
            for add_name, rule in it["add_ons"].items():
                listed[f"{it['item']} extra: {add_name}"] = {
                    o: price_text(p) for o, p in rule["prices"].items()}
            if why == "which":
                ask.append(f"which {it['item']}: " + ", ".join(it["options"]))
                continue
            not_listed.append(f"{it['item']} doesn't come as {asked!r}; that "
                              f"isn't on the menu")
            nums = _counts(_words(asked))
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
        label = _label(it, option)
        line_parts = [_scaled(price, quantity)]
        lines.append(f"{quantity} x {label} at {price_text(price)}"
                     if quantity > 1 else f"{label}: {price_text(price)}")
        if option in it["notes"]:
            notes.append(f"{label}: {it['notes'][option]}")
        extras = list(entry.get("add_ons") or [])
        # A kind the customer named ("a large pepperoni") is counted even
        # when the model passed it only in asked_for: what they said is
        # read in code (2026-10-23).
        passed = {(f[0], f[2]) for f in (_find_add_on(it, str(a.get("name") or ""))
                                         for a in extras) if f}
        for add_name, kind in kinds_in(asked, it):
            if (add_name, kind) not in passed:
                extras.append({"name": kind, "count": 1})
        for add in extras:
            name = str(add.get("name") or "")
            count = max(1, int(add.get("count") or 1))
            spec = _find_add_on(it, name)
            if spec is None:
                not_listed.append(f"no price is listed for {name!r} on {it['item']}")
                for add_name, rule in it["add_ons"].items():
                    listed[f"{it['item']} extra: {add_name}"] = {
                        o: price_text(p) for o, p in rule["prices"].items()}
                    if rule["kinds"]:
                        listed[f"{it['item']} {add_name} kinds"] = kind_names(rule)
                continue
            add_name, rule, kind = spec
            if kind:
                add_name = f"{kind} ({add_name})"
            each = rule["prices"].get(option, rule["prices"].get("any"))
            if each is None:
                not_listed.append(f"{add_name} isn't available on the {option} "
                                  f"(no price is listed for it)")
                listed[f"{add_name} (for)"] = list(rule["prices"])
                continue
            n = count if rule["each"] else 1
            per = f" (on each of the {quantity})" if quantity > 1 else ""
            lines.append(f"{n} x {add_name} on the {option} at {price_text(each)}{per}"
                         if n > 1 else f"{add_name} on the {option}: {price_text(each)}{per}")
            line_parts.append(_scaled(each, n * quantity))
        totals.append(_total(line_parts))
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
        if notes:
            result["notes"] = notes
        return result
    total = _total(totals)
    result = {"lines": lines, "total": price_text(total)}
    if notes:
        result["notes"] = notes
    exact = all(is_fixed(t) for t in totals)
    result["say"] = ("These are the menu prices; the total is before any "
                     "delivery fee or tax."
                     + (" Something here has no set price: say how it's priced, "
                        "as written." if total["quote"] else
                        "" if exact else " Some are starting prices or ranges: "
                        "say so, and that the final price is confirmed when it's "
                        "quoted.")
                     + (" Use the notes: some change the price (for "
                        "example, make it free)." if notes else ""))
    return result


def _label(it, option):
    """"18-inch large pizza", but "10 wings", not "10 wings wings"."""
    return option if set(_words(option)) & _item_words(it) else f"{option} {it['item']}"


def _find_add_on(it, name):
    """(add-on, rule, kind or None) for the words: the add-on's own name, all
    of one inside the other ("toppings" / "topping"), or one of its kinds
    ("pepperoni" is a topping). Not a shared word: "extra cheese" shares
    "cheese" with "vegan cheese" and isn't it, and isn't "extra mozzarella"
    either. Before 2026-10-23 the menu had no kinds, so "pepperoni" was "no
    price is listed" and the model added the numbers itself."""
    wanted = set(_words(name)) - _FILLER
    for add_name, rule in it["add_ons"].items():
        own = set(_words(add_name)) - _FILLER
        if wanted and own and (wanted <= own or own <= wanted):
            return add_name, rule, None
    for add_name, rule in it["add_ons"].items():
        for kind in kind_names(rule):
            if wanted and wanted == set(_words(kind)) - _FILLER:
                return add_name, rule, kind
    return None


def kinds_in(text, it):
    """The add-on kinds the customer's words name, longest first so "extra
    mozzarella" isn't also "mozzarella": [(add-on, kind)]."""
    words = _words(text)
    found, taken = [], set()
    pairs = [(add_name, kind) for add_name, rule in it["add_ons"].items()
             for kind in kind_names(rule)]
    for add_name, kind in sorted(pairs, key=lambda p: -len(_words(p[1]))):
        k = _words(kind)
        for i in range(len(words) - len(k) + 1):
            span = set(range(i, i + len(k)))
            if words[i:i + len(k)] == k and not span & taken:
                found.append((add_name, kind))
                taken |= span
                break
    return found


def tool(config):
    names = [it["item"] for it in items(config)]
    listed = ["; ".join(f"{it['item']}: " + ", ".join(it["add_ons"])
                        for it in items(config) if it["add_ons"])]
    extras = ("extras, by the menu's own names" + (f" ({listed[0]})" if listed[0] else "")
              + "; anything else they ask for goes here too, in their words, and "
              "the tool says if it isn't listed")
    return {
        "name": "menu_price",
        "description": (
            "Look up menu prices and add them up. Pass what the customer asked "
            "for in their own words: don't translate it to a menu size first "
            "(\"30 wings\" stays \"30 wings\", \"18 cupcakes\" stays \"18\"). The "
            "tool matches it against the menu and says if it isn't listed."),
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
                                "words, e.g. \"large\", \"30\", \"dozen\", \"chicken\""},
                            "quantity": {"type": "integer", "description":
                                "how many of that (1 unless they said; two dozen "
                                "is asked_for \"dozen\", quantity 2)"},
                            "add_ons": {"type": "array", "description": extras,
                                        "items": {
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
    generic = _FILLER | {"tray", "salad", "sub", "square", "pan", "roll", "any",
                         "people", "head", "hair", "together", "standard", "style",
                         "wash", "small", "special", "half", "full", "inch"}
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
    "price. Say a starting price or range as one, and apply any conditions "
    "it returns. Delivery fees, deposits and minimum orders are in the "
    "excerpts, not the menu.")


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
    out["menu"] = copy.deepcopy(draft)
    import section_links
    return section_links.follow(out, business_id)   # a draft under an older title


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
        start = len(lines)
        if was is None:
            lines.append(f"new on the menu: {it['item']} ({render_item(it)})")
            continue
        if it["each"] != was["each"] and it["each"] is not None:
            lines.append(f"{it['item']} each: "
                         + (f"{price_text(was['each'])} → " if was["each"] else "new: ")
                         + price_text(it["each"]))
        for count, price in it["packs"].items():
            old = was["packs"].get(count)
            if old != price:
                lines.append(f"{it['item']}, {count} for: "
                             + (f"{price_text(old)} → " if old else "new: ")
                             + price_text(price))
        for option, price in it["options"].items():
            old = was["options"].get(option)
            if old is None:
                lines.append(f"new: {_label(it, option)} {price_text(price)}")
            elif old != price:
                lines.append(f"{_label(it, option)}: {price_text(old)} → {price_text(price)}")
        for name, spec in it["add_ons"].items():
            old_spec = was["add_ons"].get(name, {"prices": {}})
            for option, price in spec["prices"].items():
                old = old_spec["prices"].get(option)
                where = "any" if option == "any" else option
                if old is None:
                    lines.append(f"new: {name} on {where} {price_text(price)}")
                elif old != price:
                    lines.append(f"{name} on {where}: {price_text(old)} → {price_text(price)}")
        # What's gone. (Without these, removing something left the banner
        # saying only "knowledge-base changes".)
        for option in was["options"]:
            if option not in it["options"]:
                lines.append(f"removed: {_label(was, option)}")
        for name, spec in was["add_ons"].items():
            new_prices = it["add_ons"].get(name, {"prices": {}})["prices"]
            for option in spec["prices"]:
                if option not in new_prices:
                    lines.append(f"removed: {name} on {'any' if option == 'any' else option}")
        if was["each"] is not None and it["each"] is None:
            lines.append(f"{it['item']}: no longer sold by the piece")
        for count in was["packs"]:
            if count not in it["packs"]:
                lines.append(f"removed: {it['item']}, {count} for {price_text(was['packs'][count])}")
        if len(lines) == start and render_item(it) != render_item(was):
            lines.append(f"{it['item']}: notes or wording changed")
    names = {it["item"] for it in items(new_config)}
    for name in before:
        if name not in names:
            lines.append(f"removed from the menu: {name}")
    return lines

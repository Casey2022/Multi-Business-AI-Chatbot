"""assistant_actions.py — the menu changes the owner assistant can make.

Added 2026-10-12 (owner assistant, stage 2). The rule: the assistant only
touches what goes through Publish. A menu change it makes is a draft, the
same draft the Menu page saves, listed in the "Unpublished changes" banner
for the owner to publish or discard. Settings take effect on save, so it
doesn't touch them; it tells the owner where they are instead.

Every change goes through the menu form's own code: the stored item
becomes the rows the Menu page shows (menu_form.item_rows), the tool
changes a row the way an owner edits a box, and the rows are posted
through menu_form.parse_item_form. So a price the assistant types is
read exactly like a typed one ("from 110", "20 to 60"; "4O" is refused),
and anything the form refuses, it refuses, in the same words.

Names are matched in code, never guessed: an item or option that isn't on
the menu comes back as an error listing what is, and the model asks the
owner or tries the real name.
"""

import copy
import logging

log = logging.getLogger("assistant")

# Per question. "Put every pizza up a dollar" is a handful; a hundred calls
# is a model going round in circles, or text it shouldn't have obeyed.
MAX_CHANGES = 12

_ITEM = {"type": "string", "description": "The menu item's name exactly as listed in the setup (e.g. “pizza”)."}
_PRICE = {"type": "string", "description": ("The price as the owner would write it: “24”, "
                                            "“3.50”, “from 110” (a starting price), “20 to 60” "
                                            "(a range), or words like “quoted after a trial”.")}

TOOLS = [
    {"name": "change_menu_price",
     "description": "Change the price of one option of a menu item (or its price each, for an item sold by the piece).",
     "input_schema": {"type": "object", "properties": {
         "item": _ITEM,
         "option": {"type": "string", "description": "The option's name exactly as listed (e.g. “18-inch large”), or “each” for the price each."},
         "price": _PRICE},
         "required": ["item", "option", "price"]}},
    {"name": "add_menu_option",
     "description": "Add a new option (size, version) with its price to an existing menu item.",
     "input_schema": {"type": "object", "properties": {
         "item": _ITEM,
         "option": {"type": "string", "description": "The new option's name."},
         "price": _PRICE,
         "note": {"type": "string", "description": "Optional: a short note said with the price."}},
         "required": ["item", "option", "price"]}},
    {"name": "remove_menu_option",
     "description": "Remove one option from a menu item (its extras' prices for that option go too).",
     "input_schema": {"type": "object", "properties": {
         "item": _ITEM,
         "option": {"type": "string", "description": "The option's name exactly as listed."}},
         "required": ["item", "option"]}},
    {"name": "set_extra_price",
     "description": ("Set the price of an extra (topping, add-on) on a menu item, adding the "
                     "extra if it isn't there yet."),
     "input_schema": {"type": "object", "properties": {
         "item": _ITEM,
         "extra": {"type": "string", "description": "The extra's name (e.g. “topping”, “extra cheese”)."},
         "price": _PRICE,
         "on": {"type": "string", "description": "Which option it's priced for, exactly as listed, or “any” for every option. Default “any”."},
         "each": {"type": "boolean", "description": "True if the price is per piece (3 toppings = 3 × the price). Leave out to keep it as it is."}},
         "required": ["item", "extra", "price"]}},
    {"name": "add_menu_item",
     "description": "Add a new item to the menu with its options and prices.",
     "input_schema": {"type": "object", "properties": {
         "item": {"type": "string", "description": "The new item's name, as customers say it (e.g. “calzone”)."},
         "options": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
             "option": {"type": "string", "description": "e.g. “regular”, “large”, “standard”."},
             "price": _PRICE}, "required": ["option", "price"]}},
         "other_words": {"type": "string", "description": "Optional: other words customers use for it, comma-separated."},
         "section": {"type": "string", "description": "Optional: the knowledge-base section these prices go with, exactly as listed."}},
         "required": ["item", "options"]}},
    {"name": "remove_menu_item",
     "description": "Remove an item from the menu entirely.",
     "input_schema": {"type": "object", "properties": {"item": _ITEM}, "required": ["item"]}},
]
NAMES = {t["name"] for t in TOOLS}


class Refused(Exception):
    """A change that wasn't made, said for the model to pass on or fix."""


def offered(business, config):
    """The tools, when this business has a menu; otherwise none."""
    import menu
    if not business or business.get("id") is None:
        return []
    return TOOLS if menu.enabled(menu.with_draft(config or {}, business["id"])) else []


def _same(a, b):
    return " ".join(str(a or "").split()).lower() == " ".join(str(b or "").split()).lower()


def _find_item(menu_list, name):
    for i, e in enumerate(menu_list):
        if _same(e.get("item"), name):
            return i
    for i, e in enumerate(menu_list):
        if any(_same(a, name) for a in e.get("aliases") or []):
            return i
    listed = ", ".join(f"“{e.get('item')}”" for e in menu_list) or "none"
    raise Refused(f"There's no menu item called “{name}”. The items are: {listed}.")


def _find_option(rows, name):
    for r in rows["options"]:
        if r["name"] and _same(r["name"], name):
            return r
    listed = ", ".join(f"“{r['name']}”" for r in rows["options"] if r["name"]) or "none"
    raise Refused(f"“{rows['item']}” has no option called “{name}”. Its options are: {listed}.")


def _blank(rows_list, key="name"):
    for r in rows_list:
        if not r.get(key):
            return r
    raise Refused("That item has too many rows to add another here.")


def _sections(business_id):
    from db import get_documents
    cores = []
    for d in get_documents(business_id):
        core = d["title"].split("(")[0].strip()
        if core and core not in cores:
            cores.append(core)
    return cores


def _change(name, args, menu_list, business_id):
    """(new menu list, the item's name) for one tool call, or Refused."""
    import menu_form
    args = {k: (v.strip() if isinstance(v, str) else v) for k, v in (args or {}).items()}

    if name == "add_menu_item":
        if not args.get("item"):
            raise Refused("The new item needs a name.")
        rows = menu_form.item_rows({})
        rows["item"] = args["item"]
        rows["aliases"] = args.get("other_words") or ""
        section = args.get("section") or ""
        if section:
            cores = _sections(business_id)
            match = next((c for c in cores if _same(c, section)), None)
            if not match:
                raise Refused(f"There's no knowledge-base section called “{section}”. "
                              f"The sections are: {', '.join(cores) or 'none'}.")
            rows["section"] = match
        options = args.get("options") or []
        if len(options) > menu_form.MAX_ROWS:
            raise Refused("That's more options than one item can hold.")
        rows["options"] = [{"original": "", "name": str(o.get("option", "")),
                            "price": str(o.get("price", "")), "note": "", "order": i + 1}
                           for i, o in enumerate(options)]
        entry, errors = menu_form.parse_item_form(
            menu_form.rows_to_form(rows), [e.get("item", "") for e in menu_list])
        if errors:
            raise Refused("Not added: " + "; ".join(errors) + ".")
        return menu_list + [entry], entry["item"]

    index = _find_item(menu_list, args.get("item", ""))
    old = menu_list[index]
    if name == "remove_menu_item":
        return menu_list[:index] + menu_list[index + 1:], old.get("item")

    rows = menu_form.item_rows(old)
    if name == "change_menu_price":
        if _same(args.get("option"), "each") and rows["by_piece"]:
            rows["each"] = args.get("price", "")
        else:
            _find_option(rows, args.get("option", ""))["price"] = args.get("price", "")
    elif name == "add_menu_option":
        if any(r["name"] and _same(r["name"], args.get("option")) for r in rows["options"]):
            raise Refused(f"“{old.get('item')}” already has an option called "
                          f"“{args.get('option')}”. Use change_menu_price to change its price.")
        row = _blank(rows["options"])
        row.update(name=args.get("option", ""), price=args.get("price", ""),
                   note=args.get("note", "") or "")
    elif name == "remove_menu_option":
        _find_option(rows, args.get("option", ""))["remove"] = True
    elif name == "set_extra_price":
        on = args.get("on") or "any"
        if not _same(on, "any"):
            on = _find_option(rows, on)["name"]
        else:
            on = "any"
        same_extra = [r for r in rows["add_ons"] if r["name"] and _same(r["name"], args.get("extra"))]
        row = next((r for r in same_extra if r["on"] == on), None)
        if row is None and len(same_extra) == 1 and not args.get("on"):
            row = same_extra[0]          # one price, and no option named: that one
        if row is None:
            row = _blank(rows["add_ons"])
            row.update(name=args.get("extra", ""), on=on,
                       each=same_extra[0]["each"] if same_extra else False)
        row["price"] = args.get("price", "")
        if args.get("each") is not None:
            for r in same_extra + [row]:
                r["each"] = bool(args["each"])
    else:
        raise Refused(f"Unknown change “{name}”.")

    others = [e.get("item", "") for j, e in enumerate(menu_list) if j != index]
    entry, errors = menu_form.parse_item_form(menu_form.rows_to_form(rows), others)
    if errors:
        if name == "remove_menu_option" and any("at least one priced option" in e for e in errors):
            raise Refused(f"That's the only option “{old.get('item')}” has. To take it off "
                          f"the menu, remove the item instead.")
        raise Refused("Not changed: " + "; ".join(errors) + ".")
    new_list, stale = menu_form.replace_item(menu_list, index, entry, old.get("item"))
    if stale:
        raise Refused(stale)
    return new_list, entry["item"]


def run(name, args, business, config, user_email):
    """Make one change as a draft. Returns (text for the model, action or None).

    The action is what the Help page shows under the reply: the banner's own
    description of the change ("18-inch large pizza: $22 → $24")."""
    import menu
    if name not in NAMES:
        return f"Unknown tool {name}.", None
    business_id = business["id"]
    current = menu.with_draft(config, business_id)
    menu_list = copy.deepcopy(current.get("menu") or [])
    try:
        new_list, item = _change(name, args, menu_list, business_id)
    except Refused as e:
        return str(e), None
    lines = menu.differences({**current, "menu": menu_list}, {**current, "menu": new_list})
    if not lines:
        return "Nothing to change: the menu already says that.", None
    menu.save_draft(business_id, new_list, updated_by=f"{user_email} via assistant")
    log.info("Owner assistant changed the menu (business %s): %s", business_id, name)
    return ("Saved as a draft: " + "; ".join(lines) + ". Customers still get the published "
            "menu until the owner presses “Publish changes” (or “Discard menu changes” to "
            "undo it)."), {"tool": name, "item": item, "lines": lines}

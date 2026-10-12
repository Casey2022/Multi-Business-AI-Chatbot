#!/usr/bin/env python3
"""menu_form_test.py: the owner's menu editor.

Run it:  python3 menu_form_test.py

A price is one text box typed the way an owner writes it ("48", "from 110",
"20 to 60", "quoted after a trial"); every existing menu item must survive
a trip through the form unchanged; renaming an option carries its extras
and notes along; removing one takes its extras with it; mistakes come back
as plain sentences with the owner's typing kept; a stale page can't
overwrite a newer change; and saves are drafts until Publish. No server.
"""

import sys
import tempfile
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="menu_form_test_")) / "test.db"

import yaml

import booking_state_test as helpers

ROOT = Path(__file__).resolve().parent
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def to_form(rows):
    f = {"item": rows["item"], "aliases": rows["aliases"], "section": rows["section"],
         "each": rows["each"], "each_note": rows["each_note"],
         "sold_by": str(rows["sold_by"]), "minimum": str(rows["minimum"])}
    for i, r in enumerate(rows["options"]):
        for k in ("original", "name", "price", "note", "order"):
            f[f"opt{i}_{k}"] = str(r.get(k, ""))
    for i, r in enumerate(rows["add_ons"]):
        for k in ("name", "on", "price"):
            f[f"add{i}_{k}"] = r[k]
        if r["each"]:
            f[f"add{i}_each"] = "yes"
    for i, r in enumerate(rows["packs"]):
        f[f"pack{i}_count"] = str(r["count"])
        f[f"pack{i}_price"] = r["price"]
    return f


def main():
    helpers._load_scheduler()
    import menu
    import menu_form as mf
    db.init_db()

    heading("a price is typed the way it's written")
    cases = {"48": 48, "$3.50": 3.5, "1,500": 1500, "from 110": {"from": 110},
             "110+": {"from": 110}, "starts at $45": {"from": 45},
             "20 to 60": {"from": 20, "to": 60}, "$20-$60": {"from": 20, "to": 60},
             "quoted after a trial": {"quote": "quoted after a trial"}}
    for text, want in cases.items():
        check(f"{text!r} → {want}", mf.parse_price_text(text) == (want, None),
              mf.parse_price_text(text))
    check("a backwards range is a mistake", mf.parse_price_text("60 to 20")[1])
    check("a typo like '4O' is a mistake, not a quote", mf.parse_price_text("4O")[1])
    check("empty is no price, no mistake", mf.parse_price_text("") == (None, None))
    for v in (48, 3.5, {"from": 110}, {"from": 20, "to": 60}, {"quote": "after a trial"}):
        check(f"{v} shows as text that reads back the same",
              mf.parse_price_text(mf.price_to_text(v))[0] == v, mf.price_to_text(v))
    check("$3.50 shows as 3.50, not 3.5", mf.price_to_text(3.5) == "3.50")

    heading("every existing item survives the form unchanged")
    for slug in ("crosstown_pizza", "sunrise_bakery_and_cafe", "belmont_hair_studio"):
        cfg = yaml.safe_load((ROOT / "config" / f"{slug}.yaml").read_text())
        bad = []
        for e in cfg["menu"]:
            entry, errors = mf.parse_item_form(to_form(mf.item_rows(e)))
            same = (not errors and menu.render_item(menu.items({"menu": [e]})[0])
                    == menu.render_item(menu.items({"menu": [entry]})[0]))
            if not same:
                bad.append((e["item"], errors))
        check(f"{slug}: all {len(cfg['menu'])} items round-trip", not bad, bad)

    cx = yaml.safe_load((ROOT / "config" / "crosstown_pizza.yaml").read_text())
    pizza = cx["menu"][0]

    heading("editing")
    form = to_form(mf.item_rows(pizza))
    form["opt2_name"] = "18-inch XL"                     # rename the large
    form["opt2_price"] = "24"
    entry, errors = mf.parse_item_form(form)
    check("renaming an option keeps its extras' prices (topping on the XL)",
          not errors and entry["options"].get("18-inch XL") == 24
          and entry["add_ons"]["topping"]["prices"].get("18-inch XL") == 2.75
          and "18-inch large" not in entry["options"], (errors, entry.get("add_ons")))
    form = to_form(mf.item_rows(pizza))
    form["opt0_remove"] = "yes"                          # remove the personal
    entry, errors = mf.parse_item_form(form)
    check("removing an option takes its extras' prices with it (gluten-free was personal-only)",
          not errors and "10-inch personal" not in entry["options"]
          and "gluten-free crust" not in entry.get("add_ons", {})
          and "10-inch personal" not in entry["add_ons"]["topping"]["prices"], (errors, entry))
    form = to_form(mf.item_rows(pizza))
    form["opt4_name"], form["opt4_price"] = "12-inch medium-ish", "15"
    entry, errors = mf.parse_item_form(form)
    check("a blank row adds an option", not errors and entry["options"]["12-inch medium-ish"] == 15)
    form = to_form(mf.item_rows(pizza))
    form["opt4_name"], form["opt4_price"], form["opt4_note"] = "party size", "from 40", "call ahead"
    entry, errors = mf.parse_item_form(form)
    check("a starting price and a note on a new option",
          entry["options"]["party size"] == {"from": 40} and entry["notes"]["party size"] == "call ahead")

    heading("putting options in order")
    wings = cx["menu"][1]
    form = to_form(mf.item_rows(wings))
    form["opt3_name"], form["opt3_price"], form["opt3_order"] = "6 wings", "8", "0"
    entry, errors = mf.parse_item_form(form)
    check("a new option numbered 0 goes first (6 wings before 10)",
          not errors and list(entry["options"]) == ["6 wings", "10 wings", "20 wings", "40 wings"],
          (errors, list(entry.get("options", {}))))
    form = to_form(mf.item_rows(wings))
    form["opt0_order"], form["opt2_order"] = "3", "1"
    entry, errors = mf.parse_item_form(form)
    check("renumbering swaps them", list(entry["options"]) == ["40 wings", "20 wings", "10 wings"])
    form = to_form(mf.item_rows(wings))
    form["opt1_order"] = "x"
    entry, errors = mf.parse_item_form(form)
    check("a non-number order goes last, nothing lost",
          list(entry["options"]) == ["10 wings", "40 wings", "20 wings"] and not errors)
    check("the order is what the assistant reads too",
          menu.render_item(menu.items({"menu": [entry]})[0]).startswith(
              "Wings prices: 10 wings $12, 40 wings $40, 20 wings $22"))
    shown = mf.form_rows({"item": "w", "opt0_name": "10 wings", "opt0_price": "1",
                          "opt0_order": "1", "opt1_order": "2"})
    check("an untouched blank row with only its order number isn't re-shown as filled",
          [r["name"] for r in shown["options"]] == ["10 wings", "", ""], shown["options"])

    heading("mistakes come back as sentences")
    def errs(changes, base=pizza, others=()):
        f = to_form(mf.item_rows(base))
        f.update(changes)
        return mf.parse_item_form(f, others)[1]
    check("no name", any("name" in e for e in errs({"item": ""})))
    check("a name another item has", any("already an item" in e for e in errs({}, others=["Pizza"])))
    check("the same option twice", any("twice" in e for e in errs({"opt4_name": "14-inch medium", "opt4_price": "9"})))
    check("an option with no price", any("no price" in e for e in errs({"opt4_name": "huge", "opt4_price": ""})))
    check("a price with no option", any("no option name" in e for e in errs({"opt4_name": "", "opt4_price": "9"})))
    check("a bad price", any("isn't a price" in e for e in errs({"opt1_price": "17O"})))
    check("'only in multiples of' without a price each",
          any("price each" in e for e in errs({"sold_by": "12"})))
    check("nothing priced at all",
          any("at least one" in e for e in mf.parse_item_form({"item": "x"})[1]))
    kept = mf.form_rows({"item": "pizza", "opt0_name": "huge", "opt0_price": "17O", "each": ""})
    check("the owner's typing is kept to show again", kept["options"][0]["price"] == "17O")

    heading("a stale page can't overwrite a newer change")
    lst = [{"item": "a", "options": {"x": 1}}, {"item": "b", "options": {"y": 2}}]
    new, why = mf.replace_item(lst, 1, {"item": "b2", "options": {"y": 3}}, "b")
    check("the right item is replaced", why is None and new[1]["item"] == "b2" and lst[1]["item"] == "b")
    check("…but not if the page showed a different item there",
          mf.replace_item(lst, 1, {"item": "z"}, "a")[0] is None)
    check("deleting", mf.replace_item(lst, 0, None, "a")[0] == [lst[1]])

    heading("the page and its routes")
    routes = (ROOT / "admin" / "routes.py").read_text()
    for name in ("menu_page", "menu_item_save", "menu_item_add", "menu_item_delete", "menu_discard"):
        body = routes.split(f"def {name}(")[1][:600]
        check(f"{name} checks access", "require_business_access(business_id)" in body)
    check("saves go to the draft, not live", "menu.save_draft(" in routes.split("def _save_menu(")[1][:400])
    check("an edit names the item the page showed (stale guard)",
          routes.count('request.form.get("original", "")') >= 2)
    check("a failed save re-shows the owner's typing with a 400",
          "menu_form.form_rows(request.form)" in routes and "status=400" in routes)
    macros = (ROOT / "admin" / "templates" / "admin" / "_macros.html").read_text()
    page = (ROOT / "admin" / "templates" / "admin" / "menu.html").read_text()
    js = (ROOT / "static" / "unsaved-edits.js").read_text()
    check("Menu is in the nav", "'admin.menu_page'" in macros)
    dash = (ROOT / "admin" / "templates" / "admin" / "dashboard.html").read_text()
    appts = (ROOT / "admin" / "templates" / "admin" / "appointments.html").read_text()
    check("the Businesses list uses the shared places (it had its own copy, without Menu)",
          "ui.destination_links(b)" in dash and "admin.settings" not in dash)
    check("so does the appointments page (an owner's first page)",
          "ui.destinations(business, 'appointments')" in appts and "admin.settings" not in appts)
    check("one list of places feeds both", macros.count("PLACES") >= 3)
    check("the ↑↓ buttons start hidden (the Order numbers work without them)",
          'data-move="up" aria-label="Move up" hidden' in page)
    check("item forms track every field for Save and the leave warning",
          page.count('data-track-edits="any"') == 2 and 'trackAll(form)' in js)
    check("every form on the page has a CSRF token (the banner's are in the macro)",
          page.count("csrf_token()") == 4 and macros.count("csrf_token()") == 2)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""menu_page_test.py: the Menu page speaks the business's own language.

Run it:  python3 menu_page_test.py

Every business's Menu page said "Extras (toppings, add-ons)", "Sold by the
piece (cupcakes, muffins…)", and gave "e.g. pizza, haircut, cupcake" and
"pizzas, pie" as examples: a pizzeria's and a bakery's words, on a funeral
home's page too (Casey, 2026-11-04). The headings are generic now, the
examples come from the business's own menu (menu_form.examples), and the
add-ons fold away on an item that has none, as per-item prices already did.

No API calls: the page is rendered by Flask's test client on a demo clone.
"""

import logging
import os
import re
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.update(SECRET_KEY="y" * 64, COOKIE_SECURE="false",
                  ANTHROPIC_API_KEY="fake", VOYAGE_API_KEY="fake")
os.environ.pop("ADMIN_EMAIL", None)

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="menu_page_test_")) / "test.db"
import vector_boot
vector_boot.run_in_subprocess = lambda *a, **k: None

logging.disable(logging.CRITICAL)
import app as app_module
import menu_form

PASSED, FAILED = [], []

# Words that belong to one trade, wherever they'd appear on every page.
TRADE_WORDS = ["toppings, add-ons", "cupcakes, muffins", "pizza, haircut, cupcake",
               '"pizzas, pie"', "3 toppings"]


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n" + "-" * len(text))


def menu_of(slug):
    return yaml.safe_load((ROOT / "config" / f"{slug}.yaml").read_text()).get("menu") or []


def examples():
    heading("examples come from the business's own menu")
    got = menu_form.examples(menu_of("miller_funeral"))
    check("Miller: its first items", got["item"] == "e.g. cremation package, burial package, "
                                                   "crematory fee", got)
    check("Miller: the first item's other words",
          got["aliases"] == "e.g. cremation packages, cremation, cremations", got)
    got = menu_form.examples(menu_of("belmont_hair_studio"))
    check("Belmont: haircut first", got["item"].startswith("e.g. haircut, blowout"), got)
    got = menu_form.examples([])
    check("an empty menu: neutral words, no trade's",
          got == {"item": "e.g. the service or product you're pricing",
                  "aliases": "e.g. other names customers use for it"}, got)
    got = menu_form.examples([{"item": "consultation"}, {"item": "repair", "aliases": ["fix"]}])
    check("the other words come from the first item that has some",
          got["aliases"] == "e.g. fix", got)


def the_page():
    heading("the Menu page, rendered (Crosstown, a demo clone)")
    client = app_module.app.test_client()
    page = client.get("/demo").get_data(as_text=True)
    token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    client.post("/demo/start", data={"template": "crosstown_pizza", "_csrf_token": token})
    with client.session_transaction() as s:
        business_id = db.get_user_by_email(s["user_email"])["business_id"]
    page = client.get(f"/admin/business/{business_id}/menu").get_data(as_text=True)

    for words in TRADE_WORDS:
        check(f"no {words!r}", words not in page)
    check("the page and its tab are called Prices (2026-11-04)",
          "<title>Prices — " in page and 'aria-current="page">Prices<' in page
          and ">Menu<" not in page)
    check("the generic headings", "Add-ons and upgrades" in page
          and "Priced per item or in packs" in page)
    first = menu_of("crosstown_pizza")[0]["item"]
    check(f"the Item example is Crosstown's own ({first!r} first)",
          f'placeholder="e.g. {first}' in page)

    # One card per item, then the Add an item form: the add-ons <details>
    # is open where the item has an add-on, folded where it has none.
    cards = page.split('<details class="menu-item"')[1:]
    entries = menu_of("crosstown_pizza")
    folded = re.compile(r'<details class="field" >\s*<summary>Add-ons and upgrades')
    opened = re.compile(r'<details class="field" open>\s*<summary>Add-ons and upgrades')
    check("one card per menu item", len(cards) == len(entries), (len(cards), len(entries)))
    for card, entry in zip(cards, entries):
        has = bool(entry.get("add_ons"))
        check(f"{entry['item']}: add-ons {'open' if has else 'folded away'}",
              bool((opened if has else folded).search(card)))
    check("at least one item of each kind was checked",
          any(e.get("add_ons") for e in entries) and not all(e.get("add_ons") for e in entries))
    add_form = page.split('id="add-item"')[1]
    check("Add an item: add-ons folded away", folded.search(add_form) is not None)


def main():
    examples()
    the_page()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

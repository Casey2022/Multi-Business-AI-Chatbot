#!/usr/bin/env python3
"""menu_test.py: Crosstown's menu as data, the menu_price tool, menu changes
as drafts, and photo uploads that propose menu changes.

Run it:  python3 menu_test.py

Why (2026-10-08/10): every price SUM in rag_eval passed, but "30 wings" got
the 40-wing price and a Sicilian got the large's topping price on every run,
through two prompt rules. Both wrong answers used real prices on the wrong
thing. The menu is now data (config menu:), the only copy of those prices;
code matches what the customer asked for and says when it isn't listed.
No network, no model.
"""

import copy
import re
import sys
import tempfile
import types
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="menu_test_")) / "test.db"

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


def ask(cfg, item, asked, quantity=1, add_ons=()):
    import menu
    return menu.menu_price(cfg, [{"item": item, "asked_for": asked, "quantity": quantity,
                                  "add_ons": [{"name": n, "count": c} for n, c in add_ons]}])


def main():
    helpers._load_scheduler()
    import menu
    import policy
    import llm
    import rag
    db.init_db()

    cfg = yaml.safe_load((ROOT / "config" / "crosstown_pizza.yaml").read_text())

    heading("the menu as data")
    check("Crosstown's menu loads with no problems", menu.items(cfg) and menu.problems(cfg) == [],
          menu.problems(cfg))
    bad = copy.deepcopy(cfg)
    bad["menu"][0]["add_ons"]["topping"]["prices"]["XL"] = 4
    check("an add-on priced for an option that doesn't exist is a problem",
          any("XL" in p for p in menu.problems(bad)))
    check("a business with no menu has none", menu.items({"business": {}}) == [])
    text = menu.render_menu(cfg)
    check("the price lines say every price",
          all(x in text for x in ("18-inch large $22", "$2.75 each on an 18-inch large",
                                  "$3.25 each on a Sicilian", "10 wings $12", "40 wings $40",
                                  "antipasto salad $14", "sub platter $60")), text)
    check("an add-on for one size says it's only that size",
          "Gluten-free crust: $3 extra on a 10-inch personal only" in text)

    heading("one copy: the sections carry no menu prices, the chunks get them")
    seed = (ROOT / "documents" / "crosstown_pizza" / "services.md").read_text()
    for it in menu.items(cfg):
        block = re.search(r"^## " + re.escape(it["section"]) + r".*?(?=^## |\Z)", seed, re.M | re.S)
        check(f"seed section '{it['section']}' exists and has no dollar amounts",
              block and not re.search(r"\$\s?\d", block.group(0)), block.group(0)[:120] if block else None)
    chunks = ["## Pizza Sizes and Prices (Topics: pizza)\nGluten-free is 10-inch only.",
              "## Delivery (Topics: delivery)\nThe delivery fee is $3."]
    got = menu.attach_prices(chunks, cfg)
    check("the pizza section's chunk gets the pizza price lines",
          got[0].startswith(chunks[0]) and "Pizza prices:" in got[0] and "Wings prices" not in got[0])
    check("a section with no menu item is untouched", got[1] == chunks[1])
    check("no menu: chunks untouched", menu.attach_prices(chunks, {}) == chunks)
    rag_src = (ROOT / "rag.py").read_text()
    check("Publish attaches to whole sections, boot before cutting the seed",
          rag_src.count("menu.attach_prices(chunks, config)") == 1
          and rag_src.count("menu.attach_prices_to_markdown(") == 2)
    import rag as _rag
    cut = _rag.chunk_text(menu.attach_prices_to_markdown(seed, cfg))
    trays = [c for c in cut if "Party Trays" in c.splitlines()[0]]
    line = menu.price_lines_for("Party Trays (Topics: party tray)", cfg)
    check("a section cut in two carries its price line once, at its end",
          sum(c.count(line) for c in trays) == 1 and trays[-1].rstrip().endswith(line),
          trays)
    check("…and the trays-only sentence is never interrupted by it",
          all("Trays\n" + line not in c for c in trays))
    check("a menu edit changes the collection's fingerprint",
          "digest.update(menu.render_menu(config)" in rag_src)
    check("so does a change in how chunks are built (INDEX_FORMAT)",
          "digest.update(INDEX_FORMAT" in rag_src)
    check("rag_eval checks expected answers against the menu too",
          "menu.render_menu(loaded)" in (ROOT / "rag_eval.py").read_text())

    heading("menu_price: what the customer asked for, matched in code")
    r = ask(cfg, "wings", "30 wings")
    check("30 wings isn't on the menu", r.get("not_listed") and "total" not in r, r)
    check("…and the combination that makes 30 is offered, added up",
          any("10 wings + 20 wings makes 30 for $34" in x for x in r.get("options_that_work", [])), r)
    check("…and the next size up", any("40 wings for $40" in x for x in r.get("options_that_work", [])))
    check("the result says to say so first", "before anything else" in r.get("say", ""))
    check("Sicilian + 2 toppings is $32.50", ask(cfg, "pizza", "sicilian", add_ons=[("toppings", 2)])["total"] == "$32.50")
    check("large + 3 toppings is $30.25", ask(cfg, "pizza", "large", add_ons=[("topping", 3)])["total"] == "$30.25")
    check("'18 inch' is the large", ask(cfg, "pizza", "18 inch")["total"] == "$22")
    r = ask(cfg, "pizza", "medium", add_ons=[("gluten free crust", 1)])
    check("gluten-free on a medium isn't available", r.get("not_listed") and "total" not in r, r)
    check("gluten-free personal + 2 toppings is $17.50",
          ask(cfg, "pizza", "personal", add_ons=[("gluten-free crust", 1), ("topping", 2)])["total"] == "$17.50")
    check("two steak subs are $26 (one price for any sub)", ask(cfg, "sub", "steak", quantity=2)["total"] == "$26")
    check("a chicken salad is the one with grilled chicken", ask(cfg, "salad", "chicken")["total"] == "$13")
    check("'10 piece' wings are $12", ask(cfg, "wings", "10 piece")["total"] == "$12")
    check("a pizza with no size asks which", "ask" in ask(cfg, "pizza", ""))
    check("extra ranch on wings has no price", ask(cfg, "wings", "10", add_ons=[("extra ranch", 1)]).get("not_listed"))
    check("two larges with a topping each: $49.50",
          ask(cfg, "pizza", "large", quantity=2, add_ons=[("topping", 1)])["total"] == "$49.50")
    check("something not on the menu at all", menu.menu_price(cfg, [{"item": "calzone", "asked_for": "",
                                                                   "quantity": 1, "add_ons": []}]).get("not_listed"))
    check("policy.run_tool runs it",
          policy.run_tool("menu_price", {"order": [{"item": "wings", "asked_for": "20",
                                                    "quantity": 1, "add_ons": []}]}, cfg)["total"] == "$22")
    check("policy.run_tool refuses it for a business with no menu",
          "error" in policy.run_tool("menu_price", {"order": []}, {"business": {}}))
    tool = menu.tool(cfg)
    check("the tool's item list is the menu's", tool["input_schema"]["properties"]["order"]["items"]
          ["properties"]["item"]["enum"] == [i["item"] for i in menu.items(cfg)])
    check("the tool says to pass the customer's own words", "own words" in tool["description"])

    heading("when the tool is offered (and required)")
    for msg, want in [("how much for 30 wings", True), ("what does a sicilian with two toppings cost", True),
                      ("how much is a large", True), ("is there a delivery fee", False),
                      ("what time do you close", False)]:
        check(f"{msg!r}: {'offered' if want else 'not offered'}",
              bool(llm.asks_about_price(msg) and menu.mentions_item(msg, cfg)) == want)
    sent = []
    real = (llm._create, llm.retrieve)
    llm.retrieve = lambda *a, **k: []
    text = types.SimpleNamespace(type="text", text="Wings come in 10, 20 or 40.")
    def fake(**k):
        sent.append(k)
        return types.SimpleNamespace(content=[text], stop_reason="end_turn",
                                     usage=types.SimpleNamespace(input_tokens=0, output_tokens=0))
    llm._create = fake
    from config import load_config
    full = load_config("config/crosstown_pizza.yaml")
    try:
        llm.get_llm_reply("how much for 30 wings", [], full)
        llm.get_llm_reply("what time do you close on sunday", [], full)
        llm.get_llm_reply("how much is drain cleaning", [], load_config("config/bobs_plumbing.yaml"))
    finally:
        llm._create, llm.retrieve = real
    check("a menu price question gets the tool, required",
          any(t["name"] == "menu_price" for t in sent[0].get("tools", []))
          and sent[0].get("tool_choice") == {"type": "tool", "name": "menu_price"})
    check("…and the rule", menu.PROMPT_RULE in sent[0]["system"])
    def has_menu_tool(call):
        return any(t["name"] == "menu_price" for t in call.get("tools", []))
    check("anything else gets neither", not has_menu_tool(sent[1]) and menu.PROMPT_RULE not in sent[1]["system"])
    check("a business with no menu never gets it", not has_menu_tool(sent[2]))

    heading("menu changes are drafts until Publish")
    conn = db.get_connection()
    conn.execute("INSERT INTO businesses (id, name, slug, config_path) VALUES (7, 'Crosstown', "
                 "'crosstown_pizza', 'config/crosstown_pizza.yaml')")
    conn.commit(); conn.close()
    db.add_document_section(7, "Pizza Sizes and Prices", "Gluten-free is 10-inch only.")
    db.set_documents_clean(7)
    from config import load_config
    live = load_config("config/crosstown_pizza.yaml", 7)
    draft = copy.deepcopy(live["menu"])
    draft[0]["options"]["18-inch large"] = 25
    menu.save_draft(7, draft, updated_by="o@x.com")
    check("a saved draft doesn't change what customers get",
          load_config("config/crosstown_pizza.yaml", 7)["menu"][0]["options"]["18-inch large"] == 22)
    check("the preview's config has the draft",
          menu.with_draft(live, 7)["menu"][0]["options"]["18-inch large"] == 25)
    check("…and marks the knowledge base unpublished", db.get_business_by_id(7)["documents_dirty"] == 1)
    check("the banner line says what changed",
          menu.differences(live, menu.with_draft(live, 7)) == ["18-inch large pizza: $22 → $25"],
          menu.differences(live, menu.with_draft(live, 7)))
    check("Publish makes it live", menu.publish_draft(7)
          and load_config("config/crosstown_pizza.yaml", 7)["menu"][0]["options"]["18-inch large"] == 25
          and menu.draft_menu(7) is None)
    menu.save_draft(7, live["menu"])
    menu.discard_draft(7)
    check("Discard drops a draft", menu.draft_menu(7) is None)
    routes = (ROOT / "admin" / "routes.py").read_text()
    pub = routes.split("def knowledge_publish(")[1][:2500]
    check("Publish builds with the draft and makes it live only after the rebuild",
          "menu.with_draft(" in pub and pub.index("ingest_documents(config") < pub.index("menu.publish_draft("))
    check("Discard clears the menu draft", "menu.discard_draft(business_id)" in routes)
    prev = (ROOT / "preview.py").read_text()
    check("the preview's After answers and index use the draft menu",
          "menu.with_draft(config, business_id)" in prev.split("def run_preview")[1]
          and "menu.with_draft(config, business_id)" in prev.split("def changed_sections")[1].split("def ")[0])
    check("the owner's page shows each section's prices, read-only",
          "price_lines[s.id]" in (ROOT / "admin" / "templates" / "admin" / "knowledge.html").read_text())

    heading("photo uploads propose menu changes")
    import photo_import as pi
    live = load_config("config/crosstown_pizza.yaml", 7)
    live["menu"][0]["options"]["18-inch large"] = 22
    prompt = pi.proposal_prompt(live, "Large $25", db.get_documents(7))
    check("a menu business's prompt carries the menu, fenced, and says no prices in sections",
          "<menu>" in prompt and "Never put a price in a section's text" in prompt)
    check("a business with no menu gets the old prompt",
          "<menu>" not in pi.proposal_prompt({"business": {}}, "x", []))
    data = {"changes": [], "menu_changes": [
        {"item": "pizza", "option": "18-inch large", "add_on": None, "price": 25},
        {"item": "pizza", "option": "14-inch medium", "add_on": None, "price": 17},
        {"item": "calzone", "option": "calzone with ricotta & mozzarella", "add_on": None, "price": 14},
        {"item": "calzone", "option": "", "add_on": "filling", "price": "$2"},
        {"item": "wings", "option": "30 wings", "add_on": None, "price": 33},
        {"item": "pizza", "option": "large", "add_on": None, "price": -1},
    ]}
    mc = pi.clean_menu_changes(data, live)
    ops = [(c["op"], c["item"], c["option"], c["add_on"], c["price"]) for c in mc]
    check("a changed price is set_price with the old one",
          mc[0]["op"] == "set_price" and mc[0]["old_price"] == 22 and mc[0]["price"] == 25, ops)
    check("an unchanged price is dropped", all(c["option"] != "14-inch medium" for c in mc), ops)
    check("something new is new_item (option and add-on)",
          ("new_item", "calzone", "calzone with ricotta & mozzarella", None, 14) in ops
          and ("new_item", "calzone", "", "filling", 2.0) in ops, ops)
    check("a new size of an existing item is add_option", ("add_option", "wings", "30 wings", None, 33) in ops, ops)
    check("a nonsense price is dropped", all(c["price"] > 0 for c in mc))
    photo = "Large 18\" $25  Calzone $14  Add a filling $2"
    checked = [pi.check_menu_change(dict(c), photo) for c in mc]
    check("a price in the photo isn't flagged", not checked[0]["flagged"])
    check("a price the photo doesn't show is flagged",
          [c for c in checked if c["option"] == "30 wings"][0]["flagged"])
    new_menu = pi.apply_menu_changes(live["menu"], mc[:3], ["Calzones (Topics: calzone)"])
    cal = [e for e in new_menu if e["item"] == "calzone"]
    check("applying sets the price, adds the new item with its section and add-on",
          new_menu[0]["options"]["18-inch large"] == 25 and cal
          and cal[0]["section"].startswith("Calzones") and cal[0]["add_ons"]["filling"]["prices"]["any"] == 2.0,
          cal)
    check("applying never touches the input", live["menu"][0]["options"]["18-inch large"] == 22)
    check("the result is a valid menu", menu.problems({"menu": new_menu}) == [], menu.problems({"menu": new_menu}))
    applied, stale = pi.apply_changes(7, mc[:1], {0: ("", "")}, updated_by="o@x.com (from a photo)",
                                      config=live)
    check("kept menu changes count as applied and go into the draft",
          applied == 1 and menu.draft_menu(7) is not None, (applied, stale))
    check("the draft holds the photo's price", menu.draft_menu(7)[0]["options"]["18-inch large"] == 25)
    review = (ROOT / "admin" / "templates" / "admin" / "knowledge_upload.html").read_text()
    check("the review page shows menu changes as old → new prices",
          'c.kind == "menu"' in review and "old_price_text" in review)

    heading("the bakery and the salon (2026-10-11)")
    sun = yaml.safe_load((ROOT / "config" / "sunrise_bakery_and_cafe.yaml").read_text())
    bel = yaml.safe_load((ROOT / "config" / "belmont_hair_studio.yaml").read_text())
    check("both menus load with no problems", menu.items(sun) and menu.items(bel)
          and menu.problems(sun) == [] and menu.problems(bel) == [],
          (menu.problems(sun), menu.problems(bel)))
    for slug, c in (("sunrise_bakery_and_cafe", sun), ("belmont_hair_studio", bel)):
        text = (ROOT / "documents" / slug / "services.md").read_text()
        for it in menu.items(c):
            block = re.search(r"^## " + re.escape(it["section"]) + r"\b.*?(?=^## |\Z)",
                              text, re.M | re.S)
            own = {menu.money(p["low"]) for p in list(it["options"].values())
                   + list(it["packs"].values()) + ([it["each"]] if it["each"] else [])
                   if p["low"] is not None}
            # Its own menu prices, that is: "delivery is free over $50" is a
            # policy, not a menu price, and stays in the section.
            check(f"{slug}: '{it['section']}' exists and doesn't repeat its menu prices",
                  block and not any(re.search(re.escape(m) + r"(?!\d)", block.group(0)) for m in own),
                  block.group(0)[:100] if block else "missing")
    check("prices join a section by its whole title: colour's aren't on 'Colour Deposits'",
          menu.price_lines_for("Colour Deposits and Lateness (Topics: deposit)", bel) == ""
          and "root touch-up $85" in menu.price_lines_for("Colour (Topics: colour)", bel))
    def one(c, item, asked, n=1, adds=()):
        return menu.menu_price(c, [{"item": item, "asked_for": asked, "quantity": n,
                                    "add_ons": [{"name": a, "count": k} for a, k in adds]}])
    check("a starting price stays one ('from $185')", one(bel, "balayage", "")["total"] == "from $185")
    r = one(sun, "birthday cake", "12 inch", adds=[("custom design", 1)])
    check("12-inch + a custom design is a range, said as one", r["total"] == "$140 to $180"
          and "ranges" in r["say"], r)
    check("an 8-inch isn't on the menu (only 6- and 12-inch are priced)",
          one(sun, "birthday cake", "8 inch").get("not_listed"))
    r = one(sun, "standard cupcake", "18")
    check("18 standard cupcakes: sold only by the dozen, 12 or 24 offered",
          r.get("not_listed") and r.get("options_that_work") == ["12 for $42", "24 for $84"], r)
    check("6 minis: under the minimum of 12", one(sun, "mini cupcake", "6").get("not_listed"))
    check("two dozen muffins use the dozen price: $70", one(sun, "muffin", "two dozen")["total"] == "$70"
          and one(sun, "muffin", "dozen", 2)["total"] == "$70")
    check("18 muffins: a dozen plus six singles, $54.50", one(sun, "muffin", "18")["total"] == "$54.50")
    check("a tray for 10 people is the 8-12 one (a number inside a range)",
          one(sun, "pastry tray", "10 people")["total"] == "$65")
    r = one(bel, "haircut", "fringe")
    check("a fringe trim carries its 'free within four weeks' note",
          r["total"] == "$10" and any("free within four weeks" in n for n in r.get("notes", [])), r)
    r = one(bel, "blowout", "bridal")
    check("bridal hair is 'quoted after a trial', no figure", r["total"] == "quoted after a trial"
          and "no set price" in r["say"], r)
    r = one(bel, "colour", "")
    check("'which colour?' lists the long-hair extra too",
          "colour extra: longer or very thick hair" in r.get("listed", {}), r)
    check("'extra cheese' isn't vegan cheese (no matching on a shared word)",
          one(cfg, "pizza", "large", adds=[("extra cheese", 1)]).get("not_listed"))
    check("'I'm about half grey' doesn't force the tool (the grey rule is prose)",
          not menu.mentions_item("I'm about half grey, what would it cost to cover it", bel))
    check("the tool lists each item's extras by name",
          "birthday cake: custom design, theme or character" in
          menu.tool(sun)["input_schema"]["properties"]["order"]["items"]["properties"]["add_ons"]["description"])

    heading("the menu switch (Settings: 'Prices on a menu?')")
    off = copy.deepcopy(cfg)
    off["menu_enabled"] = False
    check("switched off, nothing quotes from the menu",
          menu.items(off) == [] and menu.render_menu(off) == ""
          and not menu.mentions_item("how much is a large pizza", off), menu.render_menu(off))
    check("switched off, no price lines reach the documents",
          menu.attach_prices_to_markdown("## Pizza\nWe make pizza.", off)
          == "## Pizza\nWe make pizza.")
    check("switched off, has_items still sees the items (the Settings lock reads it)",
          menu.has_items(off) and not menu.enabled(off))
    unset = copy.deepcopy(cfg)
    unset.pop("menu_enabled", None)
    check("never set: on when there's a menu", menu.enabled(unset) and menu.items(unset))
    check("never set: off when there isn't", not menu.enabled({"business": {}}))
    check("switched on with no items yet: on, and still nothing to quote",
          menu.enabled({"menu_enabled": True}) and menu.items({"menu_enabled": True}) == [])
    # The Settings lock and the nav, run from routes.py's own source (no
    # Flask here) against this test database.
    import ast
    from config import load_config
    src = (ROOT / "admin" / "routes.py").read_text()
    wanted = {"menu_switched_on", "_menu_cannot_switch_off"}
    tree = ast.parse(src)
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    for n in funcs:
        n.decorator_list = []
    ns = {"load_config": load_config, "get_business_by_id": db.get_business_by_id}
    exec(compile(ast.Module(body=funcs, type_ignores=[]), "routes.py", "exec"), ns)
    bob = db.add_business("Bob's", "bobs_plumbing", "config/bobs_plumbing.yaml")
    cross = db.get_business_by_id(7)
    menu.discard_draft(7)
    check("Bob's: no Menu tab, and nothing stops the switch",
          not ns["menu_switched_on"](db.get_business_by_id(bob))
          and ns["_menu_cannot_switch_off"](db.get_business_by_id(bob)) is None)
    check("Crosstown: Menu tab, and the switch is locked while it has items",
          ns["menu_switched_on"](cross)
          and "can't be switched off" in (ns["_menu_cannot_switch_off"](cross) or ""))
    menu.save_draft(7, [], updated_by="o@x.com")
    check("an emptied draft isn't enough: the published menu still has items",
          ns["_menu_cannot_switch_off"](cross) is not None)
    menu.discard_draft(7)
    db.set_config_override(bob, "menu_enabled", True, updated_by="o@x.com")
    check("Bob's switched on: Menu tab, with nothing in it to quote yet",
          ns["menu_switched_on"](db.get_business_by_id(bob))
          and menu.items(load_config("config/bobs_plumbing.yaml", bob)) == [])
    menu.save_draft(bob, [{"item": "drain clearing", "options": {"standard": 120}}],
                    updated_by="o@x.com")
    check("…and once it has a draft item, it's locked on too",
          ns["_menu_cannot_switch_off"](db.get_business_by_id(bob)) is not None)
    routes_src = src
    check("every menu edit route sends a switched-off business to Settings",
          all(f"def {fn}(" in routes_src and
              "_menu_switched_off(business)" in routes_src.split(f"def {fn}(")[1].split("\n@")[0]
              for fn in ("menu_page", "menu_item_save", "menu_item_add", "menu_item_delete")))
    check("the settings save refuses switching off while locked",
          'if field == "menu_enabled" and not value:' in routes_src
          and "reason = _menu_cannot_switch_off(business)" in routes_src)

    for f in sorted((ROOT / "config").glob("*.yaml")):
        c = yaml.safe_load(f.read_text()) or {}
        if "business" not in c:
            continue
        check(f"{f.stem}: says whether it has a menu, and off means no items",
              "menu_enabled" in c and (c["menu_enabled"] or not menu.has_items(c)),
              c.get("menu_enabled"))

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

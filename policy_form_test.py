#!/usr/bin/env python3
"""policy_form_test.py — the owner's cancellation-rules form (policy_form.py).

Run it:  python3 policy_form_test.py

The rules decide what a customer is charged, and policy.py does the sums
from them, so the form must (1) give back exactly the rules it was shown
when nothing is edited, (2) turn an owner's edit into rules the sums use,
(3) refuse anything the sums can't use, in words, and (4) keep a change a
draft until Publish. Decisions (Casey, 2026-10-17): drafts through
Publish; owners edit windows, outcomes, deposit amounts and notes (kinds
and wording words stay the operator's); a business without rules can
create simple ones. No server, network or model.
"""

import copy
import sys
import tempfile
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="policy_form_test_")) / "test.db"

import yaml

ROOT = Path(__file__).resolve().parent
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def form_from(rows):
    """The form the page would post, unedited, for these rows: every card
    (a hidden one's fields are posted too, as a browser does), and a tick
    for each service card that's showing."""
    form = {}
    for c in rows["cards"]:
        k = c["key"]
        if c["deposit"]:
            form[f"{k}_dep_value"], form[f"{k}_dep_when"] = c["deposit"]["value"], c["deposit"]["when"]
        for i, w in enumerate(c["windows"]):
            form[f"{k}_w{i}_amount"], form[f"{k}_w{i}_unit"] = w["amount"], w["unit"]
            form[f"{k}_w{i}_type"], form[f"{k}_w{i}_pct"] = w["type"], w["percent"]
        form[f"{k}_last_type"], form[f"{k}_last_pct"] = c["last"]["type"], c["last"]["percent"]
        if c["service"] and c["shown"]:
            form[f"svc_{c['slug']}"] = "1"
    form["notes"] = rows["notes"]
    return form


def main():
    import policy
    import policy_form as pf
    from config import load_config
    db.init_db()

    heading("unedited, the form gives back exactly the rules it showed")
    for slug in ("belmont_hair_studio", "sunrise_bakery_and_cafe"):
        cfg = yaml.safe_load(open(ROOT / "config" / f"{slug}.yaml"))
        live = policy.cancellation(cfg)
        back, errors = pf.parse(form_from(pf.rows(live)), live)
        check(f"{slug}: same wording, same sums",
              not errors and policy.policy_text({"policies": {"cancellation": back}})
              == policy.policy_text(cfg), errors)
    belmont = yaml.safe_load(open(ROOT / "config" / "belmont_hair_studio.yaml"))
    sunrise = yaml.safe_load(open(ROOT / "config" / "sunrise_bakery_and_cafe.yaml"))
    b_rules, s_rules = policy.cancellation(belmont), policy.cancellation(sunrise)
    def amounts(rules, card=0):
        return [(w["amount"], w["unit"]) for w in pf.rows(rules)["cards"][card]["windows"]]
    check("24 and 48 hours are shown as hours, a week as 7 days",
          amounts(b_rules) == [("24", "hours")] and amounts(s_rules) == [("7", "days"), ("48", "hours")])

    heading("an edit becomes rules the sums use")
    form = form_from(pf.rows(b_rules))
    form.update(gen_w0_amount="48", gen_last_pct="75")
    new, errors = pf.parse(form, b_rules)
    edited = {"policies": {"cancellation": new}}
    outcome = policy.cancellation_outcome(edited, hours_before=30)["say"]
    check("Belmont, 48 hours and 75%: 30 hours' notice is now inside the window",
          not errors and "75%" in outcome and "48 hours" in policy.policy_text(edited), (errors, outcome))
    check("…and 50 hours' notice is free", "no charge" in policy.cancellation_outcome(edited, hours_before=50)["say"])
    check("the operator's words and kinds are copied through untouched",
          all(new[k] == b_rules[k] for k in ("title", "what", "action", "acting", "before", "price")))
    form = form_from(pf.rows(s_rules))
    form["k_custom_cake_w1_type"], form["k_wedding_cake_w1_type"] = "keep_percent", "keep_percent"
    form["k_custom_cake_w1_pct"] = form["k_wedding_cake_w1_pct"] = "40"
    new, errors = pf.parse(form, s_rules)
    middle = next(w for w in new["windows"] if w["at_least_hours"] == 48)
    check("the same outcome for every kind is stored once, as 'any'",
          not errors and middle == {"at_least_hours": 48, "any": {"keep_percent": 40}}, middle)
    form = form_from(pf.rows(s_rules)); form["k_custom_cake_dep_value"] = "60"
    new, _ = pf.parse(form, s_rules)
    check("a deposit amount can change; its kind can't",
          new["deposits"]["custom cake"]["percent"] == 60 and set(new["deposits"]) == set(s_rules["deposits"]))
    form = form_from(pf.rows(b_rules))
    form.update(gen_w1_amount="3", gen_w1_unit="days", gen_w1_type="no_charge",
                gen_w0_type="charge_percent", gen_w0_pct="25")
    new, errors = pf.parse(form, b_rules)
    check("a window can be added (3 days), and windows are sorted longest first",
          not errors and [w["at_least_hours"] for w in new["windows"]] == [72, 24, 0], (errors, new))
    form = form_from(pf.rows(b_rules)); form.update(gen_w1_amount="3", gen_w1_unit="days", gen_w1_type="no_charge")
    new, errors = pf.parse(form, b_rules)
    check("…but one that changes nothing (free from 3 days and from 24 hours) is left out",
          not errors and [w["at_least_hours"] for w in new["windows"]] == [24, 0])

    heading("anything the sums can't use is refused, in words")
    def errors_for(**changes):
        form = form_from(pf.rows(b_rules)); form.update(changes)
        return pf.parse(form, b_rules)[1]
    for changes, words, why in (
            ({"gen_w0_amount": ""}, "at least one window", "no window with notice"),
            ({"gen_w0_amount": "0"}, "more than 0", "zero notice"),
            ({"gen_w0_amount": "-5"}, "whole number", "a negative"),
            ({"gen_w0_amount": "two"}, "whole number", "words"),
            ({"gen_w0_amount": "400", "gen_w0_unit": "days"}, "more than a year", "over a year"),
            ({"gen_w1_amount": "24", "gen_w1_unit": "hours", "gen_w1_type": "no_charge"}, "same notice", "24 hours twice"),
            ({"gen_last_pct": "0"}, "between 1 and 100", "0%"),
            ({"gen_last_pct": "150"}, "between 1 and 100", "150%"),
            ({"gen_last_pct": "half"}, "between 1 and 100", "a word for a percentage"),
            ({"gen_last_type": "forfeit_deposit"}, "choose what happens", "a deposit outcome where there are no deposits"),
            ({"gen_w0_unit": "weeks"}, "whole number", "an unknown unit"),
            ({"notes": "\n".join(["a note"] * 6)}, "5 notes", "six notes"),
            ({"notes": "x" * 301}, "300 characters", "a long note")):
        errs = errors_for(**changes)
        check(f"refused: {why}", any(words in e for e in errs), errs)
    check("Belmont (no deposits) isn't offered deposit outcomes; Sunrise is",
          "forfeit_deposit" not in pf.outcomes_for(b_rules) and "forfeit_deposit" in pf.outcomes_for(s_rules))

    heading("different rules for some services: one card each (2026-10-17)")
    services = pf.service_names(belmont)
    check("the choices are the business's own services",
          services == ["haircut", "blowout", "colour", "cut and colour", "balayage", "kids cut"], services)
    check("…and 'Haircut (45 min)' style names are trimmed",
          pf.service_names({"services": ["Haircut (45 min)", "Colour (2 hours)"]}) == ["haircut", "colour"])
    page = pf.rows(b_rules, services)
    colour_card = next(c for c in page["cards"] if c["kind"] == "colour")
    check("every service has a card on the page, hidden until ticked, already a copy of everything else",
          [c["key"] for c in page["cards"]][:2] == ["gen", "k_haircut"] and len(page["cards"]) == 7
          and not colour_card["shown"] and colour_card["service"]
          and (colour_card["windows"], colour_card["last"]) == (page["cards"][0]["windows"], page["cards"][0]["last"]))
    check("everything else is 'Every appointment' alone, 'Every other appointment' beside a service",
          page["cards"][0]["title"] == "Every appointment"
          and page["cards"][0]["title_others"] == "Every other appointment")
    form = form_from(page); form["svc_colour"] = "1"
    step1, errors = pf.parse(form, b_rules, services)
    same = all(policy.cancellation_outcome({"policies": {"cancellation": step1}}, hours_before=h,
                                           kind=k)["outcomes"][k]
               == policy.cancellation_outcome(belmont, hours_before=h)["outcomes"]["appointment"]
               for h in (3, 30) for k in ("colour", "appointment"))
    check("ticking colour adds it, starting the same as everything else",
          not errors and step1["services"] == ["colour"] and same, step1)
    form = form_from(page); form.update(svc_colour="1", k_colour_w0_amount="48")
    step2, errors = pf.parse(form, b_rules, services)
    rules = {"policies": {"cancellation": step2}}
    colour30 = policy.cancellation_outcome(rules, hours_before=30, kind="colour")["say"]
    hair30 = policy.cancellation_outcome(rules, hours_before=30, kind="appointment")["say"]
    check("colour's card says 48 hours: 30 hours' notice costs 50%",
          not errors and "50%" in colour30 and "a colour appointment" in colour30, (errors, colour30))
    check("…while any other appointment is free at 30 hours, with no 'better notice' hint",
          "no charge" in hair30 and "With at least" not in hair30, hair30)
    check("the engine gets one shared list of windows (48, 24, less)",
          [w["at_least_hours"] for w in step2["windows"]] == [48, 24, 0], step2["windows"])
    cards = {c["kind"]: c for c in pf.rows(step2, services)["cards"]}
    check("…and the page splits it back: each card shows only its own windows",
          [w["amount"] for w in cards["colour"]["windows"]] == ["48"] and cards["colour"]["shown"]
          and [w["amount"] for w in cards["appointment"]["windows"]] == ["24"]
          and cards["appointment"]["title"] == "Every other appointment", cards)
    check("both are said in the rules customers are told",
          "a colour appointment, 50% of the service is charged; any other appointment, there is "
          "no charge" in policy.policy_text(rules), policy.policy_text(rules))
    tool = policy.cancellation_tool(rules)["input_schema"]["properties"]["kind"]
    check("the assistant's tool names the exact services, and everything else",
          tool["enum"] == ["colour", "appointment"] and "not one that includes it" in tool["description"])

    form = form_from(page)
    form.update(svc_colour="1", k_colour_w0_amount="3", k_colour_w0_unit="days",
                k_colour_w1_amount="48", k_colour_w1_type="charge_percent", k_colour_w1_pct="50",
                k_colour_last_type="charge_full_price",
                svc_kids_cut="1", k_kids_cut_w0_amount="12")
    mixed, errors = pf.parse(form, b_rules, services)
    sums = {"policies": {"cancellation": mixed}}
    def says(hours, kind):
        words = policy.cancellation_outcome(sums, hours_before=hours, kind=kind)["outcomes"][kind]
        return next((w for w in ("no charge", "50%", "full") if w in words), words)
    check("cards with different windows (colour 3 days/48h, kids cut 12h, the rest 24h) all hold",
          not errors
          and [says(h, "colour") for h in (80, 60, 30)] == ["no charge", "50%", "full"]
          and [says(h, "kids cut") for h in (30, 15, 5)] == ["no charge", "no charge", "50%"]
          and [says(h, "appointment") for h in (60, 30, 15)] == ["no charge", "no charge", "50%"],
          (errors, mixed))
    check("…and each card comes back exactly as the owner set it",
          all(pf.split(mixed, k) == own for k, own in (
              ("colour", ([(72, "no_charge"), (48, {"charge_percent": 50})], "charge_full_price")),
              ("kids cut", ([(12, "no_charge")], {"charge_percent": 50})),
              ("appointment", ([(24, "no_charge")], {"charge_percent": 50})))))

    form = form_from(page); form.update(k_haircut_w0_amount="two", k_haircut_last_type="nonsense")
    check("a hidden (unticked) card's fields are ignored", pf.parse(form, b_rules, services)[1] == [])
    form = form_from(page); form.update(svc_colour="1", k_colour_w0_amount="two")
    errs = pf.parse(form, b_rules, services)[1]
    check("a mistake is named by its card", any(e.startswith("Colour, window 1:") for e in errs), errs)
    form = {k: v for k, v in form_from(page).items() if not k.startswith("k_colour_")}
    form["svc_colour"] = "1"
    new, errors = pf.parse(form, b_rules, services)
    check("ticked with no card posted (an old page), it copies everything else",
          not errors and pf.split(new, "colour") == pf.split(b_rules, "appointment"))
    form = form_from(pf.rows(step2, services)); form.pop("svc_colour")
    step3, errors = pf.parse(form, step2, services)
    check("unticking it drops its rules", not errors and "services" not in step3
          and pf.kinds(step3) == ["appointment"] and step3["windows"] == b_rules["windows"], step3)
    renamed = pf.service_choices({"services": ["Haircut"]}, step2)
    check("a service renamed since it got rules is still offered, so it isn't dropped unseen",
          renamed == ["haircut", "colour"])
    many = services + ["perm", "toner"]
    form = form_from(pf.rows(b_rules, many))
    for name in many:
        form[f"svc_{pf.slug(name)}"] = "1"
    check(f"at most {pf.MAX_SERVICES} services", any("at most" in e for e in pf.parse(form, b_rules, many)[1]))

    heading("Sunrise: one card per cake, deposit and all")
    cakes = pf.rows(s_rules, pf.service_choices(sunrise, s_rules))
    check("a card per cake, each with its deposit, nothing to tick or remove",
          [(c["title"], c["deposit"]["value"], c["service"]) for c in cakes["cards"]]
          == [("Custom cake", "50", False), ("Wedding cake", "25", False)] and cakes["choices"] == [])
    form = form_from(cakes); form["svc_colour"] = "1"
    new, errors = pf.parse(form, s_rules, ["colour"])
    check("deposit kinds (Sunrise's cakes) stay the operator's: no services added",
          "services" not in new and pf.kinds(new) == pf.kinds(s_rules) and not pf.can_pick_services(s_rules))

    heading("a business with no rules can create simple ones")
    starter = pf.new_policy({"booking": {"noun": "visit"}})
    check("in its own word for a booking", "visit" in policy.policy_text({"policies": {"cancellation": starter}}))
    new, errors = pf.parse(form_from(pf.rows(starter)), starter)
    created = {"policies": {"cancellation": new}}
    check("and the sums work on what the form makes",
          not errors and "50%" in policy.cancellation_outcome(created, hours_before=3)["say"]
          and "no charge" in policy.cancellation_outcome(created, hours_before=30)["say"])
    sections = [{"title": "Estimates (Topics: quote)", "body": "Cancel any time before we start."},
                {"title": "Drain Cleaning", "body": "We clear drains."}]
    check("sections that already describe cancelling are named (a second copy)",
          pf.sections_about_cancelling(sections) == ["Estimates"])

    heading("a change is a draft until Publish")
    conn = db.get_connection()
    conn.execute("INSERT INTO businesses (id, name, slug, config_path) VALUES "
                 "(6, 'Belmont', 'belmont', 'config/belmont_hair_studio.yaml')")
    conn.commit(); conn.close()
    path = str(ROOT / "config" / "belmont_hair_studio.yaml")
    form = form_from(pf.rows(b_rules)); form.update(gen_w0_amount="48")
    new, _ = pf.parse(form, b_rules)
    pf.save_draft(6, new, updated_by="o@x.com")
    live = load_config(path, 6)
    check("customers still get the published rules", "24 hours" in policy.policy_text(live)
          and "48" not in policy.policy_text(live))
    check("…the preview and the banner see the draft",
          "48 hours" in policy.policy_text(pf.with_draft(live, 6))
          and pf.waiting_lines(live, 6) == ["cancellation rules: changed"]
          and db.get_business_by_id(6)["documents_dirty"] == 1)
    pf.discard_draft(6)
    check("Discard drops it", pf.draft(6) is None and pf.waiting_lines(load_config(path, 6), 6) == [])
    pf.save_draft(6, new); pf.publish_draft(6)
    check("Publish makes it the live rules", "48 hours" in policy.policy_text(load_config(path, 6))
          and pf.draft(6) is None)
    pf.save_draft(6, None)
    check("removing is a draft too", policy.cancellation(load_config(path, 6)) is not None
          and policy.cancellation(pf.with_draft(load_config(path, 6), 6)) is None
          and pf.waiting_lines(load_config(path, 6), 6) == ["cancellation rules: removed"])
    pf.publish_draft(6)
    check("…and once published, there are no rules (even though the YAML has them)",
          policy.cancellation(load_config(path, 6)) is None)

    heading("the wiring")
    routes = (ROOT / "admin" / "routes.py").read_text()
    page = routes.split("def cancellation_rules(")[1].split("\n@admin_bp")[0]
    publish = routes.split("def knowledge_publish(")[1].split("\n@admin_bp")[0]
    discard = routes.split("def knowledge_discard(")[1].split("\n@admin_bp")[0]
    check("the page checks business access and saves only drafts",
          "require_business_access(business_id)" in page[:300] and "policy_form.save_draft(" in page
          and "set_config_override" not in page and "publish_draft" not in page)
    check("Publish promotes the draft only after the rebuild worked",
          publish.index("ingest_documents(") < publish.index("policy_form.publish_draft(")
          < publish.index("set_documents_clean("))
    check("Discard drops it with everything else", "policy_form.discard_draft(business_id)" in discard)
    check("the banner lists it", "policy_form.waiting_lines(" in routes.split("def pending_changes(")[1][:1200])
    check("Preview answers uses it", "policy_form.with_draft(" in (ROOT / "preview.py").read_text())
    check("load_config applies the published one, never the draft",
          'elif field == "cancellation":' in (ROOT / "config.py").read_text()
          and "with_draft" not in (ROOT / "config.py").read_text()
          and "DRAFT_FIELD" not in (ROOT / "config.py").read_text())
    template = (ROOT / "admin" / "templates" / "admin" / "cancellation.html").read_text()
    check("every form on the page has a CSRF token", template.count("<form") == template.count("csrf_token()"))
    check("the percentage box is live only for a percentage outcome (and the server renders it so)",
          'data-enabled-when="{{ with_percent }}"' in template and "disabled{% endif %}" in template
          and 'wanted.split(" ")' in (ROOT / "static" / "field-deps.js").read_text())
    script = (ROOT / "static" / "rule-cards.js").read_text()
    check("a ticked service's card opens at once: rendered hidden, shown by rule-cards.js on the tick",
          "rule-cards.js" in (ROOT / "admin" / "templates" / "admin" / "base.html").read_text()
          and 'data-service-tick="k_{{ s.slug }}"' in template and "{% if not shown %}hidden{% endif %}" in template
          and 'tick.addEventListener("change"' in script and "card.hidden = false" in script)
    check("…copying everything else as it is now, the first time only",
          "data-fresh" in template and 'copy(general, card)' in script
          and 'card.removeAttribute("data-fresh")' in script)
    check("without the script, the page still works (save opens the card; all rows show)",
          "<noscript>" in template and 'data-add-window hidden' in template
          and 'data-remove-card="{{ c.key }}" hidden' in template)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

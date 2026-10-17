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
    """The form the page would post, unedited, for these rows."""
    form = {}
    for i, d in enumerate(rows["deposits"]):
        form[f"dep{i}_value"], form[f"dep{i}_when"] = d["value"], d["when"]
    for i, w in enumerate(rows["windows"]):
        form[f"w{i}_amount"], form[f"w{i}_unit"] = w["amount"], w["unit"]
        for j, o in enumerate(w["outcomes"]):
            form[f"w{i}_k{j}_type"], form[f"w{i}_k{j}_pct"] = o["type"], o["percent"]
    for j, o in enumerate(rows["last"]["outcomes"]):
        form[f"wlast_k{j}_type"], form[f"wlast_k{j}_pct"] = o["type"], o["percent"]
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
    check("24 and 48 hours are shown as hours, a week as 7 days",
          pf.rows(b_rules)["windows"][0][:2] if False else
          (pf.rows(b_rules)["windows"][0]["amount"], pf.rows(b_rules)["windows"][0]["unit"]) == ("24", "hours")
          and [(w["amount"], w["unit"]) for w in pf.rows(s_rules)["windows"]] == [("7", "days"), ("48", "hours")])

    heading("an edit becomes rules the sums use")
    form = form_from(pf.rows(b_rules))
    form.update(w0_amount="48", wlast_k0_pct="75")
    new, errors = pf.parse(form, b_rules)
    edited = {"policies": {"cancellation": new}}
    outcome = policy.cancellation_outcome(edited, hours_before=30)["say"]
    check("Belmont, 48 hours and 75%: 30 hours' notice is now inside the window",
          not errors and "75%" in outcome and "48 hours" in policy.policy_text(edited), (errors, outcome))
    check("…and 50 hours' notice is free", "no charge" in policy.cancellation_outcome(edited, hours_before=50)["say"])
    check("the operator's words and kinds are copied through untouched",
          all(new[k] == b_rules[k] for k in ("title", "what", "action", "acting", "before", "price")))
    form = form_from(pf.rows(s_rules))
    form["w1_k0_type"], form["w1_k1_type"] = "keep_percent", "keep_percent"
    form["w1_k0_pct"] = form["w1_k1_pct"] = "40"
    new, errors = pf.parse(form, s_rules)
    middle = next(w for w in new["windows"] if w["at_least_hours"] == 48)
    check("the same outcome for every kind is stored once, as 'any'",
          not errors and middle == {"at_least_hours": 48, "any": {"keep_percent": 40}}, middle)
    form = form_from(pf.rows(s_rules)); form["dep0_value"] = "60"
    new, _ = pf.parse(form, s_rules)
    check("a deposit amount can change; its kind can't",
          new["deposits"]["custom cake"]["percent"] == 60 and set(new["deposits"]) == set(s_rules["deposits"]))
    form = form_from(pf.rows(b_rules)); form.update(w1_amount="3", w1_unit="days", w1_k0_type="no_charge")
    new, errors = pf.parse(form, b_rules)
    check("a window can be added (3 days), and windows are sorted longest first",
          not errors and [w["at_least_hours"] for w in new["windows"]] == [72, 24, 0])

    heading("anything the sums can't use is refused, in words")
    def errors_for(**changes):
        form = form_from(pf.rows(b_rules)); form.update(changes)
        return pf.parse(form, b_rules)[1]
    for changes, words, why in (
            ({"w0_amount": ""}, "at least one window", "no window with notice"),
            ({"w0_amount": "0"}, "more than 0", "zero notice"),
            ({"w0_amount": "-5"}, "whole number", "a negative"),
            ({"w0_amount": "two"}, "whole number", "words"),
            ({"w0_amount": "400", "w0_unit": "days"}, "more than a year", "over a year"),
            ({"w1_amount": "1", "w1_unit": "days", "w1_k0_type": "no_charge"}, "same notice", "24 hours twice"),
            ({"wlast_k0_pct": "0"}, "between 1 and 100", "0%"),
            ({"wlast_k0_pct": "150"}, "between 1 and 100", "150%"),
            ({"wlast_k0_pct": "half"}, "between 1 and 100", "a word for a percentage"),
            ({"wlast_k0_type": "forfeit_deposit"}, "choose what happens", "a deposit outcome where there are no deposits"),
            ({"w0_unit": "weeks"}, "whole number", "an unknown unit"),
            ({"notes": "\n".join(["a note"] * 6)}, "5 notes", "six notes"),
            ({"notes": "x" * 301}, "300 characters", "a long note")):
        errs = errors_for(**changes)
        check(f"refused: {why}", any(words in e for e in errs), errs)
    check("Belmont (no deposits) isn't offered deposit outcomes; Sunrise is",
          "forfeit_deposit" not in pf.outcomes_for(b_rules) and "forfeit_deposit" in pf.outcomes_for(s_rules))

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
    form = form_from(pf.rows(b_rules)); form.update(w0_amount="48")
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

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

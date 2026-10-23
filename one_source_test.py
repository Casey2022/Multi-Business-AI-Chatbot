#!/usr/bin/env python3
"""one_source_test.py: every fact the bot states has one home, the knowledge base.

Run it:  python3 one_source_test.py

Until 2026-09-29 each business also had a `faq:` list in its YAML, pasted
into every system prompt. Twice it drifted from the document: gluten-free
"each day" against the document's Wednesdays and Saturdays, and a Sunrise
deposit rule that read one way in the FAQ and another in the document. The
prompt copy won both times, because it was always in view and the document
only was when retrieved. The FAQ was folded into the documents and removed.

This keeps it gone: no YAML carries one, the prompt ignores one if it
reappears, owners can't edit one, and the facts that only lived in an FAQ
are in the seed documents now. No server, no network, no model.
"""

import re
import sys
from pathlib import Path

import yaml

import booking_state_test as helpers    # stubs the integrations llm imports

ROOT = Path(__file__).resolve().parent
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def section(slug, heading_start):
    """One section of a seed document, heading included, or ''."""
    text = (ROOT / "documents" / slug / "services.md").read_text()
    for chunk in re.split(r"\n(?=## )", text):
        if chunk.startswith("## " + heading_start):
            return chunk
    return ""


# Facts that lived only in an FAQ, and the section that holds them now.
# The sections are where retrieval has to find them, so the heading matters
# as much as the sentence.
FOLDED = [
    ("bobs_plumbing", "Estimates, Payment", ["Estimates are free",
                                             "major credit cards",
                                             "licensed and insured in New York State"]),
    ("belmont_hair_studio", "Payment and Hours", ["walk-in", "walk-ins when a chair is free"]),
    ("ridgeline_contracting", "How Estimates Work", ["how soon", "season",
                                                     "start\nwindow"]),
]


def main():
    helpers._load_scheduler()
    import llm
    import config as config_module
    from config import load_config

    heading("no business config carries an FAQ")
    for path in sorted((ROOT / "config").glob("*.yaml")):
        if path.name == "personas.yaml":
            continue
        data = yaml.safe_load(path.read_text()) or {}
        check(f"{path.name}: no faq:", "faq" not in data)

    heading("an FAQ that comes back is ignored, not obeyed")
    cfg = load_config(str(ROOT / "config" / "sunrise_bakery_and_cafe.yaml"))
    plain = llm.build_system_prompt(cfg)
    planted = {**cfg, "faq": [{"question": "Do you sell unicorns?",
                               "answer": "Yes, UNICORN-7731 every Tuesday."}]}
    with_faq = llm.build_system_prompt(planted)
    check("the planted answer is not in the prompt", "UNICORN-7731" not in with_faq)
    check("the prompt is byte-identical with or without one", plain == with_faq)
    check("no 'Frequently asked questions' heading in the prompt",
          "frequently asked questions" not in plain.lower())

    heading("owners can't create a second copy")
    check("faq is not an editable field", "faq" not in config_module.EDITABLE_FIELDS)
    check("no editable field has the faq type",
          all(spec.get("type") != "faq"
              for spec in config_module.EDITABLE_FIELDS.values()))
    settings = (ROOT / "admin" / "templates" / "admin" / "settings.html").read_text()
    check("settings page has no FAQ textarea", 'name="faq"' not in settings)
    check("settings page points owners at the knowledge base",
          "url_for('admin.knowledge'" in settings)
    routes = (ROOT / "admin" / "routes.py").read_text()
    check("settings POST has no faq parser", '"faq"' not in routes)

    heading("startup says so if a faq: block turns up")
    app_src = (ROOT / "app.py").read_text()
    check("bootstrap warns on a leftover faq: block",
          'config.get("faq")' in app_src and "still has a faq: block" in app_src)

    heading("rules kept as data aren't also kept as prose")
    import policy
    for path in sorted((ROOT / "config").glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        if not policy.cancellation(data):
            continue
        doc_path = ROOT / "documents" / path.stem / "services.md"
        doc = doc_path.read_text().lower() if doc_path.exists() else ""
        check(f"{path.stem}: cancellation rules only in policies:, not the document",
              "## deposits and cancellation" not in doc and "forfeit" not in doc
              and "refunded" not in doc and "notice to cancel" not in doc
              and "is charged at" not in doc)

    heading("the facts that only lived in an FAQ are in the documents")
    for slug, head, needles in FOLDED:
        chunk = section(slug, head)
        check(f"{slug}: section '{head}' exists", bool(chunk))
        for needle in needles:
            check(f"{slug}: '{needle.replace(chr(10), ' ')}' in '{head}'",
                  needle.lower() in chunk.lower(), chunk[:200])

    # Notice (2026-10-20): booking.notice is the only copy of a service's
    # notice. Its section's text must not state the number too; the line the
    # bot reads is generated (policy.notice_line_for). A variant the table
    # doesn't hold yet (themed 72h, gluten-free 96h) may stay in the text.
    heading("notice numbers live only in booking.notice")
    import re
    import policy
    import menu
    for path in sorted((ROOT / "config").glob("*.yaml")):
        if path.name == "personas.yaml":
            continue
        cfg = load_config(str(path))
        slug = path.stem
        for service, entry in ((cfg.get("booking") or {}).get("notice") or {}).items():
            title = entry.get("section")
            check(f"{slug}: '{service}' names a section that exists",
                  bool(title and section(slug, title)), title)
            body = section(slug, title) if title else ""
            hours = float(entry["hours"])
            spoken = {f"{hours:g} hours", f"{hours / 24:g} days", f"{hours / 168:g} weeks"}
            spoken |= {"three hours"} if hours == 3 else set()
            said = [s for s in spoken if hours >= 1 and re.search(
                rf"\b{re.escape(s)}(?:'|’)? notice", " ".join(body.split()), re.I)]
            check(f"{slug}: '{title}' doesn't state {service}'s notice ({hours:g}h) in its text",
                  not said, said)
            check(f"{slug}: '{title}' gets the generated line, before any prices",
                  menu.section_lines_for(title, cfg).startswith(policy.notice_line_for(title, cfg))
                  and f"Notice for {service}" in menu.section_lines_for(title, cfg))
    import copy
    import rag
    sun = load_config(str(ROOT / "config" / "sunrise_bakery_and_cafe.yaml"))
    changed = copy.deepcopy(sun)
    changed["booking"]["notice"]["custom birthday cakes"]["hours"] = 96
    docs = ROOT / "documents" / "sunrise_bakery_and_cafe"
    check("a changed notice changes the boot digest (the collection is rebuilt)",
          rag._source_digest(docs, sun) != rag._source_digest(docs, changed))

    heading("an after-hours line lives only in business.emergency_phone")
    # 2026-10-22: the number lived in one knowledge-base paragraph, and the
    # emergency shortcut and the prompt used the main phone; with an owner's
    # main phone set to another number, an 11pm emergency went there.
    from config import substitute
    bob = load_config(str(ROOT / "config" / "bobs_plumbing.yaml"))
    line = bob["business"].get("emergency_phone")
    docs = " ".join((ROOT / "documents" / "bobs_plumbing").joinpath(f).read_text()
                    for f in ("services.md",))
    check("Bob's has one", line == "(585) 555-0123", line)
    check("…and the knowledge base doesn't state it", line not in docs)
    moved = copy.deepcopy(bob)
    moved["business"]["phone"] = "(585) 555-0124"
    rule = next(r for r in bob["rules"] if r["name"] == "emergency")
    check("the emergency shortcut gives the after-hours line, not the main phone",
          "555-0123" in substitute(rule["reply"], moved)
          and "555-0124" not in substitute(rule["reply"], moved))
    plain = copy.deepcopy(moved); plain["business"].pop("emergency_phone")
    check("…and a business without one gets its main phone, as before",
          substitute("{emergency_phone}", plain) == "(585) 555-0124")
    prompt = llm.build_system_prompt(moved, channel="webchat")
    check("the business facts state it beside the main phone",
          "- After-hours emergency line: (585) 555-0123" in prompt
          and "- Phone: (585) 555-0124" in prompt)
    check("…and only for a business that has one (every other prompt as it was)",
          "After-hours emergency line" not in llm.build_system_prompt(plain, channel="webchat"))
    import config as cfg_module
    check("owners set it on Settings",
          "business.emergency_phone" in cfg_module.EDITABLE_FIELDS
          and "'business.emergency_phone'" in (ROOT / "admin" / "templates" / "admin"
                                               / "settings.html").read_text())

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

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
              "## deposits" not in doc and "forfeit" not in doc
              and "refunded" not in doc)

    heading("the facts that only lived in an FAQ are in the documents")
    for slug, head, needles in FOLDED:
        chunk = section(slug, head)
        check(f"{slug}: section '{head}' exists", bool(chunk))
        for needle in needles:
            check(f"{slug}: '{needle.replace(chr(10), ' ')}' in '{head}'",
                  needle.lower() in chunk.lower(), chunk[:200])

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

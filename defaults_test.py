#!/usr/bin/env python3
"""defaults_test.py — what every business is told lives once, and says the same.

Run it:  python3 defaults_test.py            check
         python3 defaults_test.py --update   save the current guardrails as the
                                              reference (after a prompt change you
                                              meant, and measured)

Before 2026-10-23 every business YAML carried the channel rules word for
word and its own copy of the shared guardrails, which had drifted (24-54%
alike): a fix meant five edits. They now live in config/defaults.yaml,
each business naming the shared rules it uses and filling their blanks
(bot.words). The move had to leave every prompt exactly as it was, so this
compares each business's assembled guardrails with evals/guardrails.json,
taken from the YAMLs before the move (30 prompts, every business, channel
and booking state, were also compared byte for byte at the time).
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import yaml

from config import load_config, business_config_paths, SHARED_FILES

PASSED, FAILED = [], []
REFERENCE = ROOT / "evals" / "guardrails.json"


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def assembled():
    return {p.stem: load_config(str(p))["bot"]["guardrails"] for p in business_config_paths()}


def main():
    if "--update" in sys.argv:
        json.dump(assembled(), open(REFERENCE, "w"), indent=1, ensure_ascii=False)
        print(f"saved {REFERENCE}")
        return 0

    heading("each business is told exactly what it was")
    reference = json.load(open(REFERENCE))
    now = assembled()
    check("the same businesses", set(now) == set(reference), (set(now), set(reference)))
    for slug, parts in reference.items():
        for part, text in parts.items():
            got = (now.get(slug) or {}).get(part)
            check(f"{slug}: {part}", got == text,
                  next((f"first difference at {i}: {got[i:i+60]!r} vs {text[i:i+60]!r}"
                        for i in range(min(len(got or ''), len(text)))
                        if (got or '')[i] != text[i]), f"{len(got or '')} vs {len(text)} chars"))

    heading("said once")
    defaults = yaml.safe_load((ROOT / "config" / "defaults.yaml").read_text())
    for p in business_config_paths():
        raw = yaml.safe_load(p.read_text())
        guard = raw["bot"]["guardrails"]
        check(f"{p.stem}: no channel rules of its own (they're the defaults)",
              not any(c in guard for c in ("sms", "voice", "webchat")), list(guard))
        named = [i["rule"] for i in guard["universal"] if isinstance(i, dict)]
        check(f"{p.stem}: names the shared rules it uses",
              {"facts_only", "no_engaging"} <= set(named)
              and all(r in defaults["bot"]["guardrail_rules"] for r in named), named)
        prompt = load_config(str(p))["bot"]["guardrails"]["universal"]
        check(f"{p.stem}: every blank is filled",
              not any("{" + w + "}" in prompt for w in ("who", "offer", "never_invent")))

    heading("a business can still say its own")
    import config as config_module
    one = {"bot": {"words": {"who": "guest", "offer": "rooms", "never_invent": "rates."},
                   "guardrails": {"universal": [{"rule": "no_engaging"}, "Own line.\n"],
                                  "sms": "Short.\n"}}}
    config_module._with_defaults(one)
    g = one["bot"]["guardrails"]
    check("its own channel rule wins; the others come from the defaults",
          g["sms"] == "Short.\n" and g["webchat"] == defaults["bot"]["guardrails"]["webchat"])
    check("rules in its order, blanks filled, its {phone} left for later",
          g["universal"].startswith("If a guest says something")
          and "our actual rooms" in g["universal"] and g["universal"].endswith("Own line.\n"))
    two = {"bot": {"guardrails": {"universal": ["Call {phone}.\n", {"rule": "unsure"}]}}}
    config_module._with_defaults(two)
    check("{phone} in a business's own text is untouched",
          two["bot"]["guardrails"]["universal"].startswith("Call {phone}.\n"))
    old = {"bot": {"guardrails": "One block, the old way."}}
    config_module._with_defaults(old)
    check("a guardrails block from before (a single string) still loads as it is",
          old["bot"]["guardrails"] == "One block, the old way.")

    heading("shared files aren't businesses")
    names = {p.name for p in business_config_paths()}
    check("business_config_paths leaves out personas.yaml and defaults.yaml",
          not (names & SHARED_FILES) and len(names) == 5, names)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

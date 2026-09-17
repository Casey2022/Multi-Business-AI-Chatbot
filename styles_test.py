#!/usr/bin/env python3
"""styles_test.py — every class a template uses has a rule behind it.

Run it:  python3 styles_test.py

The same failure this codebase keeps producing, one layer up. A class name
with a typo in it is not an error: the browser shrugs, the element renders
unstyled, and the page looks *nearly* right — which is how it survives a
review. Nothing else catches that. The stylesheet parses fine, the template
renders fine, the tests pass, and one card on one page has no border.

So: read the class names out of every template, read the selectors out of
the stylesheet, and complain about the difference. No browser, no server.

It also runs the other direction, as a report rather than a failure: rules
nothing uses. Those aren't bugs — a component can legitimately be defined
before it's used — but a stylesheet quietly accumulating dead rules is how
the next person stops trusting it.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CSS = ROOT / "static" / "app.css"
TEMPLATE_DIRS = [ROOT / "admin" / "templates", ROOT / "templates"]

# Classes applied by JavaScript at runtime rather than written in a
# template. Listed explicitly so the check stays strict about everything
# else — an exception nobody wrote down is just a hole.
ADDED_BY_SCRIPT = {
    "typing",     # demo.html, on the "…" bubble while waiting
    "col-grip",   # table-resize.js, the draggable column edge
}

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n{detail}" if detail and not condition else ""))


def defined_classes():
    return set(re.findall(r"\.([a-z][a-z0-9-]*)", CSS.read_text(encoding="utf-8")))


def used_classes():
    """{class name: {templates it appears in}}, with Jinja stripped out."""
    found = {}
    for directory in TEMPLATE_DIRS:
        for path in sorted(directory.rglob("*.html")):
            text = path.read_text(encoding="utf-8")
            for attr in re.findall(r'class="([^"]*)"', text):
                # A class built from an expression — class="flash flash-{{ x }}"
                # — leaves a dangling prefix once the Jinja is removed. The
                # real names are in the stylesheet; the fragment isn't one.
                bare = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", attr)
                for name in bare.split():
                    if name.endswith("-") or not name:
                        continue
                    found.setdefault(name, set()).add(path.name)
    return found


def mentioned_in_expressions():
    """Class names chosen inside a Jinja expression, e.g.

        class="{{ 'is-today' if d == today }}"

    Only ever used to keep such a class OUT of the unused report — never to
    demand a rule for one. The same expression also contains literals that
    are values being compared, not classes ('error', 'user'), and telling
    those apart from the outside isn't possible. Wrong in the direction
    that costs nothing.
    """
    names = set()
    for directory in TEMPLATE_DIRS:
        for path in sorted(directory.rglob("*.html")):
            for attr in re.findall(r'class="([^"]*)"', path.read_text(encoding="utf-8")):
                for expression in re.findall(r"\{\{(.*?)\}\}", attr):
                    names.update(re.findall(r"['\"]([a-z][a-z0-9-]*)['\"]", expression))
    return names


def main():
    if not CSS.exists():
        print(f"No stylesheet at {CSS}")
        return 1

    defined = defined_classes() | ADDED_BY_SCRIPT
    used = used_classes()

    print(f"\nStylesheet: {CSS.relative_to(ROOT)}")
    print(f"  {len(defined)} selectors defined, {len(used)} classes used in templates\n")

    print("Every class a template uses is styled")
    print("-" * 37)
    missing = sorted(set(used) - defined)
    for name in missing:
        check(name, False, f"         used in {', '.join(sorted(used[name]))} — no rule defines it")
    check("no unstyled class names", not missing, f"         {len(missing)} missing")

    print("\nTokens are declared before anything reads them")
    print("-" * 46)
    css = CSS.read_text(encoding="utf-8")
    root_block = re.search(r":root\s*\{(.*?)\}", css, re.S)
    declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", root_block.group(1))) if root_block else set()
    referenced = set(re.findall(r"var\((--[a-z0-9-]+)", css))
    undeclared = sorted(referenced - declared)
    for token in undeclared:
        check(token, False, "         used with var() but never declared on :root")
    check("no undeclared custom properties", not undeclared)

    print("\nRules nothing uses (a report, not a failure)")
    print("-" * 44)
    # Only report component-looking classes: state and element selectors
    # legitimately have no template of their own.
    ignore = {"btn", "card", "chip", "field", "flash", "msg", "convo", "slot",
              "details", "verdict", "weekgrid", "facts", "prose", "toolbar"}
    unused = sorted(n for n in defined - set(used) - ADDED_BY_SCRIPT
                                  - mentioned_in_expressions()
                    if "-" in n and n.split("-")[0] not in ignore)
    for name in unused[:20]:
        print(f"  · .{name}")
    if not unused:
        print("  none")

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""routes_test.py — every url_for() names an endpoint that exists.

Run it:  python3 routes_test.py

Why this exists. A helper function was once inserted directly beneath the
@admin_bp.route("/login") decorator, which quietly made that helper the
login view. Flask was perfectly happy: GET /admin/login returned the
caller's IP address as the page body, and url_for("admin.login") raised
BuildError because no endpoint by that name existed any more. It survived a
commit, a push and a week of use, because a logged-in session never visits
the login page and the demo signs visitors in directly. The first person to
hit it was the first person to be logged out.

A BuildError can only be found by visiting the page that raises it, which
means no amount of unit testing reaches it. This does, statically: it reads
the route decorators to learn which endpoints exist, reads every url_for()
in the code and the templates to learn which are referenced, and complains
about the difference. No Flask import, no running server, no database.
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Which decorator prefixes register a route, and what each one prefixes the
# endpoint name with. Read from the source rather than assumed: the
# blueprint's name is its first argument.
APP_DECORATORS = {"app"}

# Endpoints Flask registers itself, with no route decorator in our source.
# Listed rather than ignored, so the check stays strict about everything
# else: "url_for names something real" is only useful if the exceptions are
# a short, named list.
BUILT_IN_ENDPOINTS = {
    "static",  # Flask serves static_folder at /static automatically
}

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n" + "-" * len(text))


def blueprint_names():
    """{variable name: blueprint name} for every Blueprint(...) in the tree."""
    found = {}
    for path in sorted(ROOT.rglob("*.py")):
        if "venv" in path.parts or path.name == Path(__file__).name:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            func = node.value.func
            if getattr(func, "id", None) != "Blueprint" or not node.value.args:
                continue
            first = node.value.args[0]
            if isinstance(first, ast.Constant) and node.targets:
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    found[target.id] = first.value
    return found


def registered_endpoints(blueprints):
    """{endpoint name: (file, function)} for every route decorator found.

    Uses the AST, so what's recorded is what Flask will actually register —
    the function the decorator is really attached to, not the one it looks
    like it belongs to.
    """
    endpoints = {}
    for path in sorted(ROOT.rglob("*.py")):
        if "venv" in path.parts or path.name == Path(__file__).name:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if not isinstance(target, ast.Attribute) or target.attr != "route":
                    continue
                owner = getattr(target.value, "id", None)
                if owner in blueprints:
                    name = f"{blueprints[owner]}.{node.name}"
                elif owner in APP_DECORATORS:
                    name = node.name
                else:
                    continue
                rule = None
                if isinstance(dec, ast.Call) and dec.args:
                    first = dec.args[0]
                    if isinstance(first, ast.Constant):
                        rule = first.value
                endpoints[name] = (path.relative_to(ROOT), node.name, rule)
    return endpoints


URL_FOR = re.compile(r"""url_for\(\s*['"]([A-Za-z_][\w.]*)['"]""")


def references():
    """{endpoint name: [where it's referenced]} across code and templates."""
    found = {}
    files = [p for p in sorted(ROOT.rglob("*.py")) if "venv" not in p.parts]
    files += [p for p in sorted(ROOT.rglob("*.html")) if "venv" not in p.parts]
    for path in files:
        if path.name == Path(__file__).name:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for endpoint in URL_FOR.findall(text):
            found.setdefault(endpoint, []).append(str(path.relative_to(ROOT)))
    return found


def main():
    blueprints = blueprint_names()
    endpoints  = registered_endpoints(blueprints)
    for name in BUILT_IN_ENDPOINTS:
        endpoints.setdefault(name, (Path("flask"), name, None))
    referenced = references()

    heading("What's registered")
    print(f"  blueprints: {', '.join(sorted(blueprints.values())) or 'none'}")
    print(f"  endpoints:  {len(endpoints)}")
    print(f"  url_for references: {len(referenced)} distinct")
    check("some routes were found at all", len(endpoints) > 5, f"{len(endpoints)}")

    heading("Every url_for names a real endpoint")
    for endpoint in sorted(referenced):
        where = ", ".join(sorted(set(referenced[endpoint])))
        check(f"{endpoint}", endpoint in endpoints,
              f"referenced in {where} — no route registers it. "
              f"Closest: {closest(endpoint, endpoints)}")

    heading("No route is attached to a private helper")
    # The shape of the original bug: a decorator that slid down onto the
    # function beneath it. A view named with a leading underscore is almost
    # always that accident rather than a deliberate choice.
    for name, (path, func, _rule) in sorted(endpoints.items()):
        check(f"{name} is a view, not a helper", not func.startswith("_"),
              f"{path}: @route is attached to {func}(), which looks like a "
              f"private helper — did the decorator slide off the view below?")

    heading("URLs we've promised to keep working")
    # Paths that exist in the README, on the deployed site, or in someone's
    # bookmarks. Renaming a route is cheap; silently breaking a link that's
    # already out in the world is not. /demo/<slug> in particular is the
    # original address of the chat page, kept as a redirect after /chat
    # took over the name.
    promised = {
        "/chat/<slug>":  "the chat page a customer sees",
        "/demo/<slug>":  "its original address, now a redirect",
        "/demo":         "the sandbox picker",
        "/webchat/<slug>": "the endpoint the chat page posts to",
        "/sms":          "the Twilio webhook",
    }
    rules = {rule for _n, (_p, _f, rule) in endpoints.items() if rule}
    for rule, why in sorted(promised.items()):
        check(f"{rule} — {why}", rule in rules,
              f"no route serves {rule} any more")

    heading("Scripts and the fields they govern are connected")
    base_src     = (ROOT / "admin" / "templates" / "admin" / "base.html").read_text(encoding="utf-8")
    settings_src = (ROOT / "admin" / "templates" / "admin" / "settings.html").read_text(encoding="utf-8")
    routes_src   = (ROOT / "admin" / "routes.py").read_text(encoding="utf-8")

    # A script nobody includes is the same bug as a route nobody registers.
    for script in ("table-resize.js", "field-deps.js"):
        check(f"{script} exists", (ROOT / "static" / script).exists())
        check(f"{script} is loaded by base.html",
              f"filename='{script}'" in base_src,
              "written, shipped, and never reaching a page")

    # data-enabled-by names another field by name. A typo there is silent:
    # the script finds nothing, returns, and the dependency simply never
    # happens -- no error, no console warning, a control that stays live
    # when it shouldn't.
    import re as _re
    for dependent in _re.finditer(r'data-enabled-by="([^"]+)"', settings_src):
        controller = dependent.group(1)
        check(f"data-enabled-by names a real field ({controller})",
              f'name="{controller}"' in settings_src,
              "no field by that name on this page, so the rule never fires")

    # The template asks a question only the route can answer.
    for name in ("address_check_ready",):
        check(f"settings.html reads {name}", name in settings_src)
        check(f"the settings route passes {name}",
              f"{name} =" in routes_src or f"{name}=" in routes_src,
              "the template would treat a missing value as false and "
              "quietly tell every owner their address checking is off")

    heading("Endpoints nothing links to")
    # Not a failure: /sms and /webchat are called by Twilio and by fetch(),
    # never by url_for. Printed because an unreferenced admin page is
    # usually a page someone can no longer reach.
    orphans = [e for e in sorted(endpoints) if e not in referenced]
    for e in orphans:
        print(f"  · {e}")

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


def closest(name, endpoints):
    import difflib
    match = difflib.get_close_matches(name, list(endpoints), n=1, cutoff=0.5)
    return match[0] if match else "nothing similar"


if __name__ == "__main__":
    sys.exit(main())

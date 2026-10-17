#!/usr/bin/env python3
"""version_test.py: /version says which commit is running, and nothing else.

Run it:  python3 version_test.py

Added 2026-10-17: a fix was pushed and a live test couldn't tell whether
Render had deployed it yet. Render sets RENDER_GIT_COMMIT on each deploy;
/version gives its short hash. The route runs from app.py's own source
against a stand-in environment (as chat_session_test does). No server,
network or model.
"""

import ast
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def main():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "version")
    decorators = [ast.unparse(d) for d in fn.decorator_list]
    check("it's a public GET route at /version",
          decorators == ["app.route('/version', methods=['GET'])"], decorators)
    fn.decorator_list = []
    env = {}
    namespace = {"re": re, "os": types.SimpleNamespace(environ=env)}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "app.py", "exec"), namespace)
    version = namespace["version"]

    env["RENDER_GIT_COMMIT"] = "7141863f0a9c2d4e5b6a7c8d9e0f1a2b3c4d5e6f"
    check("a deploy: the short hash", version() == {"commit": "7141863"}, version())
    env.clear()
    check("running locally (no variable): 'unknown'", version() == {"commit": "unknown"})
    env["RENDER_GIT_COMMIT"] = "<script>alert(1)</script>"
    check("anything that isn't a hash is never echoed back", version() == {"commit": "unknown"})
    body = ast.unparse(fn)
    check("it reads that one variable and nothing else about the server",
          body.count("os.environ") == 1 and "RENDER_GIT_COMMIT" in body)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

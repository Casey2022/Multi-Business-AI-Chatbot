#!/usr/bin/env python3
"""run_tests.py — every offline test file, with one progress bar.

    python3 run_tests.py             # every *_test.py that doesn't call the API
    python3 run_tests.py menu help   # only files whose name contains "menu" or "help"
    python3 run_tests.py -v          # print each file's full output too
    python3 run_tests.py --with-api  # include judge_test.py (a few cents)

Each file runs in its own process, as it would on its own: they each set
up a throwaway database and stub out modules, and sharing one interpreter
would let one file's stubs leak into the next. A file passes when it exits
0 and its last "N passed, M failed" line says M is 0.

Calls-the-API scripts are not tests in this sense and are never run from
here except judge_test.py with --with-api: rag_eval, journey_eval, qa_eval,
conversation_eval, upload_eval and assistant_eval have their own bars.
"""

import re
import subprocess
import sys
import time
from pathlib import Path

import progress
from progress import say

ROOT = Path(__file__).resolve().parent
API_TESTS = {"judge_test.py"}          # calls the model; costs money
SUMMARY = re.compile(r"(\d+) passed, (\d+) failed")
FAIL_LINE = re.compile(r"\[FAIL\]|FAILED|Traceback|Error:")


def chosen_files(args, with_api):
    files = sorted(p.name for p in ROOT.glob("*_test.py"))
    if args:
        return [f for f in files if any(a.lower() in f.lower() for a in args)]
    return [f for f in files if with_api or f not in API_TESTS]


def failure_lines(output):
    """The lines that say what failed: each [FAIL] line with the indented
    detail under it (the tests print "wanted … got …" there), or the end of
    the output when the file crashed before saying."""
    lines, keep = [], False
    for line in output.splitlines():
        if FAIL_LINE.search(line):
            lines.append(line.strip())
            keep = True
        elif keep and line.startswith("         ") and line.strip():
            lines.append("  " + line.strip())
        else:
            keep = False
    return lines or [l.rstrip() for l in output.strip().splitlines()[-15:]]


def run(name):
    """(ok, passed, failed, output) for one test file."""
    try:
        done = subprocess.run([sys.executable, name], cwd=ROOT, capture_output=True,
                              text=True, timeout=600)
        output = done.stdout + done.stderr
        code = done.returncode
    except subprocess.TimeoutExpired as e:
        output = (e.stdout or "") + (e.stderr or "") if isinstance(e.stdout, str) else ""
        return False, 0, 0, output + "\n(timed out after 10 minutes)"
    found = SUMMARY.findall(output)
    passed, failed = (int(found[-1][0]), int(found[-1][1])) if found else (0, 0)
    return code == 0 and failed == 0, passed, failed, output


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose = "-v" in sys.argv
    files = chosen_files(args, "--with-api" in sys.argv)
    if not files:
        say("No test files matched.")
        return 1

    start = time.time()
    total_passed = total_failed = 0
    broken = []
    with progress.bar(len(files), "tests", unit="file") as bar:
        for name in files:
            bar.set_description(name.replace("_test.py", ""))
            ok, passed, failed, output = run(name)
            total_passed += passed
            total_failed += failed
            counts = f"{passed} passed" + (f", {failed} failed" if failed else "")
            say(f"{'pass' if ok else 'FAIL'}  {name:34s} {counts}")
            if not ok:
                broken.append(name)
                for line in failure_lines(output)[:25]:
                    say(f"      {line}")
            if verbose:
                say(output.rstrip() + "\n")
            bar.update()

    seconds = time.time() - start
    say(f"\n{total_passed} passed, {total_failed} failed across {len(files)} files "
        f"({seconds:.0f}s)" + (f". Failing: {', '.join(broken)}" if broken else "."))
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())

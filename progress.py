"""A progress bar for the long-running evals, and a print that won't break it.

Used by rag_eval.py and conversation_eval.py, which run for minutes. The
static test suites finish in seconds and don't need one.

Optional by design: if tqdm isn't installed, or output isn't going to a
terminal (piped to a file, run by CI), there is no bar and `say` is plain
print. The evals' output is the same either way; the bar only appears
while running, on stderr, and clears itself when done.

Inside a bar, print with `say` instead of print(). A plain print() lands
in the middle of the bar's line and leaves fragments of it behind.
"""

import contextlib
import sys

try:
    from tqdm import tqdm
    from tqdm.contrib.logging import logging_redirect_tqdm
except ImportError:
    tqdm = None


def enabled():
    return tqdm is not None and sys.stderr.isatty()


def say(*args, sep=" ", end="\n"):
    """print(), but written above an active bar instead of through it."""
    text = sep.join(str(a) for a in args)
    if enabled():
        tqdm.write(text, file=sys.stdout, end=end)
    else:
        print(text, end=end)


class _NoBar:
    """Stands in for a bar when there isn't one, so callers never check."""
    def update(self, n=1): pass
    def set_description(self, text): pass
    def close(self): pass


@contextlib.contextmanager
def bar(total, desc="", unit="it"):
    """`with bar(150, "Sunrise", unit="question") as b: ... b.update()`"""
    if not enabled():
        yield _NoBar()
        return
    # Log lines (conversation_eval logs warnings to the console) are routed
    # above the bar too, for the same reason as `say`.
    with logging_redirect_tqdm():
        b = tqdm(total=total, desc=desc, file=sys.stderr, leave=False,
                 dynamic_ncols=True, unit=" " + unit)
        try:
            yield b
        finally:
            b.close()

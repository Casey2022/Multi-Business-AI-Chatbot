#!/usr/bin/env python3
"""threads_test.py — the parts threads share are safe to share.

Run it:  python3 threads_test.py

gunicorn runs with --threads 4 (2026-10-20). Measured locally with Render's
settings, four visitors who sent at the same moment waited 2.2, 3.9, 5.9 and
7.6 seconds with one thread: each reply queued behind the one before, all
waiting on the model. With four threads they waited about 2 seconds each.

Threads share a process, so anything a module keeps for reuse is shared too.
Two needed care:

  * the Google Calendar client sits on httplib2, which Google documents as
    not thread-safe: one per thread (calendar_google._local).
  * the ChromaDB client is built on the first search: two threads' first
    searches must not both build one (rag._chroma_lock).

What was checked and left alone, in notes/backlog.md: rate limits count in
SQLite with an atomic increment; SQLite took 800 writes from 8 threads with
no lock errors; the chat page sends one message at a time per visitor.

No network: the clients are fakes.
"""

import logging
import os
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n" + "-" * len(text))


def at_once(fn, n=8):
    """Call fn from n threads released together. Returns their results."""
    results, gate = [None] * n, threading.Event()

    def run(i):
        gate.wait()
        results[i] = fn()
    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join()
    return results


def test_calendar_client():
    heading("the Google Calendar client: one per thread")
    import calendar_google
    built = []

    def fake_build(*args, **kwargs):
        time.sleep(0.02)                # a real build takes a while
        client = object()
        built.append(client)
        return client

    real = (calendar_google.build,
            calendar_google.service_account.Credentials.from_service_account_info,
            os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"))
    calendar_google.build = fake_build
    calendar_google.service_account.Credentials.from_service_account_info = \
        staticmethod(lambda info, scopes=None: object())
    os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = "{}"
    logging.disable(logging.CRITICAL)
    try:
        here = calendar_google._get_service()
        check("a thread reuses its own client",
              calendar_google._get_service() is here)
        clients = at_once(calendar_google._get_service)
        check("each of 8 threads gets its own client",
              len({id(c) for c in clients}) == 8 and here not in clients,
              f"{len({id(c) for c in clients})} distinct")
    finally:
        logging.disable(logging.NOTSET)
        calendar_google.build = real[0]
        calendar_google.service_account.Credentials.from_service_account_info = real[1]
        if real[2] is None:
            os.environ.pop("GOOGLE_SERVICE_ACCOUNT_JSON", None)
        else:
            os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = real[2]
        calendar_google._local = threading.local()


def test_chroma_client():
    heading("the ChromaDB client: built once, however many threads ask")
    import rag
    made = []

    class FakeClient:
        def __init__(self, path=None):
            time.sleep(0.05)            # widen the window a race needs
            made.append(self)

    real = (rag.chromadb.PersistentClient, rag._chroma_client, rag._chroma_pid)
    rag.chromadb.PersistentClient = FakeClient
    rag._chroma_client = rag._chroma_pid = None
    try:
        clients = at_once(rag.get_chroma_client)
        check("8 first searches at once build one client",
              len(made) == 1, f"built {len(made)}")
        check("and every thread gets that one",
              all(c is clients[0] for c in clients))
    finally:
        rag.chromadb.PersistentClient, rag._chroma_client, rag._chroma_pid = real


def test_procfile():
    heading("the Procfile runs threads")
    procfile = (ROOT / "Procfile").read_text()
    check("gunicorn with --threads (Render's Start Command must match)",
          re.search(r"--threads \d+", procfile) is not None, procfile)


def main():
    test_calendar_client()
    test_chroma_client()
    test_procfile()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

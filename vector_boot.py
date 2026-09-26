"""Build the vector collections at startup, in a process of its own.

Why a separate process: on Render, gunicorn runs with --preload. Render
sets GUNICORN_CMD_ARGS="--preload ..." for every Python service, where no
settings page shows it. With --preload, app.py is imported ONCE in
gunicorn's manager process and then copied into each worker by fork().
ChromaDB's engine doesn't survive that copy: a client created in the
manager, or anything it set up, freezes the worker on its first search.

From 2026-09-17 (the demo sandbox) until 2026-09-26, app.py's bootstrap()
built every collection in the manager. Every question on the live site then
hung for 60s until gunicorn killed the worker. Bookings, which never
search, kept working and hid it.

So the manager never touches ChromaDB. bootstrap() runs this module as a
fresh interpreter (`python -m vector_boot`) that builds or checks each
collection and exits; workers open their own client on their first search.
"""

import logging
import os
import subprocess
import sys
import time

log = logging.getLogger("bootstrap")

# Long enough to embed every business from scratch on a slow cold start.
TIMEOUT_SECONDS = int(os.environ.get("VECTOR_BOOT_TIMEOUT", "600"))


def run_in_subprocess():
    """Called by app.bootstrap(). True if the collections are ready."""
    started = time.monotonic()
    log.info("Building vector collections in a separate process...")
    try:
        result = subprocess.run([sys.executable, "-m", "vector_boot"],
                                timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        log.error("Vector collections not ready after %ss; questions may get "
                  "the 'can't look that up' reply until the next restart.",
                  TIMEOUT_SECONDS)
        return False
    except Exception:
        log.exception("Couldn't start the vector-collection process")
        return False
    elapsed = time.monotonic() - started
    if result.returncode != 0:
        log.error("Vector-collection process failed (exit %s) after %.0fs",
                  result.returncode, elapsed)
        return False
    log.info("Vector collections ready (%.0fs)", elapsed)
    return True


def drop_demo_collections(client):
    """Every demo is swept at startup, so every demo- collection is orphaned.

    Done here rather than in demo.sweep_all(), which runs in the manager
    process and so must not touch ChromaDB (see the module docstring).
    """
    dropped = []
    for collection in client.list_collections():
        name = getattr(collection, "name", collection)
        if str(name).startswith("demo-"):
            try:
                client.delete_collection(name)
                dropped.append(name)
            except Exception as e:
                log.warning("Could not drop collection %r: %s", name, e)
    if dropped:
        log.info("Dropped %d demo collection(s) left from a previous run",
                 len(dropped))
    return dropped


def ingest_all():
    """Check or build the collection of every active, non-demo business."""
    from config import load_config
    from db import get_all_businesses
    from rag import ensure_ingested, get_chroma_client

    drop_demo_collections(get_chroma_client())
    failures = 0
    for b in get_all_businesses():
        # A demo clone shares its template's collection until it publishes;
        # ingesting one would overwrite a real client's knowledge base.
        if not b["active"] or b.get("is_demo"):
            continue
        try:
            ensure_ingested(load_config(b["config_path"], b["id"]))
        except Exception as e:
            # One business's failure costs that business its RAG, not
            # everyone theirs.
            failures += 1
            log.warning("Ingest failed for %s: %s", b["name"], e)
    return failures


def main():
    from dotenv import load_dotenv
    load_dotenv()
    from logging_setup import setup_logging
    setup_logging()
    ingest_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())

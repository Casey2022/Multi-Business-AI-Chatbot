#!/usr/bin/env python3
"""startup_test.py: gunicorn's manager process never touches ChromaDB.

Run it:  python3 startup_test.py

Render runs gunicorn with --preload (its default GUNICORN_CMD_ARGS, shown
nowhere in the dashboard), so app.py's startup runs in the manager process
and is copied into each worker. From 2026-09-17 to 2026-09-26 that startup
built the vector collections itself, the workers inherited its ChromaDB
client, and every live question froze inside the search until the worker
was killed. Bookings never search, so they kept working.

No server, no ChromaDB, no network: app.py is read as source, and
vector_boot / demo run against fakes.
"""

import ast
import logging
import subprocess
import sys
import types
from pathlib import Path

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def stub_missing(*names):
    """Stand in for heavy packages this machine lacks."""
    class _Any:
        def __getattr__(self, name): return _Any()
        def __call__(self, *a, **k): return _Any()
    stubbed = set()
    for name in names:
        parent = name.rsplit(".", 1)[0]
        if parent != name and parent in stubbed:
            missing = True
        else:
            try:
                __import__(name)
                missing = False
            except ImportError:
                missing = True
        if missing:
            module = types.ModuleType(name)
            module.__getattr__ = lambda attr: _Any()
            sys.modules[name] = module
            stubbed.add(name)


CHROMA_CALLS = {"ensure_ingested", "get_chroma_client", "ingest_documents",
                "_drop_forked_collection", "PersistentClient"}


def names_called(node):
    out = set()
    for c in ast.walk(node):
        if isinstance(c, ast.Call):
            f = c.func
            out.add(f.id if isinstance(f, ast.Name) else
                    f.attr if isinstance(f, ast.Attribute) else None)
    return out


def test_bootstrap_source():
    heading("app.py's startup (runs in gunicorn's manager) never opens ChromaDB")
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    boot = funcs["bootstrap"]
    called = names_called(boot)
    check("bootstrap() calls nothing that opens ChromaDB",
          not (called & CHROMA_CALLS), f"calls {sorted(called & CHROMA_CALLS)}")
    check("bootstrap() builds the collections in a separate process",
          "run_in_subprocess" in called)

    sweeps = [c for c in ast.walk(boot) if isinstance(c, ast.Call)
              and getattr(c.func, "id", None) == "sweep_all"]
    check("bootstrap() sweeps demos without dropping their collections",
          sweeps and all(any(k.arg == "drop_collections"
                             and getattr(k.value, "value", None) is False
                             for k in c.keywords) for c in sweeps))

    # Code at module scope also runs in the manager under --preload.
    module_level = [n for n in tree.body
                    if not isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    top = set()
    for n in module_level:
        top |= names_called(n)
    check("nothing at app.py's module scope opens ChromaDB",
          not (top & CHROMA_CALLS), f"calls {sorted(top & CHROMA_CALLS)}")


def test_sweep():
    heading("demo.sweep_all can leave the collections to vector_boot")
    import demo
    dropped, deleted = [], []
    real = (demo.get_demo_businesses, demo.delete_business,
            demo._drop_forked_collection)
    demo.get_demo_businesses = lambda *a, **k: [{"id": 7, "slug": "demo-x-1",
                                                 "rag_collection": "demo-x-1"}]
    demo.delete_business = lambda bid: deleted.append(bid) or True
    demo._drop_forked_collection = lambda row: dropped.append(row["slug"])
    try:
        demo.sweep_all(drop_collections=False)
        check("drop_collections=False deletes the demo but not its collection",
              deleted == [7] and dropped == [])
        demo.sweep_all()
        check("the default still drops the collection (idle sweeps in a worker)",
              dropped == ["demo-x-1"])
    finally:
        (demo.get_demo_businesses, demo.delete_business,
         demo._drop_forked_collection) = real


def test_vector_boot():
    import vector_boot
    heading("vector_boot drops orphaned demo collections, and only those")

    class Client:
        def __init__(self, names):
            self.names, self.deleted = names, []
        def list_collections(self):
            return [types.SimpleNamespace(name=n) for n in self.names]
        def delete_collection(self, name): self.deleted.append(name)

    client = Client(["bobs_plumbing", "demo-sunrise_bakery_and_cafe-60d329",
                     "sunrise_bakery_and_cafe", "demo-bobs_plumbing-aa11"])
    vector_boot.drop_demo_collections(client)
    check("both demo- collections dropped", sorted(client.deleted) ==
          ["demo-bobs_plumbing-aa11", "demo-sunrise_bakery_and_cafe-60d329"])
    check("real businesses' collections untouched",
          "sunrise_bakery_and_cafe" not in client.deleted
          and "bobs_plumbing" not in client.deleted)

    heading("vector_boot ingests every active, non-demo business")
    ingested = []
    fake_rag = types.SimpleNamespace(
        ensure_ingested=lambda cfg: (_ for _ in ()).throw(RuntimeError("boom"))
        if cfg["name"] == "Broken" else ingested.append(cfg["name"]),
        get_chroma_client=lambda: Client([]))
    fake_db = types.SimpleNamespace(get_all_businesses=lambda: [
        {"id": 1, "name": "Bob", "active": True, "is_demo": False, "config_path": "b"},
        {"id": 2, "name": "Off", "active": False, "is_demo": False, "config_path": "o"},
        {"id": 3, "name": "Demo", "active": True, "is_demo": True, "config_path": "d"},
        {"id": 4, "name": "Broken", "active": True, "is_demo": False, "config_path": "x"},
        {"id": 5, "name": "Sunrise", "active": True, "is_demo": False, "config_path": "s"},
    ])
    fake_config = types.SimpleNamespace(
        load_config=lambda path, bid: {"name": {"b": "Bob", "o": "Off", "d": "Demo",
                                                "x": "Broken", "s": "Sunrise"}[path]})
    saved = {k: sys.modules.get(k) for k in ("rag", "db", "config")}
    sys.modules.update(rag=fake_rag, db=fake_db, config=fake_config)
    logging.disable(logging.CRITICAL)
    try:
        failures = vector_boot.ingest_all()
    finally:
        logging.disable(logging.NOTSET)
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    check("active real businesses ingested, in order", ingested == ["Bob", "Sunrise"],
          str(ingested))
    check("one broken business doesn't stop the rest, and is counted",
          failures == 1)

    heading("the separate process is reported honestly")
    real_run = vector_boot.subprocess.run
    logging.disable(logging.CRITICAL)
    try:
        seen = []
        def ok(cmd, timeout):
            seen.append(cmd)
            return types.SimpleNamespace(returncode=0)
        vector_boot.subprocess.run = ok
        check("success is reported", vector_boot.run_in_subprocess() is True)
        check("it runs a FRESH interpreter (python -m vector_boot), not a fork",
              seen and seen[0][1:] == ["-m", "vector_boot"]
              and seen[0][0] == sys.executable)
        vector_boot.subprocess.run = lambda cmd, timeout: types.SimpleNamespace(returncode=1)
        check("a failed process is reported as a failure",
              vector_boot.run_in_subprocess() is False)
        def slow(cmd, timeout): raise subprocess.TimeoutExpired(cmd, timeout)
        vector_boot.subprocess.run = slow
        check("a process that runs too long doesn't hang startup",
              vector_boot.run_in_subprocess() is False)
    finally:
        vector_boot.subprocess.run = real_run
        logging.disable(logging.NOTSET)


def main():
    stub_missing("anthropic", "chromadb", "chromadb.utils",
                 "chromadb.utils.embedding_functions", "dotenv")
    test_bootstrap_source()
    test_sweep()
    test_vector_boot()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

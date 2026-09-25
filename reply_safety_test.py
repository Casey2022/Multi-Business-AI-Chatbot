#!/usr/bin/env python3
"""reply_safety_test.py: a question is never swallowed or hijacked.

Run it:  python3 reply_safety_test.py

Two live failures on 2026-09-25, asking Sunrise about a deposit:

1. "I cancelled my cake order 3 days before, do I get my deposit back?"
   started a BOOKING. The booking keyword rule fired on the word "order"
   anywhere in a message, before anything read what the sentence meant.
2. Every question got "Sorry, I couldn't reach the assistant". The
   knowledge-base search (a Voyage call) raised, nothing caught it, Flask
   returned an HTML error page, and the widget couldn't read it. Bookings
   still worked because they never search.

No API calls, no Flask: the search, Claude and process_message are faked.
"""

import ast
import logging
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


def import_llm():
    """llm.py (and rag.py) with only the modules this machine lacks stubbed."""
    class _Any:
        def __getattr__(self, name): return _Any()
        def __call__(self, *a, **k): return _Any()
    stubbed = set()
    for name in ("anthropic", "chromadb", "chromadb.utils",
                 "chromadb.utils.embedding_functions", "dotenv"):
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
    if not hasattr(sys.modules["anthropic"], "Anthropic"):
        sys.modules["anthropic"].Anthropic = lambda *a, **k: _Any()
    import llm
    import rag
    return llm, rag


def test_booking_keyword(config):
    import rules
    heading("the booking keyword starts a booking only when that's the whole message")
    commands = ["book", "order", "Order please", "schedule",
                "I would like to book", "I want to book an appointment",
                "make an appointment", "book an appointment please!",
                "Hi, I'd like to place an order", "I need an appointment",
                "can I book"]
    for message in commands:
        check(f"starts a booking: {message!r}",
              rules.get_reply(message, config) == rules.BOOK_INTENT)

    not_commands = [
        # the two live messages, verbatim
        "I have a question about my deposit. I cancelled 3 days before, do I get my deposit back?",
        "yes please, I wanted to know if I cancelled my cake order 3 days before, do I get my deposit back?",
        "no, I want to know about my deposit. I cancelled 3 days before, do I get my deposit back?",
        "where is my order", "change my order", "can I change my appointment",
        "how far ahead should I order a wedding cake?", "I read a good book",
    ]
    for message in not_commands:
        check(f"not a booking command: {message!r}",
              rules.get_reply(message, config) != rules.BOOK_INTENT,
              f"got {rules.get_reply(message, config)!r}")

    heading("business rules that share the word can win now")
    reply = rules.get_reply("cancel my order", config)
    check("Sunrise's 'cancel my order' rule answers, not the booking flow",
          reply and reply != rules.BOOK_INTENT and "(585) 555-0188" in reply,
          f"got {reply!r}")
    check("a greeting still gets the greeting rule",
          "Welcome" in (rules.get_reply("hi", config) or ""))


def test_search_failure(llm, rag, config):
    heading("a failed knowledge-base search is reported, not guessed around")

    class Collection:
        def __init__(self, error=None): self.error = error
        def query(self, **k):
            if self.error: raise self.error
            return {"documents": [["# Deposits\nLess than 7 days: forfeited."]],
                    "distances": [[0.2]]}

    class Client:
        def __init__(self, collection=None, missing=False):
            self.collection, self.missing = collection, missing
        def get_collection(self, *a, **k):
            if self.missing: raise ValueError("no such collection")
            return self.collection

    real_client = rag.get_chroma_client
    logging.disable(logging.CRITICAL)      # the expected tracebacks aren't news
    try:
        rag.get_chroma_client = lambda: Client(Collection(RuntimeError("Voyage 429")))
        try:
            rag.retrieve("deposit?", config)
            raised = None
        except rag.RetrievalUnavailable as e:
            raised = e
        check("a search that raises becomes RetrievalUnavailable, not []",
              raised is not None)
        check("the underlying error is kept in the message",
              raised is not None and "Voyage 429" in str(raised))

        rag.get_chroma_client = lambda: Client(missing=True)
        check("a missing collection still means 'nothing found' ([])",
              rag.retrieve("deposit?", config) == [])

        rag.get_chroma_client = lambda: Client(Collection())
        check("a working search still returns its chunks",
              len(rag.retrieve("deposit?", config)) == 1)
    finally:
        rag.get_chroma_client = real_client
        logging.disable(logging.NOTSET)

    heading("get_llm_reply without its documents")
    sent = []
    real_create, real_retrieve = llm._create, llm.retrieve
    def fail(*a, **k): raise rag.RetrievalUnavailable("Voyage 429")
    llm._create = lambda **call: sent.append(call)
    llm.retrieve = fail
    try:
        reply = llm.get_llm_reply("do I get my deposit back?", None, config)
    finally:
        llm._create, llm.retrieve = real_create, real_retrieve
    check("says it can't look it up", "can't look that up" in reply, reply)
    check("gives the business's phone number", "(585) 555-0188" in reply, reply)
    check("does NOT ask Claude to answer without the policy text", sent == [],
          f"{len(sent)} call(s) made")
    check("no phone configured still gives a usable reply",
          "call us directly" in llm.unavailable_reply({"business": {}}))


def test_voyage_deadline(rag):
    heading("every Voyage call has a deadline shorter than the worker's life")
    # voyageai defaults to 600s per request; Render kills a worker at 60s.
    made = {}

    class FakeClient:
        def __init__(self, api_key=None, max_retries=0, timeout=None):
            made.update(api_key=api_key, max_retries=max_retries, timeout=timeout)

    class FakeEmbedder:
        def __init__(self, api_key=None, model_name=None):
            self._client = "chromadb's own client, timeout=None"

    real_voyage = sys.modules.get("voyageai")
    real_embedder = getattr(rag.embedding_functions, "VoyageAIEmbeddingFunction", None)
    sys.modules["voyageai"] = types.SimpleNamespace(Client=FakeClient)
    rag.embedding_functions.VoyageAIEmbeddingFunction = FakeEmbedder
    try:
        fn = rag.make_voyage_embedding_fn("test-key")
    finally:
        if real_voyage is None:
            del sys.modules["voyageai"]
        else:
            sys.modules["voyageai"] = real_voyage
        rag.embedding_functions.VoyageAIEmbeddingFunction = real_embedder
    check("chromadb's timeout-less client is replaced",
          isinstance(fn._client, FakeClient))
    check("the replacement has a timeout", made.get("timeout") == rag.VOYAGE_TIMEOUT
          and rag.VOYAGE_TIMEOUT > 0, str(made))
    worst = rag.VOYAGE_ATTEMPTS * rag.VOYAGE_TIMEOUT + 16 * (rag.VOYAGE_ATTEMPTS - 1)
    check(f"worst case ({worst:.0f}s) fits inside Render's 60s worker timeout",
          worst < 60, f"{rag.VOYAGE_ATTEMPTS} attempts x {rag.VOYAGE_TIMEOUT}s")

    # The private attribute swapped above must still exist on the real class,
    # or the replacement silently does nothing after a chromadb upgrade.
    source = None
    try:
        import inspect
        import chromadb.utils.embedding_functions.voyageai_embedding_function as m
        source = inspect.getsource(m)
    except Exception:
        pass
    if source is not None:
        check("chromadb's Voyage embedder still keeps its client in self._client",
              "self._client" in source)
    else:
        print("  [skip] chromadb not installed here; can't inspect its embedder")

    heading("no Voyage key on Render fails fast instead of hanging")
    real_fn, real_render = rag._embedding_fn, rag.ON_RENDER
    rag._embedding_fn, rag.ON_RENDER = None, True
    try:
        try:
            rag.retrieve("deposit?", {"business": {"name": "x"}})
            raised = False
        except rag.RetrievalUnavailable:
            raised = True
    finally:
        rag._embedding_fn, rag.ON_RENDER = real_fn, real_render
    check("retrieve refuses at once (the local embedder would hang the worker)",
          raised)


def test_endpoint_safety_net():
    heading("the chat and SMS endpoints never crash on a bug")
    source = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    for endpoint in ("webchat_reply", "sms_reply"):
        calls = {c.func.id for c in ast.walk(funcs[endpoint])
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        check(f"{endpoint} calls safe_process_message",
              "safe_process_message" in calls and "process_message" not in calls,
              f"calls {sorted(calls & {'process_message', 'safe_process_message'})}")

    # Run the real safe_process_message against a process_message that crashes.
    namespace = {"log": logging.getLogger("reply_safety_test")}
    def boom(*a, **k): raise KeyError("a bug somewhere in the flow")
    namespace["process_message"] = boom
    exec(compile(ast.Module([funcs["safe_process_message"]], []),
                 "app.py", "exec"), namespace)
    logging.disable(logging.CRITICAL)
    try:
        reply = namespace["safe_process_message"](
            "hello", "web-1", 1, {"business": {"phone": "(585) 555-0188"}},
            channel="webchat")
    finally:
        logging.disable(logging.NOTSET)
    check("a crash becomes an apology with the phone number",
          isinstance(reply, str) and "(585) 555-0188" in reply, repr(reply))

    namespace["process_message"] = lambda *a, **k: "normal reply"
    check("a normal reply passes through unchanged",
          namespace["safe_process_message"]("hi", "web-1", 1, {}, "webchat")
          == "normal reply")


def main():
    llm, rag = import_llm()
    from config import load_config
    config = load_config("config/sunrise_bakery_and_cafe.yaml")
    config.setdefault("business", {})["id"] = 1
    test_booking_keyword(config)
    test_search_failure(llm, rag, config)
    test_voyage_deadline(rag)
    test_endpoint_safety_net()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

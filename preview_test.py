#!/usr/bin/env python3
"""preview_test.py: the owner's answer preview, with a fake index and model.

Run it:  python3 preview_test.py

The preview builds a throwaway index from the unpublished sections and asks
each question twice through llm.get_llm_reply: against the live index
("Now") and the draft ("After you publish"). These checks pin that the
draft holds exactly what Publish would build, that "After" really searches
the draft and "Now" the live index, that the draft is deleted even when
something fails, the question limits, the changed-section detection, the
suggestion prompt, the rate limit, and the page wiring. No network.
"""

import sys
import tempfile
import types
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="preview_test_")) / "test.db"

import booking_state_test as helpers

ROOT = Path(__file__).resolve().parent
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


class FakeCollection:
    def __init__(self, name):
        self.name, self.docs = name, []
    def add(self, documents, ids, metadatas):
        self.docs += list(documents)
    def get(self, include=None):
        return {"documents": list(self.docs)}


class FakeClient:
    def __init__(self):
        self.collections, self.deleted = {}, []
    def get_or_create_collection(self, name, **k):
        return self.collections.setdefault(name, FakeCollection(name))
    def get_collection(self, name, **k):
        if name not in self.collections:
            raise ValueError(f"no collection {name}")
        return self.collections[name]
    def delete_collection(self, name):
        if name in self.collections:
            del self.collections[name]
            self.deleted.append(name)
        else:
            raise ValueError(f"no collection {name}")


def main():
    helpers._load_scheduler()
    import rag
    import llm
    import preview
    import ratelimit
    db.init_db()

    biz = 7
    db.add_document_section(biz, "Pizza Sizes", "10, 14 and 18 inch. A 10-inch "
                            "gluten-free crust is available for $3 extra.")
    db.add_document_section(biz, "Delivery", "Within 6 miles, $3 fee.")
    config = {"business": {"name": "Test Pizza", "collection": "test_pizza", "id": biz}}

    fake = FakeClient()
    rag.get_chroma_client = lambda: fake
    rag._embedding_fn = None

    heading("questions an owner types")
    qs = preview.parse_questions("- Do you deliver?\n\n2) do you deliver?\n"
                                 "• gluten free on a large?\n" + "x" * 500 +
                                 "\nq4\nq5\nq6")
    check("bullets and numbers are stripped, duplicates dropped",
          qs[:2] == ["Do you deliver?", "gluten free on a large?"], qs)
    check("at most 5 questions", len(qs) == preview.MAX_QUESTIONS, qs)
    check("each at most 200 characters", all(len(q) <= 200 for q in qs))
    check("an empty box is no questions", preview.parse_questions(" \n \n") == [])

    heading("the draft holds exactly what Publish would build")
    chunks, ids, _ = rag.db_chunks(biz)
    check("one chunk per section, '## title' first",
          chunks[0].startswith("## Pizza Sizes\n") and len(chunks) == 2, chunks)
    src = (ROOT / "rag.py").read_text()
    check("Publish (ingest_documents) builds from the same db_chunks",
          "chunks, ids, metas = db_chunks(business_id)" in src)
    name = preview.draft_collection_name(config, biz)
    check("the draft index is per business (a demo clone can't touch its template's)",
          name == "test_pizza__preview_7", name)

    heading("what changed since the last publish")
    live = ["## Pizza Sizes\n10, 14 and 18 inch.", "## Delivery\nWithin 6 miles, $3 fee.",
            "## Party Trays\nFeed 15 to 20."]
    changed, removed = preview.changed_sections(config, biz, live=live)
    check("an edited section is changed", any("gluten-free" in c for c in changed), changed)
    check("an untouched section isn't", not any(c.startswith("## Delivery") for c in changed))
    check("a deleted section is reported as removed", removed == ["Party Trays"], removed)
    check("nothing changed: nothing to suggest from",
          preview.changed_sections(config, biz, live=chunks) == ([], []))

    heading("suggested questions")
    sent = []
    def fake_create(**call):
        sent.append(call)
        return types.SimpleNamespace(usage=None, content=[types.SimpleNamespace(
            type="text", text="Can I get gluten-free on a large?\nHow much is it?\n"
                              "Do you still do party trays?")])
    real_create = llm._create
    llm._create = fake_create
    try:
        got = preview.suggest_questions(config, changed, removed)
    finally:
        llm._create = real_create
    prompt = sent[0]["messages"][0]["content"]
    check("suggestions come back as questions", got[0] == "Can I get gluten-free on a large?", got)
    check("the owner's text is fenced as data", "<sections>" in prompt and "gluten-free" in prompt)
    check("a removed topic gets a question too", "Party Trays" in prompt)
    check("no changes: no call, no suggestions", preview.suggest_questions(config, [], []) == [])
    llm._create = lambda **c: (_ for _ in ()).throw(RuntimeError("down"))
    try:
        check("a failed call suggests nothing (the page says to type some)",
              preview.suggest_questions(config, changed) == [])
    finally:
        llm._create = real_create

    heading("Now searches the live index, After searches the draft")
    fake.get_or_create_collection("test_pizza").add(live, ["a", "b", "c"], [{}, {}, {}])
    asked = []
    def fake_reply(message, history=None, config=None, channel="sms",
                   mid_booking=False, temperature=None, now=None):
        coll = config["business"]["collection"]
        asked.append((message, coll, temperature, list(history or [])))
        if "deliver" in message:          # same facts, reworded
            return ("Delivery covers up to 6 miles." if coll.endswith("__preview_7")
                    else "We deliver within 6 miles!")
        if coll.endswith("__preview_7"):
            searched = fake.get_collection(coll).docs
            return ("Only the 10-inch, $3 extra." if any("10-inch" in d for d in searched)
                    else "No idea.")
        return "Yes, $3 extra on any size."
    import json as _json
    fact_calls = []
    def fake_facts(**call):
        content = call["messages"][0]["content"]
        fact_calls.append(call)
        if "any size" in content and "10-inch" in content:
            data = {"facts_changed": True, "differences": [
                {"topic": "gluten-free sizes", "now": "on any size",
                 "after": "Only the 10-inch"}]}
        else:
            data = {"facts_changed": False, "differences": []}
        return types.SimpleNamespace(usage=None, content=[types.SimpleNamespace(
            type="text", text=_json.dumps(data))])
    real_reply, real_create = llm.get_llm_reply, llm._create
    llm.get_llm_reply, llm._create = fake_reply, fake_facts
    try:
        results = preview.run_preview(config, biz, ["gluten free on a large?", "do you deliver?"])
    finally:
        llm.get_llm_reply, llm._create = real_reply, real_create
    by_q = {r["question"]: r for r in results}
    r = by_q["gluten free on a large?"]
    check("Now is today's answer (live index)", r["now"] == "Yes, $3 extra on any size.", r)
    check("After is the draft's answer, which really searched the draft",
          r["after"] == "Only the 10-inch, $3 extra.", r)
    check("a changed fact is marked changed", r["changed"] is True)
    check("with what changed", r["differences"] == [{"topic": "gluten-free sizes",
          "now": "on any size", "after": "Only the 10-inch"}], r["differences"])
    check("and the phrase highlighted in the After reply",
          ("Only the 10-inch", True) in r["after_segments"], r["after_segments"])
    d = by_q["do you deliver?"]
    check("a reworded reply with the same facts is the same",
          d["changed"] is False and d["differences"] == [], d)
    colls = {c for _, c, _, _ in asked}
    check("both indexes were used, nothing else",
          colls == {"test_pizza", "test_pizza__preview_7"}, colls)
    check("answers are pinned (temperature 0), so 'changed' means changed",
          all(t == 0 for _, _, t, _ in asked))
    check("each question is asked fresh, with no history",
          all(h == [] for _, _, _, h in asked))
    check("the draft index is deleted afterwards",
          "test_pizza__preview_7" not in fake.collections
          and "test_pizza__preview_7" in fake.deleted)
    check("the live index is untouched", "test_pizza" in fake.collections
          and fake.collections["test_pizza"].docs == live)
    check("the caller's config still points at the live index",
          config["business"]["collection"] == "test_pizza")

    llm.get_llm_reply = lambda *a, **k: "Same answer."
    try:
        same = preview.run_preview(config, biz, ["do you deliver?"])
    finally:
        llm.get_llm_reply = real_reply
    check("identical answers are marked the same", same[0]["changed"] is False)

    real_answer = preview._answer
    preview._answer = lambda q, c: (_ for _ in ()).throw(RuntimeError("boom"))
    fake.deleted.clear()
    try:
        preview.run_preview(config, biz, ["x?"])
        check("an error is raised to the caller", False)
    except RuntimeError:
        check("an error is raised to the caller", True)
    finally:
        preview._answer = real_answer
    check("and the draft index is still deleted",
          "test_pizza__preview_7" in fake.deleted
          and "test_pizza__preview_7" not in fake.collections)

    heading("a ceiling on previews (demo visitors can edit a knowledge base)")
    first = ratelimit.knowledge_preview(99)[0]
    second = ratelimit.knowledge_preview(99)[0]
    third = ratelimit.knowledge_preview(99)[0]
    check(f"{ratelimit.PREVIEW_PER_MINUTE} a minute, then refused",
          first and second and not third, (first, second, third))
    check("another business has its own allowance", ratelimit.knowledge_preview(98)[0])

    heading("facts, not wording")
    calls = []
    def facts_reply(text):
        def _c(**call):
            calls.append(call)
            return types.SimpleNamespace(usage=None, content=[types.SimpleNamespace(
                type="text", text=text)])
        return _c
    real_create = llm._create
    try:
        calls.clear()
        r = preview.compare_facts("q", "We deliver within 6 miles.",
                                  "  We deliver   within 6 miles. ")
        check("identical (but for spacing): same, and no model call",
              r == {"changed": False, "differences": [], "method": "identical"}
              and not calls, (r, len(calls)))
        llm._create = facts_reply('{"facts_changed": false, "differences": []}')
        r = preview.compare_facts("do you deliver?", "We deliver within 6 miles! 🍕",
                                  "Delivery covers up to 6 miles.")
        check("reworded, same facts: Same", r["changed"] is False and r["method"] == "model")
        prompt = calls[-1]["messages"][0]["content"]
        check("both replies and the question are fenced as data",
              "<reply_now>" in prompt and "<reply_after>" in prompt and "<question>" in prompt)
        check("the comparison is pinned", calls[-1].get("temperature") == 0)
        llm._create = facts_reply('```json\n{"facts_changed": true, "differences": ['
                                  '{"topic": "fee", "now": "$3", "after": "$5"},'
                                  '{"topic": "a", "now": "", "after": "x"},'
                                  '{"topic": "b", "now": "", "after": "y"},'
                                  '{"topic": "c", "now": "", "after": "z"}]}\n```')
        r = preview.compare_facts("fee?", "The fee is $3.", "The fee is $5.")
        check("a changed fact: Changes, fenced JSON accepted",
              r["changed"] is True and r["differences"][0] == {"topic": "fee", "now": "$3", "after": "$5"})
        check(f"at most {preview.MAX_DIFFERENCES} differences listed",
              len(r["differences"]) == preview.MAX_DIFFERENCES)
        llm._create = facts_reply('{"facts_changed": false, "differences": [{"topic": "x", "now": "a", "after": "b"}]}')
        check("'not changed' wins over a stray difference",
              preview.compare_facts("q", "a", "b")["differences"] == [])
        for bad in ("not json", '{"differences": []}', '["x"]'):
            llm._create = facts_reply(bad)
            r = preview.compare_facts("q", "old", "new")
            check(f"unreadable answer ({bad[:12]!r}) falls back to the exact comparison",
                  r == {"changed": True, "differences": [], "method": "exact"}, r)
        llm._create = lambda **c: (_ for _ in ()).throw(RuntimeError("down"))
        check("a failed call falls back too (never hides a change)",
              preview.compare_facts("q", "old", "new")["method"] == "exact")
    finally:
        llm._create = real_create

    seg = preview.highlight_segments("Only the 10-inch, $3 extra.", ["only the 10-inch", "$3"])
    check("phrases are highlighted where they appear (any case)",
          seg == [("Only the 10-inch", True), (", ", False), ("$3", True), (" extra.", False)], seg)
    check("the pieces join back to exactly the reply",
          "".join(p for p, _ in seg) == "Only the 10-inch, $3 extra.")
    check("a phrase that isn't there word for word isn't highlighted",
          preview.highlight_segments("Only the 10-inch.", ["just the ten inch"])
          == [("Only the 10-inch.", False)])
    check("overlapping phrases don't double-mark",
          sum(1 for _, m in preview.highlight_segments("the 10-inch crust", ["10-inch crust", "10-inch"]) if m) == 1)
    check("no phrases: the whole reply, unmarked",
          preview.highlight_segments("Hi.", []) == [("Hi.", False)]
          and preview.highlight_segments("", ["x"]) == [("", False)])
    page = (ROOT / "admin" / "templates" / "admin" / "knowledge.html").read_text()
    check("highlights are rendered escaped (no |safe), inside <mark>",
          '<mark class="fact-change">{{ piece }}</mark>' in page and "|safe" not in page
          and "| safe" not in page)

    heading("Discard changes: back to the published wording")
    bid = db.add_business("Discard Test", "discard_test", "config/crosstown_pizza.yaml")
    sid_a = db.add_document_section(bid, "Pizza Sizes", "Gluten-free comes in 10-inch only.")
    sid_b = db.add_document_section(bid, "Delivery", "Within 6 miles.")
    sid_c = db.add_document_section(bid, "Party Trays", "Feed 15 to 20.")
    db.set_documents_clean(bid)                       # published
    snap = db.get_published_snapshot(bid)
    check("publishing keeps a snapshot of the sections",
          [s["title"] for s in snap] == ["Pizza Sizes", "Delivery", "Party Trays"], snap)
    published = [(s["title"], s["body"]) for s in db.get_documents(bid)]
    db.update_document_section(sid_a, "Pizza Sizes", "A 10-inch gluten-free crust is available.")
    db.delete_document_section(sid_c)
    db.add_document_section(bid, "Catering", "Call us.")
    dirty = db.get_business_by_id(bid)["documents_dirty"]
    check("edits mark it unpublished", dirty == 1)
    check("discard succeeds", db.discard_document_changes(bid, updated_by="owner") is True)
    back = [(s["title"], s["body"]) for s in db.get_documents(bid)]
    check("the sections are exactly as published, in order", back == published, back)
    check("and it's no longer marked unpublished",
          db.get_business_by_id(bid)["documents_dirty"] == 0)
    versions = db.get_document_versions(sid_a)
    check("the discarded wording is kept in the section's History",
          any("available" in v["body"] for v in versions), versions)
    check("discarding again changes nothing",
          db.discard_document_changes(bid) is True and
          [(s["title"], s["body"]) for s in db.get_documents(bid)] == published)

    bid2 = db.add_business("Lazy Test", "lazy_test", "config/crosstown_pizza.yaml")
    conn = db.get_connection()
    conn.execute("INSERT INTO documents (business_id, position, title, body, updated_at) "
                 "VALUES (?, 0, 'Hours', 'Open 11 to 10.', 'x')", (bid2,))
    conn.execute("UPDATE businesses SET documents_dirty = 0 WHERE id = ?", (bid2,))
    conn.commit(); conn.close()
    sid = db.get_documents(bid2)[0]["id"]
    db.update_document_section(sid, "Hours", "Open 9 to 5.")
    check("a clean business with no snapshot gets one on its first edit",
          db.get_published_snapshot(bid2)[0]["body"] == "Open 11 to 10.")
    db.discard_document_changes(bid2)
    check("so the first edit can be discarded too",
          db.get_documents(bid2)[0]["body"] == "Open 11 to 10.")

    # Your local database today: edited by scripts (dirty), never published
    # through the portal, so no snapshot. Discard must refuse, not guess.
    bid3 = db.add_business("No Snapshot", "no_snapshot", "config/crosstown_pizza.yaml")
    conn = db.get_connection()
    conn.execute("INSERT INTO documents (business_id, position, title, body, updated_at) "
                 "VALUES (?, 0, 'Hours', 'Open late.', 'x')", (bid3,))
    conn.execute("UPDATE businesses SET documents_dirty = 1 WHERE id = ?", (bid3,))
    conn.commit(); conn.close()
    db.update_document_section(db.get_documents(bid3)[0]["id"], "Hours", "Open late.")
    check("dirty with no published version: nothing to discard to",
          db.discard_document_changes(bid3) is False
          and db.get_documents(bid3)[0]["body"] == "Open late.")
    imp = (ROOT / "import_documents.py").read_text()
    check("importing the seed marks it published (and snapshots it)",
          "set_documents_clean(business[\"id\"])" in imp)
    check("deleting a demo business deletes its snapshot",
          ("published_snapshot", "business_id") in db._TENANT_TABLES)

    heading("the page is wired up")
    routes = (ROOT / "admin" / "routes.py").read_text()
    page = (ROOT / "admin" / "templates" / "admin" / "knowledge.html").read_text()
    check("a POST route serves the preview",
          '"/business/<int:business_id>/knowledge/preview", methods=["POST"]' in routes)
    check("the route checks access and the rate limit",
          "def knowledge_preview" in routes and
          "ratelimit.knowledge_preview(business_id)" in routes.split("def knowledge_preview")[1][:900]
          and "require_business_access(business_id)" in routes.split("def knowledge_preview")[1][:400])
    check("the page posts to it with a CSRF token",
          "url_for('admin.knowledge_preview'" in page and
          "_csrf_token" in page.split("knowledge_preview")[1][:200])
    check("Discard has a POST route that checks access",
          '"/business/<int:business_id>/knowledge/discard", methods=["POST"]' in routes
          and "require_business_access(business_id)" in routes.split("def knowledge_discard")[1][:300])
    check("the unpublished banner offers Discard beside Publish, with CSRF",
          "url_for('admin.knowledge_discard'" in page and
          "_csrf_token" in page.split("knowledge_discard")[1][:200])
    check("the page shows Now and After for each result",
          "r.now" in page and "r.after" in page and "After you publish" in page)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

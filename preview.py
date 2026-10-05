"""preview.py: see how the assistant will answer before publishing.

An owner can write a true sentence the assistant reads wrongly: "A 10-inch
gluten-free crust is available" was answered "$3 extra on any size" (it's
the only size). Publish used to be the first time anyone saw that, and the
first person to see it was a customer.

The preview builds a throwaway search index from the unpublished sections,
the same chunks Publish would build (rag.db_chunks), and asks each question
twice through the real reply path (llm.get_llm_reply): once against the
live index ("Now") and once against the draft ("After you publish"). Same
prompt, same tools, same model; only the index differs. The draft index is
deleted afterwards, success or not.

With no questions typed, it suggests a few from the sections that changed
since the last publish (found by comparing the draft's chunks with the live
index's), so an owner who edits one section gets questions about that
section.
"""

import copy
import logging
import re
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("preview")

MAX_QUESTIONS = 5
MAX_QUESTION_CHARS = 200
# Each question is two replies; four at a time keeps five questions well
# inside the worker's 60-second limit.
PARALLEL = 4


def parse_questions(text):
    """Owner-typed questions: one per line, bullets stripped, deduplicated,
    at most MAX_QUESTIONS of at most MAX_QUESTION_CHARS each."""
    seen, out = set(), []
    for line in (text or "").splitlines():
        q = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        if not q:
            continue
        q = q[:MAX_QUESTION_CHARS]
        if q.lower() in seen:
            continue
        seen.add(q.lower())
        out.append(q)
        if len(out) == MAX_QUESTIONS:
            break
    return out


def draft_collection_name(config, business_id):
    """Per business, so a demo clone's preview never touches its template's."""
    import rag
    return f"{rag.collection_for(config)}__preview_{business_id}"


def _title(chunk):
    first = (chunk or "").splitlines()[0] if chunk else ""
    return first[3:].strip() if first.startswith("## ") else first.strip()


def live_chunks(config):
    """The live index's texts, or [] if there isn't one yet."""
    import rag
    try:
        collection = rag.get_chroma_client().get_collection(
            rag.collection_for(config), embedding_function=rag._embedding_fn)
        return list(collection.get(include=["documents"])["documents"] or [])
    except Exception as e:
        log.info("No live index to compare with (%s)", e)
        return []


def changed_sections(config, business_id, live=None):
    """(changed, removed): draft sections whose text isn't live, and live
    titles no draft section has. The first publish of a seed-built index
    shows everything as changed, since its chunks were cut differently."""
    import rag
    chunks, _ids, _metas = rag.db_chunks(business_id)
    live = live_chunks(config) if live is None else live
    live_set = {c.strip() for c in live}
    changed = [c for c in chunks if c.strip() not in live_set]
    draft_titles = {_title(c) for c in chunks}
    removed = sorted({_title(c) for c in live} - draft_titles - {""})
    return changed, removed


def suggest_questions(config, changed, removed=()):
    """Up to MAX_QUESTIONS customer questions the changed sections answer."""
    from llm import _create, as_data, text_of, _note_usage
    if not changed and not removed:
        return []
    shown = "\n\n".join(changed[:3])
    gone = ", ".join(removed[:3])
    prompt = (
        f"A business owner edited the sections below of the knowledge base "
        f"their customer-service assistant answers from. Write up to "
        f"{MAX_QUESTIONS - 1} short questions a customer might send that "
        f"these sections answer, the way a customer would type them. Prefer "
        f"questions where a detail (a price, a condition, a time limit) "
        f"matters to the answer."
        + (f" Also write one question about this removed topic: {gone}." if gone else "")
        + " One question per line, nothing else.\n\n"
        + as_data(shown, tag="sections"))
    try:
        response = _create(max_tokens=250,
                           messages=[{"role": "user", "content": prompt}])
        _note_usage(response, "preview questions")
        return parse_questions(text_of(response))
    except Exception as e:
        log.warning("Couldn't suggest preview questions: %s", e)
        return []


def build_draft(config, business_id):
    """Build the throwaway index from the unpublished sections. Returns its
    name, or None if there are no sections."""
    import rag
    chunks, ids, metas = rag.db_chunks(business_id)
    if not chunks:
        return None
    name = draft_collection_name(config, business_id)
    client = rag.get_chroma_client()
    try:
        client.delete_collection(name)
    except Exception:
        pass
    collection = client.get_or_create_collection(
        name=name, metadata={"hnsw:space": "cosine"},
        embedding_function=rag._embedding_fn)
    collection.add(documents=chunks, ids=ids, metadatas=metas)
    return name


def drop_draft(name):
    import rag
    if not name:
        return
    try:
        rag.get_chroma_client().delete_collection(name)
    except Exception as e:
        log.warning("Couldn't delete preview index %s: %s", name, e)


def _answer(question, config):
    """One reply, pinned (temperature 0) like the evals: unpinned, "Now" and
    "After" would differ in wording when nothing that matters changed."""
    from llm import get_llm_reply
    try:
        return get_llm_reply(question, [], config, channel="webchat",
                             temperature=0)
    except Exception as e:
        log.warning("Preview answer failed: %s", e)
        return f"(Couldn't get an answer: {type(e).__name__})"


def run_preview(config, business_id, questions):
    """[{question, now, after, changed}] for each question."""
    questions = list(questions)[:MAX_QUESTIONS]
    draft = None
    try:
        draft = build_draft(config, business_id)
        draft_config = copy.deepcopy(config)
        if draft:
            draft_config.setdefault("business", {})["collection"] = draft
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            now = [pool.submit(_answer, q, config) for q in questions]
            after = [pool.submit(_answer, q, draft_config) for q in questions]
            results = []
            for q, n, a in zip(questions, now, after):
                n, a = n.result(), a.result()
                results.append({"question": q, "now": n, "after": a,
                                "changed": " ".join(n.split()) != " ".join(a.split())})
        return results
    finally:
        drop_draft(draft)

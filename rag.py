# rag.py — document ingestion and semantic retrieval.
#
# Two phases:
#
#   Ingestion (run once per business, offline):
#     python3 rag.py
#     Reads Markdown files from the business's documents folder, splits them
#     into chunks, embeds them, and stores them in ChromaDB.
#
#   Retrieval (called per-request, online):
#     retrieve(query, config) -> [(chunk_text, distance), ...]
#     Embeds the query, finds the closest chunks, filters by distance threshold.
#
# Config is passed explicitly so the same module can serve any business.
# The ChromaDB client is shared (one per server); collections are opened
# per-call so stale handles after re-ingestion are impossible.

import re
import chromadb
from pathlib import Path
import os
import chromadb.utils.embedding_functions as embedding_functions

import logging
log = logging.getLogger("rag")

# Embedding backend selection.
#
# Chroma's default embedder runs a local ONNX model — fine on a laptop,
# unusable on a 0.1-CPU container (query embedding exceeded a 300s timeout
# in production). Using a hosted embedding API turns that CPU-bound work
# into a fast network call and removes ~200MB of runtime memory plus a
# 79MB model download on every cold start.
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY")

if VOYAGE_API_KEY:
    _embedding_fn = embedding_functions.VoyageAIEmbeddingFunction(
        api_key=VOYAGE_API_KEY,
        model_name="voyage-3-large",
    )
    log.info("Using Voyage AI embeddings.")
else:
    # Fallback: Chroma's local default. Works locally; too slow to deploy.
    _embedding_fn = None
    log.warning("VOYAGE_API_KEY not set — falling back to local "
          "ONNX embeddings. Slow, and NOT compatible with collections "
          "built using Voyage (different vector dimensions).")

# ---------------------------------------------------------------------------
# Constants — not business-specific, safe at module level
# ---------------------------------------------------------------------------

CHROMA_DB_PATH     = Path("chroma_db")
CHUNK_SIZE         = 500
CHUNK_OVERLAP      = 50
TOP_K              = 4
# How far a chunk may be and still be handed to the model.
#
# Measured by rag_calibrate.py over 117 questions across all five
# businesses, 2026-09-22:
#
#   the right section, on questions that have one   n=99  median 0.327
#                                                         p90 0.414  max 0.547
#   the closest section, on questions that don't    n=16  median 0.477
#                                                         p90 0.504  max 0.511
#
# Read those two ranges together: they overlap from 0.383 to 0.547. There
# is no threshold that keeps every right section and blocks every wrong
# one, and the shape of the curve is worse than that — at 0.50, 88% of
# out-of-scope questions already get a chunk; at 0.55, all of them do.
#
# So this number is a weak filter, not a safety mechanism. What actually
# stops a wrong answer is the assistant declining to invent one, and that
# is what the DECLINE questions in rag_eval measure.
#
# It was briefly 0.55, on a measurement of FOUR out-of-scope questions
# that made raising it look free. Twelve more showed what it cost: asked
# "do you make cookies", the assistant went from "I'm not sure about
# cookies specifically" to "We sure do!" — inventing a product, because
# the daily-pastries chunk crossed the line and reads like a yes. One
# customer arriving for cookies that don't exist is worse than one being
# asked to phone about change orders, so 0.50 it is.
#
# The right section that 0.50 throws away is Ridgeline's change-order
# question at 0.547. That one is fixed where it belongs — in the section's
# Topics line — rather than by moving a number that governs all five
# businesses to rescue one query.
DISTANCE_THRESHOLD = 0.50

# Created lazily rather than at import: gunicorn forks worker processes, and
# ChromaDB's Rust-backed client is not fork-safe — a client created before the
# fork deadlocks when the child first touches it.
_chroma_client = None


def get_chroma_client():
    """Return the ChromaDB client, creating it in this process if needed."""
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
    return _chroma_client


# ---------------------------------------------------------------------------
# Slug utility
# ---------------------------------------------------------------------------

def _slugify(name):
    """Make a filesystem-safe identifier from a business name.

    Used for both the documents folder name and the ChromaDB collection name
    so they always match. Single source of truth for the naming rule.

    "Bob's Plumbing"        -> "bobs_plumbing"
    "Sunrise Bakery & Café" -> "sunrise_bakery_and_cafe"
    "Crosstown Pizza Co."   -> "crosstown_pizza_co"

    Note the last one: this is a GUESS at an identifier, derived from a name
    a human wrote for other humans. It used to leave the trailing period on
    ("crosstown_pizza_co."), which is not a valid anything — and because the
    business is actually registered as "crosstown_pizza", the guess named a
    collection that had never existed. Retrieval came back empty for every
    question, which reads exactly like a knowledge base with nothing
    relevant in it rather than a lookup in the wrong place.

    Stripping the punctuation makes the guess tidier. It does not make it
    right: a name and a slug are different things, and the slug is the one
    that should be stated rather than inferred. See collection_for.
    """
    import re
    slug = (
        name.lower()
        .replace("'", "")
        .replace("&", "and")
        .replace("é", "e")
        .replace(" ", "_")
    )
    return re.sub(r"[^a-z0-9_]", "", slug).strip("_")


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text):
    """Split a Markdown document into semantically bounded chunks.

    Splits on Markdown headings so each chunk is a coherent section rather
    than an arbitrary character-count slice. Filters out heading-only orphan
    chunks using a structural check (real content has more than one non-empty
    line). Long sections are further split with a sliding window.
    """
    raw_chunks = re.split(r"(?=^#{1,6}\s)", text, flags=re.MULTILINE)

    def has_content_beyond_heading(chunk):
        """True if the chunk has body content, not just a heading line."""
        non_empty = [line for line in chunk.split("\n") if line.strip()]
        return len(non_empty) > 1

    chunks = []
    for raw in raw_chunks:
        raw = raw.strip()
        if not raw:
            continue
        if not has_content_beyond_heading(raw):
            continue

        if len(raw) <= CHUNK_SIZE:
            chunks.append(raw)
            continue

        # A section too long for one chunk used to be sliced by character
        # count, which produced a chunk beginning "change to the" — no
        # heading, no context, starting mid-sentence. Retrieval could return
        # it and the model would be reading a fragment with nothing to say
        # what it was about. It also broke the measurement: rag_eval reads a
        # chunk's section from its first line, so a headless continuation
        # counted as the wrong section even when the right one came back.
        #
        # So: split on line boundaries, and repeat the heading on every
        # piece. Each chunk stands on its own, which is the whole point of
        # chunking on headings in the first place.
        lines   = raw.split("\n")
        heading = lines[0] if lines[0].lstrip().startswith("#") else ""
        body    = "\n".join(lines[1:]) if heading else raw
        budget  = max(80, CHUNK_SIZE - len(heading) - len(" (continued)") - 1)

        pieces, current = [], []
        for line in body.split("\n"):
            # A single line longer than the budget has no boundary to use;
            # fall back to slicing that line and only that line.
            if len(line) > budget:
                if current:
                    pieces.append("\n".join(current))
                    current = []
                for start in range(0, len(line), budget - CHUNK_OVERLAP):
                    pieces.append(line[start:start + budget])
                continue
            if current and len("\n".join(current + [line])) > budget:
                pieces.append("\n".join(current))
                current = [current[-1], line]      # one line of overlap
            else:
                current.append(line)
        if current:
            pieces.append("\n".join(current))

        for index, piece in enumerate(pieces):
            piece = piece.strip()
            if not piece:
                continue
            if heading:
                label = heading if index == 0 else f"{heading} (continued)"
                chunks.append(f"{label}\n{piece}")
            else:
                chunks.append(piece)

    return chunks


# ---------------------------------------------------------------------------
# Ingestion — run offline: python3 rag.py
# ---------------------------------------------------------------------------

def collection_for(config):
    """The ChromaDB collection this business's knowledge lives in.

    Prefers the collection name stamped on the config by load_config, then
    the immutable slug, then slugifies the business name for callers that
    load a YAML file directly with no business_id — the offline scripts.

    The collection is a separate column from the slug because two businesses
    can legitimately share one: a demo clone reads its template's knowledge
    base until the visitor edits it, which is what makes cloning a tenant
    cost nothing in embedding calls.

    Why this matters: the business NAME is owner-editable. Keying a
    collection on it meant that renaming a business in settings pointed
    retrieval at a collection that had never existed, and the failure was
    silent — no exception, just an empty result list and a bot that quietly
    stopped knowing anything. The slug is a database column nobody can edit.
    """
    business = config.get("business", {})
    return (business.get("collection")
            or business.get("slug")
            or _slugify(business.get("name", "")))


def ingest_documents(config, business_id=None):
    """Rebuild a business's vector collection from its document sections.

    Builds into a temporary collection and swaps it in only after every
    embedding call succeeds. The naive delete-then-rebuild left the business
    with an empty knowledge base whenever the embedding API failed partway —
    and the caller had no way to tell that from a clean failure.
    """
    slug            = collection_for(config)
    collection_name = slug
    temp_name       = f"{slug}__building"

    log.info(f"Using collection: {collection_name}")

    chunks = []
    ids    = []
    metas  = []

    if business_id is not None:
        from db import get_documents
        sections = get_documents(business_id)
        log.info(f"Ingesting {len(sections)} section(s) from database")
        for s in sections:
            chunks.append(f"## {s['title']}\n{s['body']}")
            ids.append(f"section_{s['id']}")
            metas.append({"source": "db", "section_id": s["id"],
                          "title": s["title"]})
    else:
        docs_path = Path("documents") / slug
        log.info(f"Ingesting documents from {docs_path}...")
        if not docs_path.exists():
            log.error(f"Documents folder not found: {docs_path}")
            return None
        for md_file in sorted(docs_path.glob("*.md")):
            text = md_file.read_text(encoding="utf-8")
            file_chunks = chunk_text(text)
            log.info(f"{md_file.name}: {len(file_chunks)} chunks")
            for i, c in enumerate(file_chunks):
                chunks.append(c)
                ids.append(f"{md_file.stem}_chunk_{i}")
                metas.append({"source": md_file.name, "chunk_index": i})

    if not chunks:
        log.warning(f"No content to ingest for '{collection_name}'")
        return None

    client = get_chroma_client()

    # Clear any temp collection left behind by a previous failure.
    try:
        client.delete_collection(temp_name)
    except Exception:
        pass

    temp = client.get_or_create_collection(
        name=temp_name,
        metadata={"hnsw:space": "cosine"},
        embedding_function=_embedding_fn,
    )

    try:
        temp.add(documents=chunks, ids=ids, metadatas=metas)
    except Exception:
        # Leave the live collection untouched — it's still serving the
        # previous content, which is exactly what we tell the owner.
        try:
            client.delete_collection(temp_name)
        except Exception:
            pass
        raise

    # Every embedding succeeded. Now swap.
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass

    # Stamp what this was built from. Without it ensure_ingested has only
    # the chunk count to go on, which cannot see an edit that leaves the
    # count alone. Absent for a DB-sourced collection, which is the right
    # answer: nothing about the seed files describes it.
    live_metadata = {"hnsw:space": "cosine"}
    docs_path = Path("documents") / slug
    if business_id is None and docs_path.exists():
        live_metadata["source_digest"] = _source_digest(docs_path)

    live = client.get_or_create_collection(
        name=collection_name,
        metadata=live_metadata,
        embedding_function=_embedding_fn,
    )
    live.add(documents=chunks, ids=ids, metadatas=metas)
    client.delete_collection(temp_name)

    log.info(f"Ingested {len(chunks)} chunks into '{collection_name}'.")
    return live

def _source_digest(docs_path):
    """A fingerprint of the seed documents, for spotting an edited one.

    Chunk count was the old freshness test, and it only notices changes that
    add or remove a chunk. Edit a heading, correct a price, widen a Topics
    line — the count is identical, the collection is declared complete, and
    the change never reaches retrieval. Nothing errors and nothing looks
    wrong; the assistant just keeps answering from the previous version.

    (Found exactly that way: a Topics line was widened to fix a retrieval
    miss, the boot said "complete — skipping", and the measured distance
    came back to three decimal places unchanged.)
    """
    import hashlib
    digest = hashlib.sha256()
    for md_file in sorted(docs_path.glob("*.md")):
        digest.update(md_file.name.encode("utf-8"))
        digest.update(md_file.read_bytes())
    return digest.hexdigest()[:16]


def ensure_ingested(config):
    """Ingest this business's documents if the collection is missing or stale.

    Safe to call on every server boot. On a persistent filesystem this is a
    fast no-op; on an ephemeral one (cloud free tiers) it rebuilds the vector
    store automatically after a restart.
    """
    slug      = collection_for(config)
    docs_path = Path("documents") / slug

    expected = 0
    digest   = None
    if docs_path.exists():
        for md_file in docs_path.glob("*.md"):
            expected += len(chunk_text(md_file.read_text(encoding="utf-8")))
        digest = _source_digest(docs_path)

    try:
        collection = get_chroma_client().get_collection(
            slug,
            embedding_function=_embedding_fn,
        )
        count  = collection.count()
        stored = (collection.metadata or {}).get("source_digest")
        if count == expected and count > 0 and stored == digest:
            log.info(f"Collection '{slug}' complete ({count} chunks) — skipping.")
            return
        if count == expected and count > 0 and stored != digest:
            log.info(f"Collection '{slug}' has the right number of chunks but "
                     f"the documents have changed — rebuilding.")
        else:
            log.info(f"Collection '{slug}' has {count} chunks, expected "
                     f"{expected} — rebuilding.")
    except Exception:
        pass

    ingest_documents(config)

# ---------------------------------------------------------------------------
# Retrieval — called per-request from llm.py
# ---------------------------------------------------------------------------

    
class RetrievalUnavailable(Exception):
    """The knowledge base couldn't be searched (embedding API down, rate
    limited, bad key). Distinct from "searched and found nothing": an empty
    result means the documents don't cover it, this means we don't know."""


def retrieve(query, config):
    """Return relevant document chunks for a query, scoped to the given business.

    Returns a list of (chunk_text, distance) tuples, closest first, filtered by
    DISTANCE_THRESHOLD. Returns [] if nothing is close enough or if the
    collection hasn't been ingested yet.
    """
    collection_name = collection_for(config)
    log.info(f"Opening collection '{collection_name}'...")

    try:
        collection = get_chroma_client().get_collection(
            collection_name,
            embedding_function=_embedding_fn,
        )
    except Exception as e:
        log.warning(f"Collection '{collection_name}' not found: {e}")
        return []

    log.debug("Collection opened. Embedding query and searching...")
    try:
        # Embedding the query is a network call to Voyage. It was the one
        # unguarded call on the question path: on 2026-09-25 it failed on
        # Render, the exception reached Flask, and the chat widget showed
        # "couldn't reach the assistant" for every question while bookings
        # (which never search) kept working.
        results = collection.query(query_texts=[query], n_results=TOP_K)
    except Exception as e:
        log.exception("Knowledge-base search failed for '%s'", collection_name)
        raise RetrievalUnavailable(f"{type(e).__name__}: {e}") from e
    log.debug(f"Search returned {len(results['documents'][0])} raw results.")

    documents = results["documents"][0]
    distances = results["distances"][0]

    filtered = [
        (doc, dist)
        for doc, dist in zip(documents, distances)
        if dist <= DISTANCE_THRESHOLD
    ]

    log.info("Retrieval: %d usable chunk(s) of %d returned",
             len(filtered), len(documents))
    log.debug("Query was: %r", query)
    for doc, dist in filtered:
        preview = doc[:80] + ("..." if len(doc) > 80 else "")
        log.debug(f"distance={dist:.4f}  {preview}")

    return filtered


# ---------------------------------------------------------------------------
# Standalone ingestion script
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Ingest documents for ALL registered businesses in one run.
    # No more editing this file to switch targets.
    #
    # Usage:
    #   python3 rag.py                  -> ingest every active business
    #   python3 rag.py <config_path>    -> ingest just one (old behavior)

    import sys
    from dotenv import load_dotenv
    load_dotenv()

    from config import load_config

    if len(sys.argv) > 1:
        # Single-business mode: path given on the command line.
        config_paths = [sys.argv[1]]
    else:
        # All-business mode: read paths from the businesses table.
        from db import get_all_businesses
        businesses = get_all_businesses()
        config_paths = [b["config_path"] for b in businesses if b["active"]]
        if not config_paths:
            print("[rag] No businesses registered. Run seed_businesses.py first.")
            exit(1)

    for path in config_paths:
        print(f"\n{'='*60}")
        config = load_config(path)
        ingest_documents(config)

    print(f"\n[rag] Done — ingested {len(config_paths)} business(es).")
    print("[rag] Restart the Flask server if it was running during ingestion.")
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
    print("[rag] Using Voyage AI embeddings.")
else:
    # Fallback: Chroma's local default. Works locally; too slow to deploy.
    _embedding_fn = None
    print("[rag] WARNING: VOYAGE_API_KEY not set — falling back to local "
          "ONNX embeddings. Slow, and NOT compatible with collections "
          "built using Voyage (different vector dimensions).")

# ---------------------------------------------------------------------------
# Constants — not business-specific, safe at module level
# ---------------------------------------------------------------------------

CHROMA_DB_PATH     = Path("chroma_db")
CHUNK_SIZE         = 500
CHUNK_OVERLAP      = 50
TOP_K              = 4
# Tuned against real queries after switching to Voyage embeddings, which
# compress the distance range compared to the previous local model — the
# old 0.85 threshold let everything through. Measured separation on Bob's
# Plumbing: answerable queries 0.23–0.41, irrelevant 0.48–0.58.
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
    """
    return (
        name.lower()
        .replace("'", "")
        .replace("&", "and")
        .replace("é", "e")
        .replace(" ", "_")
    )


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
        else:
            start = 0
            while start < len(raw):
                end = start + CHUNK_SIZE
                chunks.append(raw[start:end])
                start += CHUNK_SIZE - CHUNK_OVERLAP

    return chunks


# ---------------------------------------------------------------------------
# Ingestion — run offline: python3 rag.py
# ---------------------------------------------------------------------------

def ingest_documents(config, business_id=None):
    """Rebuild a business's vector collection from its document sections.

    Reads from the documents table when a business_id is given; falls back
    to the Markdown files otherwise, so the standalone ingest script and
    onboarding still work before anything has been imported.
    """
    slug            = _slugify(config["business"]["name"])
    collection_name = slug

    print(f"[rag] Using collection: {collection_name}")

    try:
        get_chroma_client().delete_collection(collection_name)
    except Exception:
        pass

    collection = get_chroma_client().get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
        embedding_function=_embedding_fn,
    )

    chunks = []
    ids    = []
    metas  = []

    if business_id is not None:
        from db import get_documents
        sections = get_documents(business_id)
        print(f"[rag] Ingesting {len(sections)} section(s) from database")
        for i, s in enumerate(sections):
            # One chunk per section — boundaries are exactly what the owner
            # defined, rather than inferred from a regex over free text.
            chunks.append(f"## {s['title']}\n{s['body']}")
            ids.append(f"section_{s['id']}")
            metas.append({"source": "db", "section_id": s["id"],
                          "title": s["title"]})
    else:
        docs_path = Path("documents") / slug
        print(f"[rag] Ingesting documents from {docs_path}...")
        if not docs_path.exists():
            print(f"[rag] ERROR: documents folder not found: {docs_path}")
            return None
        for md_file in sorted(docs_path.glob("*.md")):
            text = md_file.read_text(encoding="utf-8")
            file_chunks = chunk_text(text)
            print(f"[rag] {md_file.name}: {len(file_chunks)} chunks")
            for i, c in enumerate(file_chunks):
                chunks.append(c)
                ids.append(f"{md_file.stem}_chunk_{i}")
                metas.append({"source": md_file.name, "chunk_index": i})

    if not chunks:
        print(f"[rag] WARNING: no content to ingest for '{collection_name}'")
        return collection

    collection.add(documents=chunks, ids=ids, metadatas=metas)
    print(f"[rag] Ingested {len(chunks)} chunks into '{collection_name}'.")
    return collection

def ensure_ingested(config):
    """Ingest this business's documents only if its collection is empty.

    Safe to call on every server boot. On a persistent filesystem this is a
    fast no-op; on an ephemeral one (cloud free tiers) it rebuilds the vector
    store automatically after a restart.
    """
    slug      = _slugify(config["business"]["name"])
    docs_path = Path("documents") / slug

    # Expected chunk count from the source documents. Comparing against this
    # (rather than just count > 0) catches partially-ingested collections,
    # which happen when ingestion fails midway — e.g. an embedding API rate limit.
    expected = 0
    if docs_path.exists():
        for md_file in docs_path.glob("*.md"):
            expected += len(chunk_text(md_file.read_text(encoding="utf-8")))

    try:
        collection = get_chroma_client().get_collection(
            slug,
            embedding_function=_embedding_fn,
        )
        count = collection.count()
        if count == expected and count > 0:
            print(f"[rag] Collection '{slug}' complete ({count} chunks) — skipping.")
            return
        print(f"[rag] Collection '{slug}' has {count} chunks, expected {expected} "
              f"— rebuilding.")
    except Exception:
        pass

    ingest_documents(config)

# ---------------------------------------------------------------------------
# Retrieval — called per-request from llm.py
# ---------------------------------------------------------------------------

    
def retrieve(query, config):
    """Return relevant document chunks for a query, scoped to the given business.

    Returns a list of (chunk_text, distance) tuples, closest first, filtered by
    DISTANCE_THRESHOLD. Returns [] if nothing is close enough or if the
    collection hasn't been ingested yet.
    """
    collection_name = _slugify(config["business"]["name"])
    print(f"[rag] Opening collection '{collection_name}'...")

    try:
        collection = get_chroma_client().get_collection(
            collection_name,
            embedding_function=_embedding_fn,
        )
    except Exception as e:
        print(f"[rag] WARNING: collection '{collection_name}' not found: {e}")
        return []

    print("[rag] Collection opened. Embedding query and searching...")
    results = collection.query(query_texts=[query], n_results=TOP_K)
    print(f"[rag] Search returned {len(results['documents'][0])} raw results.")

    documents = results["documents"][0]
    distances = results["distances"][0]

    filtered = [
        (doc, dist)
        for doc, dist in zip(documents, distances)
        if dist <= DISTANCE_THRESHOLD
    ]

    print(f"[rag] Query: {query!r} -> {len(filtered)} usable chunks "
          f"(of {len(documents)} returned)")
    for doc, dist in filtered:
        preview = doc[:80] + ("..." if len(doc) > 80 else "")
        print(f"        distance={dist:.4f}  {preview}")

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
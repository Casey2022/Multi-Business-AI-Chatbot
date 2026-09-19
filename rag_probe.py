#!/usr/bin/env python3
"""rag_probe.py — what came back, and how close it was.

    python3 rag_probe.py ridgeline_contracting "what happens if I change my mind"
    python3 rag_probe.py belmont_hair_studio "I want to go lighter" --top 8

retrieve() applies DISTANCE_THRESHOLD and hands back what survived, so a
query that finds nothing and a query whose best match sat just over the
line look identical from the outside: both are []. That distinction is the
whole diagnosis. A near miss is a vocabulary problem in the document, and
the fix is the section's Topics line. A distant miss means the knowledge
base genuinely doesn't cover it, and the fix is to write the section.

This prints every candidate with its distance and says which side of the
threshold it fell on, so the choice is made on a number rather than a
hunch.
"""

import sys
from dotenv import load_dotenv
load_dotenv()

import rag
from config import load_config


def config_for(slug):
    """Resolve the way the app does — through the registered business."""
    try:
        from db import get_business_by_slug
        business = get_business_by_slug(slug)
    except Exception:
        business = None
    if business:
        return load_config(business["config_path"], business["id"])
    return load_config(f"config/{slug}.yaml")


def main():
    if len(sys.argv) < 3:
        print(__doc__.strip())
        return 1

    slug, query = sys.argv[1], sys.argv[2]
    top = 6
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])

    config     = config_for(slug)
    collection = rag.collection_for(config)
    client     = rag.get_chroma_client()

    try:
        store = client.get_collection(collection,
                                      embedding_function=rag._embedding_fn)
    except Exception as e:
        print(f"No collection {collection!r}: {e}")
        return 1

    found = store.query(query_texts=[query], n_results=top)
    docs      = found["documents"][0]
    distances = found["distances"][0]

    print(f"\n{slug} · collection {collection!r} · threshold "
          f"{rag.DISTANCE_THRESHOLD}")
    print(f"query: {query!r}\n")
    print(f"  {'distance':>8}  {'':3} section")
    print(f"  {'-' * 8}  {'-' * 3} {'-' * 52}")
    for doc, distance in zip(docs, distances):
        heading = doc.splitlines()[0].strip("# ").strip()
        side = "in " if distance <= rag.DISTANCE_THRESHOLD else "out"
        print(f"  {distance:8.3f}  {side} {heading[:52]}")

    best = min(distances) if distances else None
    print()
    if best is None:
        print("  nothing in the collection at all.")
    elif best <= rag.DISTANCE_THRESHOLD:
        print(f"  best {best:.3f} — retrieved.")
    else:
        margin = best - rag.DISTANCE_THRESHOLD
        print(f"  best {best:.3f}, which is {margin:.3f} over the threshold.")
        print(f"  Under ~0.05 over: a vocabulary gap — the section is right but")
        print(f"  the customer's words aren't in it. Widen its Topics line.")
        print(f"  Well over: the knowledge base doesn't cover this. Write it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

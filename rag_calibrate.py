#!/usr/bin/env python3
"""rag_calibrate.py — set the distance threshold from evidence, not a hunch.

    python3 rag_calibrate.py

DISTANCE_THRESHOLD decides what retrieval hands to the model. Too tight and
the right section is thrown away; too loose and the model answers from
something irrelevant. It is one global number, and its comment in rag.py
says it was measured on Bob's Plumbing alone: answerable 0.23-0.41,
irrelevant 0.48-0.58. Five businesses now, and 105 questions that already
say which ones the knowledge base can answer.

So measure it. For every question that has a right answer, this records how
far away the RIGHT section was. For every question whose right answer is
"we don't do that", it records how far away the CLOSEST section was —
anything admitted there is the model being handed something irrelevant.

Those two distributions are the whole problem. Where they don't overlap,
any threshold between them works. Where they do, the threshold is a choice
about which mistake you prefer, and this prints the cost of each so the
choice is made with the numbers visible.

Uses embeddings but no chat calls — cents, not dollars.
"""

import sys
from dotenv import load_dotenv
load_dotenv()

import rag
from rag_eval import TESTS, HARD, DECLINE, config_for, heading_matches

TOP = 10


def gather():
    """Return (answerable, unanswerable) distance samples."""
    answerable, unanswerable = [], []

    for slug in sorted(set(TESTS) | set(HARD)):
        config = config_for(slug)
        name   = rag.collection_for(config)
        try:
            store = rag.get_chroma_client().get_collection(
                name, embedding_function=rag._embedding_fn)
        except Exception as e:
            print(f"  skipping {slug}: {e}")
            continue

        rows = list(TESTS.get(slug, [])) + list(HARD.get(slug, []))
        for question, expected_chunk, expected_fact in rows:
            found = store.query(query_texts=[question], n_results=TOP)
            docs      = found["documents"][0]
            distances = found["distances"][0]
            if not docs:
                continue
            headings = [d.splitlines()[0].strip("# ").strip() for d in docs]

            if expected_chunk not in (None, "ANSWER_ONLY"):
                # How far away was the section that actually holds the answer?
                for heading, distance in zip(headings, distances):
                    if heading_matches([heading], expected_chunk):
                        answerable.append((distance, slug, question))
                        break
            elif expected_fact == DECLINE or expected_chunk is None:
                # Nothing here is a real answer, so the nearest thing is the
                # best case for a wrong admission.
                unanswerable.append((min(distances), slug, question))

    return answerable, unanswerable


def spread(samples):
    values = sorted(d for d, _, _ in samples)
    if not values:
        return None
    def at(fraction):
        return values[min(len(values) - 1, int(len(values) * fraction))]
    return {"n": len(values), "min": values[0], "p50": at(0.50),
            "p90": at(0.90), "max": values[-1]}


def main():
    print("Measuring…")
    answerable, unanswerable = gather()

    good, bad = spread(answerable), spread(unanswerable)
    if not good or not bad:
        print("Not enough samples — is every collection ingested?")
        return 1

    print(f"\n  the RIGHT section, on questions that have one   "
          f"n={good['n']:3}  min {good['min']:.3f}  median {good['p50']:.3f}"
          f"  p90 {good['p90']:.3f}  max {good['max']:.3f}")
    print(f"  the CLOSEST section, on questions that don't    "
          f"n={bad['n']:3}  min {bad['min']:.3f}  median {bad['p50']:.3f}"
          f"  p90 {bad['p90']:.3f}  max {bad['max']:.3f}")

    print(f"\n  threshold   right sections kept   irrelevant admitted")
    print(f"  {'-' * 9}   {'-' * 19}   {'-' * 19}")
    best = None
    for threshold in [x / 100 for x in range(40, 76, 1)]:
        kept    = sum(1 for d, _, _ in answerable   if d <= threshold)
        leaked  = sum(1 for d, _, _ in unanswerable if d <= threshold)
        if threshold * 100 % 5 == 0 or threshold == rag.DISTANCE_THRESHOLD:
            mark = "  <- current" if abs(threshold - rag.DISTANCE_THRESHOLD) < 1e-9 else ""
            print(f"    {threshold:.2f}      {kept:3}/{good['n']} "
                  f"({100*kept/good['n']:3.0f}%)        {leaked:3}/{bad['n']} "
                  f"({100*leaked/bad['n']:3.0f}%){mark}")
        # Prefer keeping the right section; break ties by admitting less.
        score = (kept / good["n"]) - (leaked / bad["n"])
        if best is None or score > best[0] + 1e-9:
            best = (score, threshold, kept, leaked)

    _, threshold, kept, leaked = best
    print(f"\n  Best separation at {threshold:.2f}: keeps {kept}/{good['n']} "
          f"right sections, admits {leaked}/{bad['n']} irrelevant.")
    if leaked:
        print("  Note the leaks are not automatically wrong answers — the")
        print("  assistant still has to decline, and the DECLINE questions in")
        print("  rag_eval measure whether it does.")

    print(f"\n  Right sections that would still be thrown away at {threshold:.2f}:")
    missed = sorted((d, s, q) for d, s, q in answerable if d > threshold)
    for distance, slug, question in missed[:10]:
        print(f"    {distance:.3f}  {slug}: {question[:56]}")
    if not missed:
        print("    none")
    return 0


if __name__ == "__main__":
    sys.exit(main())

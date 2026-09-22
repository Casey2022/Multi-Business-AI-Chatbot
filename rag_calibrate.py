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
    curve = []
    for threshold in [x / 100 for x in range(40, 76, 1)]:
        kept    = sum(1 for d, _, _ in answerable   if d <= threshold)
        leaked  = sum(1 for d, _, _ in unanswerable if d <= threshold)
        if threshold * 100 % 5 == 0 or threshold == rag.DISTANCE_THRESHOLD:
            mark = "  <- current" if abs(threshold - rag.DISTANCE_THRESHOLD) < 1e-9 else ""
            print(f"    {threshold:.2f}      {kept:3}/{good['n']} "
                  f"({100*kept/good['n']:3.0f}%)        {leaked:3}/{bad['n']} "
                  f"({100*leaked/bad['n']:3.0f}%){mark}")
        curve.append((threshold, kept, leaked))

    # The honest recommendation is the DOMINANT one: the loosest threshold
    # that admits no more irrelevant sections than the current setting.
    #
    # An earlier version scored keeping and leaking equally and recommended
    # tightening to 0.44, which would have thrown away three right sections
    # to prevent leaks that have never produced a wrong answer. Weighting
    # two different mistakes the same is a choice, and it was the wrong one:
    # a right section discarded is a question the assistant cannot answer,
    # while an irrelevant one admitted still has to get past the guardrails
    # before it hurts anyone.
    here = next((row for row in curve
                 if abs(row[0] - rag.DISTANCE_THRESHOLD) < 1e-9), None)
    if here:
        dominant = max((row for row in curve if row[2] <= here[2]),
                       key=lambda row: (row[1], row[0]))
        threshold, kept, leaked = dominant
        print(f"\n  Current {rag.DISTANCE_THRESHOLD:.2f}: keeps {here[1]}/{good['n']}"
              f", admits {here[2]}/{bad['n']}.")
        if threshold > rag.DISTANCE_THRESHOLD and kept > here[1]:
            print(f"  {threshold:.2f} keeps {kept}/{good['n']} for the SAME "
                  f"{leaked}/{bad['n']} admitted — strictly better here.")
        elif kept == here[1]:
            print(f"  Nothing looser keeps more without admitting more.")

    print(f"\n  Right sections thrown away at {rag.DISTANCE_THRESHOLD:.2f}:")
    missed = sorted((d, s, q) for d, s, q in answerable
                    if d > rag.DISTANCE_THRESHOLD)
    for distance, slug, question in missed[:10]:
        print(f"    {distance:.3f}  {slug}: {question[:56]}")
    if not missed:
        print("    none")

    if bad["n"] < 15:
        print(f"\n  CAUTION: only {bad['n']} genuinely out-of-scope questions.")
        print("  The 'irrelevant admitted' column is an anecdote at this size,")
        print("  and every argument for loosening rests on it. Add more")
        print("  questions the knowledge base has no section for before")
        print("  trusting a threshold set from this half of the data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

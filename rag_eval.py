# rag_eval.py — hand-scored retrieval and answer accuracy.

# Two separate measures:
#   retrieval — did the expected chunk come back in the top results?
#   answer    — did the reply contain the fact it should have?

# Retrieval is scored automatically against the expected chunk heading.
# Answer correctness needs a human read; this script prints the replies
# and you score them.

# Usage:
#   python3 rag_eval.py bobs_plumbing
#   python3 rag_eval.py sunrise_bakery_and_cafe

import sys
from dotenv import load_dotenv
load_dotenv()

from config import load_config
from rag import retrieve
from llm import get_llm_reply


TESTS = {
    "bobs_plumbing": [
        # (question, expected chunk heading fragment, fact the answer must contain)
        ("how much is drain cleaning",           "Drain Cleaning",      "$150"),
        ("do you do hydro jetting",              "Drain Cleaning",      "$400"),
        ("what does a tankless water heater cost","Water Heater",       "$3,500"),
        ("how long does a hidden leak take",     "Leak Repair",         "1-2 days"),
        ("do you handle septic tanks",           "Septic Tanks",        "not"),
        ("do you do commercial work",            "Septic Tanks",        "not"),
        ("what brands do you service",           "Brands We Service",   "Kohler"),
        ("do you serve Webster",                 "Service Area",        "Webster"),
        ("what if I'm outside your service area","Service Area",        "$50"),
        ("how fast can you get here in an emergency", "Emergency",      "90 minutes"),
        ("is there a fee for after hours",       "Emergency",           "$75"),
        ("what's the warranty on a water heater","Water Heater",        "10-year"),
        # Should retrieve nothing useful:
        ("do you install solar panels",          "ANSWER_ONLY",         "don't"),
        ("what's your favourite colour",         "ANSWER_ONLY",          None),
    ],
    "sunrise_bakery_and_cafe": [
        ("how much is a 6 inch birthday cake",   "Custom Birthday",     "$45"),
        ("how much notice for a custom cake",    "Custom Birthday",     "72 hours"),
        ("what do wedding cakes cost",           "Wedding Cakes",       "$400"),
        ("do you have vegan options",            "Flavors",             "not"),
        ("can I get gluten free",                "Flavors",             "96 hours"),
        ("how much are cupcakes",                "Cupcake Orders",      "$3.50"),
        ("how far in advance for cupcakes",      "Cupcake Orders",      "24 hours"),
        ("what's your cancellation policy",      "Deposits",            "50%"),
        ("do you deliver",                       "Pickup, Delivery",    "5-mile"),
        ("are you open Mondays",                 "Pickup, Delivery",    "closed"),
        ("do you make cookies",                  None,                  None),
    ],
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in TESTS:
        print("Usage: python3 rag_eval.py <business_slug>")
        print("Available:", ", ".join(TESTS))
        return

    slug   = sys.argv[1]
    config = load_config(f"config/{slug}.yaml")
    tests  = TESTS[slug]

    retrieval_pass = 0
    scorable       = 0

    print(f"\n{'=' * 70}")
    print(f"  RAG EVALUATION — {config['business']['name']}")
    print(f"{'=' * 70}\n")

    for i, (question, expected_chunk, expected_fact) in enumerate(tests, 1):
        chunks = retrieve(question, config)
        headings = [c.split("\n")[0].strip("# ") for c, _ in chunks]

        if expected_chunk == "ANSWER_ONLY":
            ok = True          # retrieval isn't the measure here
            verdict = "n/a (answer-scored)"
        elif expected_chunk is None:
            # Negative case — retrieving nothing is the correct behaviour.
            ok = len(chunks) == 0
            verdict = "PASS (correctly empty)" if ok else \
                      f"FAIL (returned {len(chunks)} chunks)"
        else:
            ok = any(expected_chunk.lower() in h.lower() for h in headings)
            verdict = "PASS" if ok else f"FAIL (got: {headings[:2]})"

        retrieval_pass += 1 if ok else 0
        scorable += 1

        reply = get_llm_reply(question, None, config)

        print(f"[{i:>2}] {question}")
        print(f"     retrieval: {verdict}")
        if expected_fact:
            print(f"     expected in answer: {expected_fact!r}")
        print(f"     reply: {reply}")
        print()

    print(f"{'=' * 70}")
    print(f"  Retrieval: {retrieval_pass}/{scorable} "
          f"({100 * retrieval_pass / scorable:.0f}%)")
    print(f"  Answer accuracy: score the replies above by hand.")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    main()
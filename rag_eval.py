#!/usr/bin/env python3
"""rag_eval.py — how often the assistant retrieves the right thing and says it.

Run it:
    python3 rag_eval.py bobs_plumbing      one business
    python3 rag_eval.py --all              every business, with a combined score
    python3 rag_eval.py --all --retrieval  no LLM calls, no cost, retrieval only

Two measures, deliberately separate, because they fail for different
reasons and have different fixes:

  retrieval — did the chunk holding the answer come back in the top results?
              Bad retrieval is fixed by chunking, embeddings, or the
              distance threshold.
  answer    — did the reply actually contain the fact? A right chunk and a
              wrong answer is a prompt problem, not a retrieval problem, and
              the two are worth being able to tell apart.

Answer scoring is a substring check against a fact taken verbatim from the
business's own knowledge base. That is a narrow measure and worth being
honest about: it says nothing about whether the reply was well phrased,
complete, or pleasant. What it does catch is the failure that actually
matters for a receptionist — stating a price, a duration or a policy that
isn't the one in the documents. A number nobody wrote down is the thing a
customer acts on and the business has to honour.

Every expected fact below was read out of documents/<slug>/services.md. If
a document changes, these change with it — a test asserting a price the
business no longer charges is worse than no test, because it passes.
"""

import sys
from dotenv import load_dotenv
load_dotenv()

from config import load_config
from rag import retrieve, collection_for, get_chroma_client
from llm import get_llm_reply, start_turn_accounting, turn_cost_summary


def config_for(slug):
    """Load a business's config the way the running app loads it.

    The app passes a business_id, which stamps the registered collection
    name onto the config. Loading the YAML alone skips that, and
    collection_for then falls back to guessing the collection from the
    business's display name.

    That guess is what made this file report 6% for Crosstown Pizza Co.:
    the name inferred "crosstown_pizza_co", the business is registered as
    "crosstown_pizza", and every query went to a collection that had never
    existed. Retrieval returned nothing, every time, which looks exactly
    like a knowledge base with nothing relevant in it.

    Measuring a path production never takes is worse than not measuring.
    """
    try:
        from db import get_business_by_slug
        business = get_business_by_slug(slug)
    except Exception:
        business = None
    if business:
        return load_config(business["config_path"], business["id"])
    return load_config(f"config/{slug}.yaml")


# The right answer to some questions is "we don't do that". Asserting a
# phrase would be asserting a particular way of saying no, so this checks
# for any of them — the measure is that the assistant declined rather than
# inventing a service the business doesn't offer.
DECLINE = "<<declines>>"

DECLINING = ("don't", "do not", "dont", "we don’t", "not something",
             "not sure", "i'm not sure", "we specialize", "we specialise",
             "no, ", "we only", "just hair", "our menu is")


# (question, expected chunk heading fragment, fact the answer must contain)
#
#   expected chunk  "Some Heading"  that heading should be retrieved
#                   None            retrieving nothing is the right answer
#                   "ANSWER_ONLY"   retrieval isn't the measure here
#   expected fact   "text"          the reply must contain this
#                   ("a", "b")      the reply must contain at least one
#                   None            not scored — printed for reading only
TESTS = {
    "bobs_plumbing": [
        ("how much is drain cleaning",            "Drain Cleaning",    "$150"),
        ("do you do hydro jetting",               "Drain Cleaning",    "$400"),
        ("what does a tankless water heater cost","Water Heater",      "$3,500"),
        ("how long does a hidden leak take",      "Leak Repair",       "1-2 days"),
        ("do you handle septic tanks",            "Septic Tanks",      DECLINE),
        ("do you do commercial work",             "Septic Tanks",      DECLINE),
        ("what brands do you service",            "Brands We Service", "Kohler"),
        ("do you serve Webster",                  "Service Area",      "Webster"),
        ("what if I'm outside your service area", "Service Area",      "$50"),
        ("how fast can you get here in an emergency", "Emergency",     "90 minutes"),
        ("is there a fee for after hours",        "Emergency",         "$75"),
        ("what's the warranty on a water heater", "Water Heater",      "10-year"),
        ("do you install solar panels",           "ANSWER_ONLY",       DECLINE),
        ("what's your favourite colour",          "ANSWER_ONLY",       None),
    ],
    "sunrise_bakery_and_cafe": [
        ("how much is a 6 inch birthday cake",    "Custom Birthday",   "$45"),
        ("how much notice for a custom cake",     "Custom Birthday",   "72 hours"),
        ("what do wedding cakes cost",            "Wedding Cakes",     "$400"),
        ("do you have vegan options",             "Flavors",           None),
        ("can I get gluten free",                 "Flavors",           "96 hours"),
        ("how much are cupcakes",                 "Cupcake Orders",    "$3.50"),
        ("how far in advance for cupcakes",       "Cupcake Orders",    "24 hours"),
        ("what's your cancellation policy",       "Deposits",          "50%"),
        ("do you deliver",                        "Pickup, Delivery",  ("5-mile", "5 miles")),
        ("are you open Mondays",                  "Pickup, Delivery",  "closed"),
        ("do you make cookies",                   "ANSWER_ONLY",       DECLINE),
    ],
    "belmont_hair_studio": [
        ("how much is a haircut",                 "Haircuts",          "$48"),
        ("do you do kids cuts and how much",      "Haircuts",          "$25"),
        ("what's a blowout cost",                 "Blowouts",          "$35"),
        ("how much for an updo for a wedding",    "Blowouts",          "$75"),
        ("what does root touch up cost",          "Colour",            "$85"),
        ("how long does colour take",             "Colour",            ("two hours", "2 hours")),
        ("price for a cut and colour together",   "Cut and Colour",    "$145"),
        ("how much is balayage",                  "Balayage",          "$185"),
        ("how long does balayage take",           "Balayage",          ("three hours", "3 hours")),
        ("do I need a patch test",                "Patch Tests",       "48 hours"),
        ("who are your stylists",                 "Stylists",          ("Dana", "Marcus", "Priya")),
        ("what happens if I cancel late",         "Cancellation",      "50%"),
        ("is there a deposit for colour",         "Cancellation",      "$30"),
        ("what time is the last colour appointment", "Payment and Hours", "3pm"),
        ("are you open on Sunday",                "Payment and Hours", "closed"),
        ("what products do you use",              "Products",          ("Davines", "Olaplex")),
        # Nothing in the knowledge base covers this.
        ("do you do nails",                       "ANSWER_ONLY",       DECLINE),
    ],
    "ridgeline_contracting": [
        ("is the estimate free",                  "How Estimates Work", "free"),
        ("how long until I get the quote",        "How Estimates Work", "three business days"),
        ("what does a kitchen remodel cost",      "Kitchen Remodels",   ("$18,000", "$75,000")),
        ("how long does a kitchen take",          "Kitchen Remodels",   ("five to eight", "5 to 8")),
        ("what about a bathroom",                 "Bathroom Remodels",  ("$12,000", "$38,000")),
        ("how much is a composite deck per square foot", "Decks",       ("$55", "$80")),
        ("do you pull the permit",                ("Decks", "Licensing"), "permit"),
        ("can you finish my basement if it gets water", "Basement",     ("will not", "won't")),
        ("what does a roof cost",                 "Roofing",            ("$9,000", "$22,000")),
        ("do you do small repairs",               "General Repairs",    "$350"),
        ("are you licensed and insured",          "Licensing",          ("licensed", "insured")),
        ("do you want all the money up front",    "Payment",            ("do not", "don't", "stages")),
        ("how far do you travel",                 "Service Area",       "35 miles"),
        ("what's the warranty",                   "Warranty",           "two years"),
        # A contractor who quotes a firm price in chat is the failure mode
        # this business exists to demonstrate, so ask for one directly.
        ("just give me a price for my kitchen right now", "ANSWER_ONLY", None),
    ],
    "crosstown_pizza": [
        ("how much is a large pizza",             "Pizza Sizes",        "$22"),
        ("what sizes do you have",                "Pizza Sizes",        ("10-inch", "18-inch")),
        ("how much are toppings on a medium",     "Pizza Sizes",        "$2.25"),
        ("do you have gluten free crust",         ("Pizza Sizes", "Allergens"), "$3"),
        ("what's on the topping list",            "Toppings",           ("pepperoni", "mushroom")),
        ("do you have vegan cheese",              "Toppings",           "$2"),
        ("how much are 20 wings",                 "Wings",              "$22"),
        ("what wing sauces do you have",          "Wings",              ("honey garlic", "garlic parm")),
        ("how much is a sub",                     "Subs and Salads",    "$13"),
        ("do you do party trays",                 "Party Trays",        ("$45", "$52", "$60")),
        ("how much notice for a party tray",      "Party Trays",        "three hours"),
        ("how far do you deliver",                "Delivery",           "6 miles"),
        ("what's the delivery minimum",           "Delivery",           "$15"),
        ("how long does delivery take",           "Delivery",           ("30 to 45", "30-45")),
        ("are you open Monday",                   "Hours",              "closed"),
        ("what time do you stop taking orders",   "Hours",              "9:45"),
        ("is the gluten free safe for celiac",    "Allergens",          ("cannot", "can't", "shared")),
        ("do you sell ice cream",                 "ANSWER_ONLY",        DECLINE),
    ],
}


def preflight(slugs):
    """Refuse to score against collections that aren't there.

    A retrieval percentage is only meaningful if the lookup reached the
    right place. An empty or missing collection produces a very low number
    that reads like bad chunking and sends you off tuning the wrong layer
    for an afternoon. Checked before any question runs, for the same reason
    validate_tests is: a wrong answer about why is worse than no answer.
    """
    problems = []
    try:
        client = get_chroma_client()
        existing = {c.name if hasattr(c, "name") else str(c)
                    for c in client.list_collections()}
    except Exception as e:
        return [f"couldn't open the vector store: {e}"]

    for slug in slugs:
        wanted = collection_for(config_for(slug))
        if wanted not in existing:
            problems.append(
                f"{slug}: looks for collection {wanted!r}, which doesn't exist. "
                f"Present: {sorted(existing)}")
            continue
        # A collection that exists but holds nothing fails the same way and
        # is just as invisible: every query returns [].
        try:
            if client.get_collection(wanted).count() == 0:
                problems.append(f"{slug}: collection {wanted!r} is empty — "
                                f"run the ingest before scoring it")
        except Exception as e:
            problems.append(f"{slug}: couldn't count {wanted!r}: {e}")
    return problems


def validate_tests():
    """Check every expected heading and fact against the actual documents.

    A test asserting a price the business no longer charges is worse than no
    test: it fails on every run, gets read as noise, and eventually the whole
    suite gets ignored. Renaming a section or editing a price in
    documents/<slug>/services.md should break this loudly and immediately,
    not quietly lower the score.

    Run before every evaluation rather than behind a flag, because a check
    you have to remember to run is a check that doesn't happen.
    """
    import re
    from pathlib import Path

    problems = []
    for slug, rows in TESTS.items():
        path = Path("documents") / slug / "services.md"
        if not path.exists():
            problems.append(f"{slug}: no documents/{slug}/services.md")
            continue
        document = path.read_text(encoding="utf-8")
        lowered  = document.lower()
        headings = [h.strip() for h in re.findall(r"^##\s+(.+)$", document, re.M)]

        for question, chunk, fact in rows:
            if chunk not in (None, "ANSWER_ONLY"):
                for wanted in (chunk if isinstance(chunk, tuple) else (chunk,)):
                    if not any(wanted.lower() in h.lower() for h in headings):
                        problems.append(
                            f"{slug}: no section matches {wanted!r} — {question}")
            if fact is not None and fact != DECLINE:
                # DECLINE asserts the shape of the reply, not a fact in the
                # document — there is nothing here to check it against.
                facts = fact if isinstance(fact, tuple) else (fact,)
                # A tuple means "any of these will do", so it's only wrong
                # when the document contains none of them.
                if not any(f.lower() in lowered for f in facts):
                    problems.append(
                        f"{slug}: document says none of {facts!r} — {question}")
    return problems


def normalise(text):
    """Flatten the ways the same fact gets typed.

    Six of the nine failures in the first honest run were this and nothing
    else: the assistant wrote "5–8 weeks" with an en dash where the document
    says "5 to 8", or "3 hours" where the document says "three hours". The
    answers were correct. The test was asserting a spelling.

    A test that fails for the wrong reason is worse than no test, because
    the reasonable response to it is to stop believing the suite.
    """
    text = (text or "").lower()
    for dash in ("\u2013", "\u2014", "\u2212"):      # en, em, minus
        text = text.replace(dash, "-")
    text = text.replace("\u2019", "'")                # curly apostrophe
    text = text.replace("**", "").replace("*", "")   # markdown emphasis
    return " ".join(text.split())


# Numbers a business writes one way and an assistant says another. Only
# the small ones: past ten, nobody writes it out.
NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}


def digit_form(text):
    """'three hours' -> '3 hours', so a fact can be asserted either way."""
    words = normalise(text).split()
    return " ".join(NUMBER_WORDS.get(w, w) for w in words)


def range_form(text):
    """'five to eight weeks' and '5-8 weeks' are the same claim.

    A document writes a range out; an assistant writes it with a dash. Both
    forms collapse to "5-8" so the comparison is about the numbers rather
    than the punctuation between them.
    """
    import re
    text = digit_form(text)
    text = re.sub(r"\s*-\s*", "-", text)
    return re.sub(r"(\d)\s+to\s+(\d)", r"\1-\2", text)


def contains(reply, expected):
    """Does the reply carry the fact? A tuple means any one of them will do."""
    if expected == DECLINE:
        text = normalise(reply)
        return any(phrase in text for phrase in DECLINING)

    forms = (normalise(reply), digit_form(reply), range_form(reply))
    for one in (expected if isinstance(expected, (tuple, list)) else (expected,)):
        if (normalise(one) in forms[0]
                or digit_form(one) in forms[1]
                or range_form(one) in forms[2]):
            return True
    return False


def heading_matches(headings, expected):
    """Was the right section retrieved? A tuple means any one of them will do."""
    wanted = expected if isinstance(expected, (tuple, list)) else (expected,)
    return any(str(w).lower() in h.lower() for w in wanted for h in headings)


def evaluate(slug, ask_llm=True, verbose=True):
    """Score one business. Returns (retrieval_hits, retrieval_n, answer_hits, answer_n)."""
    config = config_for(slug)
    tests  = TESTS[slug]

    r_hits = r_total = a_hits = a_total = 0
    failures = []

    if verbose:
        print(f"\n{'=' * 72}")
        print(f"  {config['business']['name']}")
        print(f"{'=' * 72}")

    for i, (question, expected_chunk, expected_fact) in enumerate(tests, 1):
        chunks   = retrieve(question, config)
        headings = [c.split("\n")[0].strip("# ").strip() for c, _ in chunks]

        if expected_chunk == "ANSWER_ONLY":
            retrieved_ok, verdict = None, "n/a"
        elif expected_chunk is None:
            # Retrieving nothing IS the correct behaviour here. A knowledge
            # base that answers questions it has nothing to say about is
            # worse than one that admits the gap.
            retrieved_ok = len(chunks) == 0
            verdict = "correctly empty" if retrieved_ok else f"returned {len(chunks)}"
        else:
            retrieved_ok = heading_matches(headings, expected_chunk)
            verdict = "hit" if retrieved_ok else f"got {headings[:2]}"

        if retrieved_ok is not None:
            r_total += 1
            r_hits  += 1 if retrieved_ok else 0

        reply = get_llm_reply(question, None, config) if ask_llm else None

        answered_ok = None
        if ask_llm and expected_fact is not None:
            answered_ok = contains(reply, expected_fact)
            a_total += 1
            a_hits  += 1 if answered_ok else 0

        bad = (retrieved_ok is False) or (answered_ok is False)
        if bad:
            failures.append((question, verdict, expected_fact, reply))

        if verbose:
            marks = []
            if retrieved_ok is not None:
                marks.append(f"retrieval {'PASS' if retrieved_ok else 'FAIL'} ({verdict})")
            if answered_ok is not None:
                marks.append(f"answer {'PASS' if answered_ok else 'FAIL'}")
            print(f"[{i:>2}] {question}")
            print(f"     {' · '.join(marks) if marks else 'not scored'}")
            if bad:
                print(f"     wanted: {expected_fact!r}")
            if reply:
                print(f"     reply: {reply}")

    return r_hits, r_total, a_hits, a_total, failures


def percent(hits, total):
    return f"{100 * hits / total:.0f}%" if total else "n/a"


def main():
    args = [a for a in sys.argv[1:]]
    ask_llm = "--retrieval" not in args
    args = [a for a in args if not a.startswith("--") or a == "--all"]

    if "--all" in args:
        slugs = list(TESTS)
    elif args and args[0] in TESTS:
        slugs = [args[0]]
    else:
        print("Usage: python3 rag_eval.py <slug> | --all [--retrieval]")
        print("Businesses:", ", ".join(TESTS))
        return 1

    problems = preflight(slugs) + validate_tests()
    if problems:
        print(f"Not scoring — {len(problems)} problem(s) would make the "
              f"numbers meaningless:\n")
        for problem in problems:
            print("  ·", problem)
        print("\nFix these before reading any score from this run.")
        return 1

    if ask_llm:
        start_turn_accounting()

    rows = []
    for slug in slugs:
        r_hits, r_total, a_hits, a_total, failures = evaluate(slug, ask_llm=ask_llm)
        rows.append((slug, r_hits, r_total, a_hits, a_total, failures))

    print(f"\n{'=' * 72}")
    print(f"  {'business':<26} {'retrieval':>12} {'answer':>12}")
    print(f"  {'-' * 26} {'-' * 12} {'-' * 12}")
    R = A = RT = AT = 0
    for slug, r_hits, r_total, a_hits, a_total, _ in rows:
        print(f"  {slug:<26} {f'{r_hits}/{r_total} ' + percent(r_hits, r_total):>12}"
              f" {f'{a_hits}/{a_total} ' + percent(a_hits, a_total) if a_total else 'skipped':>12}")
        R, RT, A, AT = R + r_hits, RT + r_total, A + a_hits, AT + a_total
    if len(rows) > 1:
        print(f"  {'-' * 26} {'-' * 12} {'-' * 12}")
        print(f"  {'all':<26} {f'{R}/{RT} ' + percent(R, RT):>12}"
              f" {f'{A}/{AT} ' + percent(A, AT) if AT else 'skipped':>12}")
    print(f"{'=' * 72}")

    # Everything that failed, gathered in one place. A score tells you
    # whether it got worse; this tells you what to go and look at.
    every_failure = [f for row in rows for f in row[5]]
    if every_failure:
        print(f"\n{len(every_failure)} to look at:\n")
        for question, verdict, expected, reply in every_failure:
            print(f"  · {question}")
            print(f"    retrieval: {verdict}")
            if expected is not None:
                print(f"    wanted:    {expected!r}")
            if reply:
                print(f"    said:      {reply[:160]}")
            print()

    if ask_llm:
        summary = turn_cost_summary()
        if summary:
            print(f"Run cost: {summary}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

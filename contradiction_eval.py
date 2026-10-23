#!/usr/bin/env python3
"""contradiction_eval.py — does contradictions.py find clashes, and only those?

Run it:
    python3 contradiction_eval.py                       the default model
    python3 contradiction_eval.py --model=claude-sonnet-5-5 [--repeat=3]

Cases are built from the seed (documents/<slug>/*.md and config/<slug>.yaml):
each business as it is, which must get no flags, and ten planted clashes
across the five, each a sentence added to one section that disagrees with
another section or with a setting (hours, menu prices, booking notice).

  caught      — planted clashes flagged (one side's quote is in the plant)
  false flags — flags that don't involve a plant: each is an owner told two
                true things disagree, which is how a warning gets ignored.
"""

import sys
from dotenv import load_dotenv
load_dotenv()

import contradictions
import llm
from config import load_config
from import_documents import split_sections
from pathlib import Path
from progress import say

ROOT = Path(__file__).resolve().parent

# (business, section to add to, the sentence added, what it disagrees with)
PLANTS = [
    ("sunrise_bakery_and_cafe", "Allergens and Dietary Notes",
     "Gluten-free muffins can be ready the next day.", "Booking notice: 96 hours"),
    ("sunrise_bakery_and_cafe", "Muffins and Daily Pastries",
     "We're open until 5pm every day we're open.", "Business details: 3pm"),
    ("bobs_plumbing", "Service Area",
     "An estimate visit costs $50.", "Estimates: free"),
    ("bobs_plumbing", "Brands We Service",
     "Water heaters we install carry a 5-year warranty.", "Water Heater: 10-year"),
    ("crosstown_pizza", "Subs and Salads",
     "We deliver up to 10 miles from the shop.", "Delivery: 6 miles"),
    ("crosstown_pizza", "Wings",
     "A large pizza is $20.", "Menu prices: $22"),
    ("belmont_hair_studio", "Stylists",
     "Patch tests need to be done at least 24 hours before a colour.", "Patch Tests: 48 hours"),
    ("belmont_hair_studio", "Products and Aftercare",
     "We don't take cards, only cash.", "Payment: all major cards"),
    ("ridgeline_contracting", "Kitchen Remodels",
     "You'll get your written quote within a week of the visit.", "Estimates: three business days"),
    ("ridgeline_contracting", "Bathroom Remodels",
     "All our work comes with a one-year workmanship warranty.", "Warranty: two years"),
]


# Fair flags in the seed itself, allowed but not required: Belmont says a
# cut and colour booked together is cheaper, and a cut ($48) plus a root
# touch-up ($85) is $133 against $145; Colour says longer hair is "quoted at
# consultation" where the menu gives "$20 to $60 extra" (2026-10-23).
ACCEPTABLE = {"belmont_hair_studio": ["rather than two is cheaper", "quoted at consultation",
                                      "$20 to $60 extra"]}


def seed(slug):
    out = []
    for md in sorted((ROOT / "documents" / slug).glob("*.md")):
        for title, body in split_sections(md.read_text()):
            out.append({"title": title, "body": body})
    return out


def cases():
    slugs = sorted({p[0] for p in PLANTS})
    out = [(slug, None, seed(slug)) for slug in slugs]
    for slug, section, sentence, against in PLANTS:
        sections = seed(slug)
        target = next(s for s in sections if contradictions.label(s["title"]) == section)
        target["body"] = target["body"].rstrip() + " " + sentence
        out.append((slug, (sentence, against), sections))
    return out


def main():
    model = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--model=")), None)
    repeat = next((int(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--repeat=")), 1)
    llm.start_turn_accounting()
    failed = 0
    built = cases()
    for run in range(repeat):
        caught = planted = false = 0
        lines = []
        for slug, plant, sections in built:
            cfg = load_config(f"config/{slug}.yaml")
            found = contradictions.check(cfg, sections, model)
            hit = [f for f in found if plant and any(
                plant[0].lower() in s["quote"].lower() or s["quote"].lower() in plant[0].lower()
                for s in (f["a"], f["b"]))]
            fair = [f for f in found if f not in hit and any(
                a.lower() in s["quote"].lower() for a in ACCEPTABLE.get(slug, [])
                for s in (f["a"], f["b"]))]
            wrong = [f for f in found if f not in hit and f not in fair]
            planted += bool(plant)
            caught += bool(hit)
            false += len(wrong)
            if (plant and not hit) or wrong:
                lines.append(f"  {slug}" + (f" | planted: {plant[0]!r} vs {plant[1]}" if plant else " | as seeded")
                             + ("\n     MISSED" if plant and not hit else "")
                             + "".join(f"\n     false flag: [{w['a']['where']}] {w['a']['quote']!r}"
                                       f" vs [{w['b']['where']}] {w['b']['quote']!r} ({w['why']})"
                                       for w in wrong))
        say(f"\nrun {run + 1}: caught {caught}/{planted}, false flags {false} "
            f"({model or llm.MODEL})")
        for line in lines:
            say(line)
        failed += caught < planted or false > 0
    spent = llm.turn_cost_summary()
    if spent:
        say(spent)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

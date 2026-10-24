"""contradictions.py — two things a business says that can't both be true.

The knowledge base is the owner's own writing, edited a section at a time,
and some facts also live in settings (hours, phone, menu prices, booking
notice, cancellation rules). A section can come to disagree with another,
or with a setting: "estimates are free" in one place and "an estimate
visit is $50" in another, or "open till 5" typed into a section while the
hours say 3pm. The assistant reads both and picks one; the owner never
sees the clash. The Knowledge page's "Check for contradictions" runs this.

A model lists the pairs; code keeps only pairs whose two quotes really are
in the sources they name (the same safeguard as grounding.py). Measured on
contradiction_eval.py: clean seed knowledge bases and planted clashes.
"""

import logging
import os
import re

log = logging.getLogger("contradictions")

# The model the Knowledge page's check runs with, and contradiction_eval.py's
# default. Sonnet 5.5 caught 10/10 planted clashes with no false flags on
# five clean seed knowledge bases, 3 runs in 3; Haiku 5.5 9/10 with 4
# (contradiction_eval.py, 2026-10-23). The eval used to default to the app's
# default model (Haiku) instead, so its headline numbers weren't this one's.
MODEL = os.environ.get("CONTRADICTION_MODEL", "claude-sonnet-5-5")
MAX_PAIRS = 6
SETTINGS = ("Business details", "Menu prices", "Booking notice", "Cancellation rules")


def label(title):
    """A section's title as the owner wrote it, without "(Topics: …)"."""
    return (title or "").split(" (Topics")[0].split("(Topics")[0].strip()


def sources(config, sections):
    """[(where, text)]: each section by its title (without Topics), then the
    facts kept in settings, each under its own name."""
    import menu
    import notice_form
    import policy
    out = [(label(s["title"]), (s["body"] or "").strip())
           for s in sections if (s.get("body") or "").strip()]
    b = config.get("business") or {}
    details = [f"{label}: {b[key]}" for key, label in
               (("hours", "Hours"), ("phone", "Phone"),
                ("emergency_phone", "After-hours emergency line"),
                ("address", "Address"), ("service_area", "Service area")) if b.get(key)]
    if details:
        out.append(("Business details", ". ".join(details) + "."))
    prices = menu.render_menu(config)
    kinds = [menu.kinds_line(name, rule) for it in menu.items(config)
             for name, rule in it["add_ons"].items() if rule["kinds"]]
    if prices or kinds:
        out.append(("Menu prices", " ".join(p for p in [prices, *kinds] if p)))
    notice = notice_form.describe(config)
    if notice:
        out.append(("Booking notice", " ".join(notice)))
    rules = policy.policy_text(config)
    if rules:
        out.append(("Cancellation rules", " ".join(rules.split())))
    return out


PROMPT = """Below is everything a small business has told its customer \
assistant: its knowledge-base sections, and facts kept in its settings \
(business details, menu prices, booking notice, cancellation rules), each \
under its name.

Find pairs of statements that can't both be true: the same thing with a \
different price, time, number, distance, day or rule, or a yes in one place \
and a no in another.

Both statements must answer the same question. NOT a contradiction: the \
same fact in other words; one statement with more detail than the other; \
different services, items, sizes or situations; statements about different \
questions (how soon you can book, and how many visits it takes); a general \
rule and a stated exception to it; advice ("ideally 4-6 weeks") beside a \
rule ("at least 2 weeks"); "anytime" meaning during opening hours.

Respond with ONLY a JSON object, no preamble:
{"contradictions": [{"a": {"where": "...", "quote": "..."}, \
"b": {"where": "...", "quote": "..."}, "why": "..."}]}
"where" is a name exactly as given below; "quote" is copied word for word \
from it, a sentence or less. "why" is a short reason. Empty list if there \
are none. At most %d.

""" % MAX_PAIRS


# A pair whose own reason says there's no clash. Sonnet 5.5, 2026-10-24,
# contradiction_eval x3: 3 false flags, all one Sunrise pair (gluten-free
# muffins made to order / not in the case), each with a "why" like "These
# two statements are consistent, so this is not a real contradiction".
# Narrow on purpose: "inconsistent", "not consistent" and "isn't
# consistent" are reasons FOR a clash and don't match.
_NO_CLASH = re.compile(
    r"\b(?:are|is|seem|appear) (?:fully |entirely |actually )?consistent\b"
    r"|\bnot (?:a |an )?(?:real |actual |true |genuine )?(?:contradiction|conflict)"
    r"|\bno (?:real |actual |true |genuine )?(?:contradiction|conflict)"
    r"|\b(?:do not|don'?t|does not|doesn'?t) (?:actually )?(?:contradict|conflict)",
    re.I)


def _norm(text):
    return " ".join(str(text or "").lower().replace("’", "'").split())


def check(config, sections, model=None):
    """[{"a": {"where", "quote"}, "b": {...}, "why"}] for each clash the
    model finds whose quotes are really where it says. [] on any failure."""
    from llm import _create, as_data, text_of, _note_usage, json_object
    found = sources(config, sections)
    if len(found) < 2:
        return []
    text = {where: _norm(body) for where, body in found}
    block = "\n\n".join(f"[{where}]\n{body}" for where, body in found)
    call = dict(max_tokens=900, messages=[{"role": "user", "content":
                                           PROMPT + as_data(block, tag="business_information")}])
    if model:
        call["model"] = model
    try:
        response = _create(**call)
        _note_usage(response, "contradiction check")
        # The first whole object, whatever comes before or after it (once in
        # 15, Sonnet 5.5 wrote a second one: "Extra data"). llm.json_object.
        data = json_object(text_of(response))
        pairs = data.get("contradictions") if isinstance(data, dict) else None
        if not isinstance(pairs, list):
            raise ValueError("unexpected shape")
    except Exception as e:
        log.warning("Contradiction check failed: %s", e)
        return []
    out = []
    for pair in pairs[:MAX_PAIRS]:
        try:
            sides = [{"where": str(pair[k]["where"]).strip(),
                      "quote": str(pair[k]["quote"]).strip().strip('"')} for k in ("a", "b")]
        except (KeyError, TypeError):
            continue
        why = str(pair.get("why") or "")
        if _NO_CLASH.search(why):
            log.info("Dropped a pair whose own reason says it isn't a contradiction")
            continue
        if all(s["where"] in text and _norm(s["quote"]) and _norm(s["quote"]) in text[s["where"]]
               for s in sides) and _norm(sides[0]["quote"]) != _norm(sides[1]["quote"]):
            out.append({"a": sides[0], "b": sides[1], "why": why[:200]})
    return out

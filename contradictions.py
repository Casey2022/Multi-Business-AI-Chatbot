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

import json
import logging
import re

log = logging.getLogger("contradictions")

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


def _norm(text):
    return " ".join(str(text or "").lower().replace("’", "'").split())


def check(config, sections, model=None):
    """[{"a": {"where", "quote"}, "b": {...}, "why"}] for each clash the
    model finds whose quotes are really where it says. [] on any failure."""
    from llm import _create, as_data, text_of, _note_usage
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
        raw = text_of(response)
        # The first whole object, whatever comes before or after it (once in
        # 15, Sonnet 5.5 wrote a second one: "Extra data").
        start = raw.find("{")
        data = json.JSONDecoder().raw_decode(raw[start:])[0] if start != -1 else json.loads(raw)
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
        if all(s["where"] in text and _norm(s["quote"]) and _norm(s["quote"]) in text[s["where"]]
               for s in sides) and _norm(sides[0]["quote"]) != _norm(sides[1]["quote"]):
            out.append({"a": sides[0], "b": sides[1], "why": str(pair.get("why") or "")[:200]})
    return out

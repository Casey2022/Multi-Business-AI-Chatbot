"""assistant_knowledge.py — the knowledge-base changes the owner assistant can make.

Added 2026-10-15, after the menu (assistant_actions.py). The same rule: the
assistant only touches what goes through Publish. A section it changes is
saved through the same db functions the Knowledge base page uses, so it's
an ordinary draft: "Changed since publish" on the page, a line in the
"Unpublished changes" banner, kept in the section's History, previewable
with "Preview answers", and undone by "Discard all changes".

Why find-and-replace, not "rewrite the section": a model asked to change
one sentence and handed the whole section back tends to tidy the rest,
and a tidied section can lose a fact nobody asked to lose. edit_section
replaces the exact words it names and leaves every other character as it
was, and the owner sees exactly those words, before and after, under the
reply. The find has to be in the section exactly once, which means the
model has read the section (read_section) rather than guessed at it.

What code refuses, whatever the model was told:
- A number nobody gave. Every number in new text must be one the owner
  wrote in this conversation, or one already in their knowledge base or
  business details (moving a fact is fine; inventing one isn't). The
  photo import checks a proposal's numbers against the photo the same way
  (photo_import.numbers_in, reused).
- A second copy of something with one home. Prices of menu items live on
  the menu; cancellation, refund (and, where the rules cover them, deposit)
  rules live in the YAML, which only the operator changes. A section that
  restated either could disagree with it, and the FAQ showed which copy a
  model believes (one_source_test.py).
- Cutting the menu loose. Menu prices attach to a section by its title
  (menu.price_lines_for), so renaming that part of the title or deleting
  the section would leave the prices attached to nothing.

Section text is the owner's, or a photo's, so a read comes back fenced as
data, and nothing in it is an instruction.
"""

import logging
import re

log = logging.getLogger("assistant")

MAX_TITLE = 120
MAX_BODY = 4000
SNIPPET = 140

_SECTION = {"type": "string",
            "description": ("The section's title as listed in the setup; the part before "
                            "“(Topics: …)” is enough (e.g. “Delivery”).")}

TOOLS = [
    {"name": "read_section",
     "description": ("Read a knowledge-base section's current title and text. Do this "
                     "before edit_section, to copy the exact words to replace."),
     "input_schema": {"type": "object", "properties": {"section": _SECTION},
                      "required": ["section"]}},
    {"name": "edit_section",
     "description": ("Change wording in a section: replace the exact words in “find” "
                     "(copied from read_section, appearing once in the text) with "
                     "“replace”. Everything else in the section stays as it is. An empty "
                     "“replace” removes the words."),
     "input_schema": {"type": "object", "properties": {
         "section": _SECTION,
         "find": {"type": "string", "description": "Words copied exactly from the section's text: enough to appear only once, usually a whole sentence."},
         "replace": {"type": "string", "description": "What goes in their place, written the way the section is written."}},
         "required": ["section", "find", "replace"]}},
    {"name": "add_to_section",
     "description": "Add a sentence or paragraph at the end of a section.",
     "input_schema": {"type": "object", "properties": {
         "section": _SECTION,
         "text": {"type": "string", "description": "What to add, written the way the section is written."}},
         "required": ["section", "text"]}},
    {"name": "add_section",
     "description": ("Add a new section, for something no section covers yet. Put the "
                     "words customers use in the title; you may add “(Topics: word, "
                     "word)” with other words they'd use, like the other sections."),
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string", "description": "The new section's title."},
         "text": {"type": "string", "description": "What the assistant should know, in plain sentences."}},
         "required": ["title", "text"]}},
    {"name": "rename_section",
     "description": "Change a section's title.",
     "input_schema": {"type": "object", "properties": {
         "section": _SECTION,
         "title": {"type": "string", "description": "The whole new title."}},
         "required": ["section", "title"]}},
    {"name": "delete_section",
     "description": "Delete a section (its earlier versions are kept in History).",
     "input_schema": {"type": "object", "properties": {"section": _SECTION},
                      "required": ["section"]}},
]
NAMES = {t["name"] for t in TOOLS}
CHANGES = NAMES - {"read_section"}

# Words that say the number the owner meant ("open at seven"). Only the
# plain ones; "a dozen" and "half" are arithmetic, which the model doesn't do.
_NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
_NUMBER_WORDS.update(thirty="30", forty="40", fifty="50", sixty="60", seventy="70",
                     eighty="80", ninety="90", hundred="100")

_MONEY = re.compile(r"\$\s?\d|\b\d+(?:\.\d+)?\s*dollars?\b", re.I)
_CANCELLING = r"cancel\w*|refund\w*|forfeit\w*|no[- ]shows?"


class Refused(Exception):
    """A change that wasn't made, said for the model to pass on or fix."""


def offered(business):
    """The tools for a business owner (an operator account has no sections)."""
    if not business or business.get("id") is None:
        return []
    return TOOLS


# ---------------------------------------------------------------------------
# Finding things
# ---------------------------------------------------------------------------

def core(title):
    """A title without its "(Topics: …)" tail, as written (menu.section_core
    is the same cut, lowercased for matching)."""
    return " ".join((title or "").split("(")[0].split())


def _key(text):
    return " ".join(str(text or "").split()).lower()


def find_section(sections, name):
    """The section the model means: by whole title, or by the part before
    "(Topics: …)". Never a guess: anything else lists what there is."""
    wanted = _key(name)
    for s in sections:
        if _key(s["title"]) == wanted:
            return s
    matches = [s for s in sections if _key(core(s["title"])) == _key(core(name))]
    if len(matches) == 1:
        return matches[0]
    listed = "; ".join(f"“{core(s['title'])}”" for s in sections) or "none yet"
    raise Refused(f"There's no section called “{name}”. The sections are: {listed}.")


def _attached_items(config, title):
    """Menu items whose prices are shown with this section."""
    import menu
    here = _key(core(title))
    return [it["item"] for it in menu.items(config)
            if it["section"] and _key(core(it["section"])) == here]


def snippet(text):
    text = " ".join(str(text or "").split())
    return text if len(text) <= SNIPPET else text[:SNIPPET - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# What code refuses
# ---------------------------------------------------------------------------

def _money(text):
    """The dollar amounts in text, normalised like photo_import.numbers_in."""
    from photo_import import numbers_in
    return {v for v, _ in numbers_in(" ".join(re.findall(r"\$\s?[\d,]+(?:\.\d+)?", text or ""))).items()}


def known_numbers(owner_words, sections, config):
    """(owner's, already written, already written as money): the numbers new
    text may use. The owner's count in any form, digits or plain words
    ("seven"). One already in their sections or business details may be
    moved, but a dollar amount only if it's already a dollar amount: "$4"
    isn't known because "4pm" is (2026-10-15)."""
    from photo_import import numbers_in
    words = owner_words or ""
    spelled = " ".join(_NUMBER_WORDS[w] for w in re.findall(r"[a-z]+", words.lower())
                       if w in _NUMBER_WORDS)
    written = " ".join([s["title"] + " " + s["body"] for s in sections]
                       + [str((config or {}).get("business") or "")])
    return set(numbers_in(words + " " + spelled)), set(numbers_in(written)), _money(written)


def check_text(text, *, owner_words, sections, config, section_title=None):
    """Refused if new text breaks a rule (see the module docstring)."""
    import menu
    import policy
    from photo_import import numbers_in
    owner, written, written_money = known_numbers(owner_words, sections, config)
    money = _money(text)
    new = [t for v, t in numbers_in(text).items() if v not in owner
           and v not in (written_money if v in money else written)]
    if new:
        raise Refused(
            "Not changed: " + ", ".join(f"“{t}”" for t in new) + (" isn't a number" if len(new) == 1
            else " aren't numbers") + " the owner gave, or one already in their knowledge "
            "base. Use the owner's own numbers, or ask them for the right one.")

    rules = policy.cancellation(config)
    if rules:
        pattern = _CANCELLING + (r"|deposits?" if rules.get("deposits") else "")
        said = re.search(rf"\b({pattern})\b", text, re.I)
        if said:
            raise Refused(
                f"Not changed: the text mentions “{said.group(0)}”, and this business's "
                "cancellation rules (" + ("deposits, " if rules.get("deposits") else "")
                + "cancelling, refunds) are kept in one place that only the operator "
                "changes, so customers are never told two different things. Tell the "
                "owner, and offer to pass it to support with hand_off_to_support.")

    items = menu.items(config)
    if items and _MONEY.search(text):
        attached = _attached_items(config, section_title) if section_title else []
        named = [it["item"] for it in items
                 if any(re.search(rf"\b{re.escape(n)}s?\b", text, re.I)
                        for n in [it["item"]] + it["aliases"])]
        if attached or named:
            which = ", ".join(f"“{n}”" for n in (named or attached))
            raise Refused(
                f"Not changed: prices for {which} live on the menu, the only copy (the "
                "Knowledge base page shows them under the section). Change them with "
                "the menu tools instead (change_menu_price, add_menu_option, "
                "set_extra_price, add_menu_item).")


# ---------------------------------------------------------------------------
# The changes
# ---------------------------------------------------------------------------

def _read(section, config):
    from llm import as_data
    text = as_data(f"Title: {section['title']}\n\n{section['body']}", tag="section")
    attached = _attached_items(config, section["title"])
    if attached:
        import menu
        text += ("\nShown under it on the page, from the menu (not part of the text; "
                 "change with the menu tools): " + menu.price_lines_for(section["title"], config))
    return text


def _keep_breaks(found, replace):
    """The replacement with the found text's own line breaks, gap for gap,
    when it has as many gaps between words (a sentence with one word
    changed). Otherwise as written."""
    gaps = re.findall(r"\s+", found.strip())
    parts = replace.split()
    if len(parts) - 1 != len(gaps):
        return replace
    return "".join(p + (gaps[i] if i < len(gaps) else "") for i, p in enumerate(parts))


def _clean(text):
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _change(name, args, business_id, config, owner_words, user_email):
    """(text for the model, the line shown under the reply) for one change."""
    from db import (get_documents, update_document_section, add_document_section,
                    delete_document_section)
    sections = get_documents(business_id)
    by = f"{user_email} via assistant"
    checks = dict(owner_words=owner_words, sections=sections, config=config)

    if name == "add_section":
        title, body = _clean(args.get("title")), _clean(args.get("text"))
        if not title or not body:
            raise Refused("A new section needs both a title and text.")
        if len(title) > MAX_TITLE or len(body) > MAX_BODY:
            raise Refused(f"Too long: a title can be {MAX_TITLE} characters and the text {MAX_BODY}.")
        clash = next((s for s in sections if _key(core(s["title"])) == _key(core(title))), None)
        if clash:
            raise Refused(f"There's already a section called “{core(clash['title'])}”. "
                          "Add to it (add_to_section or edit_section) instead.")
        check_text(title + "\n" + body, section_title=title, **checks)
        add_document_section(business_id, title, body, updated_by=by)
        return f"New section: “{title}”"

    section = find_section(sections, args.get("section", ""))
    title, body, label = section["title"], section["body"], core(section["title"])

    if name == "delete_section":
        if len(sections) <= 1:
            raise Refused("That's their only section: without it, the assistant can't answer "
                          "questions about the business. Edit it instead.")
        attached = _attached_items(config, title)
        if attached:
            raise Refused(f"Not deleted: the menu's prices for {', '.join(attached)} are "
                          f"shown with “{label}”, so they'd be attached to nothing. Move "
                          f"or remove those items on the Menu first.")
        delete_document_section(section["id"])
        return f"Section deleted: “{label}”"

    if name == "rename_section":
        new_title = _clean(args.get("title"))
        if not new_title or len(new_title) > MAX_TITLE:
            raise Refused(f"A title needs 1 to {MAX_TITLE} characters.")
        if new_title == title:
            raise Refused("Nothing to change: the section is already called that.")
        if _key(core(new_title)) != _key(label):
            attached = _attached_items(config, title)
            if attached:
                raise Refused(f"Not renamed: the menu's prices for {', '.join(attached)} are "
                              f"attached to this section by the name “{label}”. Keep that "
                              "part; the “(Topics: …)” part can change.")
            if any(s["id"] != section["id"] and _key(core(s["title"])) == _key(core(new_title))
                   for s in sections):
                raise Refused(f"There's already a section called “{core(new_title)}”.")
        check_text(new_title, section_title=title, **checks)
        update_document_section(section["id"], new_title, body, updated_by=by)
        return f"Section renamed: “{label}” → “{core(new_title)}”"

    if name == "edit_section":
        find, replace = args.get("find") or "", _clean(args.get("replace"))
        if not find.strip():
            raise Refused("“find” needs the exact words to replace, copied from read_section.")
        count = body.count(find)
        if count == 0:
            # Line breaks in the stored text are where the owner pressed
            # Enter; a model copying a sentence joins them with spaces.
            flexible = r"\s+".join(map(re.escape, find.split()))
            spots = list(re.finditer(flexible, body))
            if len(spots) == 1:
                find, count = spots[0].group(0), 1
                replace = _keep_breaks(find, replace)
            else:
                count = len(spots)
        if count == 0:
            raise Refused(f"Not changed: those words aren't in “{label}”. Read it with "
                          "read_section and copy the words exactly.")
        if count > 1:
            raise Refused(f"Not changed: those words appear {count} times in “{label}”. "
                          "Include more of the sentence so they appear once.")
        if _key(find) == _key(replace):
            raise Refused("Nothing to change: that's what the section already says.")
        new_body = _clean(body.replace(find, replace, 1))
        if not new_body:
            raise Refused("That would leave the section empty. To remove it, use delete_section.")
        if len(new_body) > MAX_BODY:
            raise Refused(f"Too long: a section's text can be {MAX_BODY} characters.")
        if replace:
            check_text(replace, section_title=title, **checks)
        update_document_section(section["id"], title, new_body, updated_by=by)
        return (f"{label}: “{snippet(find)}” → “{snippet(replace)}”" if replace
                else f"{label}: removed “{snippet(find)}”")

    if name == "add_to_section":
        text = _clean(args.get("text"))
        if not text:
            raise Refused("There's nothing to add.")
        if _key(text) in _key(body):
            raise Refused(f"Nothing to change: “{label}” already says that.")
        new_body = body.rstrip() + "\n\n" + text
        if len(new_body) > MAX_BODY:
            raise Refused(f"Too long: a section's text can be {MAX_BODY} characters.")
        check_text(text, section_title=title, **checks)
        update_document_section(section["id"], title, new_body, updated_by=by)
        return f"{label}: added “{snippet(text)}”"

    raise Refused(f"Unknown change “{name}”.")


def run(name, args, business, config, user_email, owner_words=""):
    """One tool call. Returns (text for the model, action or None, is_error).

    A read is neither an action nor an error. A change returns the action
    the Help page lists under "Saved as a draft"."""
    import menu
    from db import get_documents
    if name not in NAMES:
        return f"Unknown tool {name}.", None, True
    business_id = business["id"]
    config = menu.with_draft(config or {}, business_id)
    args = {k: (v if isinstance(v, str) or v is None else str(v)) for k, v in (args or {}).items()}
    try:
        if name == "read_section":
            return _read(find_section(get_documents(business_id), args.get("section", "")),
                         config), None, False
        line = _change(name, args, business_id, config, owner_words, user_email)
    except Refused as e:
        return str(e), None, True
    log.info("Owner assistant changed the knowledge base (business %s): %s", business_id, name)
    return ("Saved as a draft: " + line + ". Customers still get the published knowledge "
            "base until the owner presses “Publish changes”; “Preview answers” on the "
            "Knowledge base page shows how the assistant will answer with it, and "
            "“Discard all changes” undoes it."), {"tool": name, "lines": [line], "page": "knowledge"}, False

"""section_links.py — a renamed knowledge-base section keeps what it shows.

Several things are shown under a section, chosen by its title: a menu
item's price lines (menu: item.section), a topping list (an add-on's
kinds_section), and a booking-notice line (booking.notice: section, and a
variant's sections). Owners rename sections on the Knowledge page. Before
2026-10-23 a rename detached all of them without a word: renaming "Wings"
to "Chicken Wings", "Toppings" to "Pizza Toppings" and "Custom Birthday
Cakes" to "Birthday Cakes" left the chunks Publish builds with no wing
prices, no topping list, no cake prices and no cake notice. The bot then
answers from the prose alone, which no longer states those numbers.

Every rename is already in a section's History (document_versions keeps
the title it had). So load_config points each link at the title its
section has now: a link naming an earlier title of a section is moved to
that section's current one. A title another section has now isn't
borrowed. Drafts (menu, notice) saved under an older title follow too.
"""

import copy


def _core(title):
    import menu
    return menu.section_core(title)


def renames(business_id):
    """{earlier title's core: the section's title now}, from each section's
    History. Empty when nothing was renamed (or there's no database)."""
    import db
    try:
        sections = db.get_documents(business_id)
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT document_id, title FROM document_versions WHERE document_id IN "
            f"({','.join('?' * len(sections))}) ORDER BY id",
            [s["id"] for s in sections]).fetchall() if sections else []
        conn.close()
    except Exception:
        return {}
    now = {s["id"]: s["title"] for s in sections}
    taken = {_core(t) for t in now.values()}
    out = {}
    for row in rows:
        old, current = _core(row["title"]), now.get(row["document_id"])
        # Oldest first, and the first claim stands: if two sections have had
        # the same title, the one that had it first is the one the links
        # (from the seed) meant (section_links_test: the hours section,
        # briefly called "Wings", mustn't take the wing prices).
        if current and old and old != _core(current) and old not in taken:
            out.setdefault(old, current)
    return out


def follow(config, business_id):
    """Point every section link at the section's current title. Changes
    `config` in place and returns it; nothing to do without renames."""
    moved = renames(business_id) if business_id is not None else {}
    if not moved:
        return config

    def to(title):
        return moved.get(_core(title), title) if title else title

    for entry in config.get("menu") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("section"):
            entry["section"] = to(entry["section"])
        for spec in (entry.get("add_ons") or {}).values():
            if isinstance(spec, dict) and spec.get("kinds_section"):
                spec["kinds_section"] = to(spec["kinds_section"])
    for entry in ((config.get("booking") or {}).get("notice") or {}).values():
        if not isinstance(entry, dict):
            continue
        if entry.get("section"):
            entry["section"] = to(entry["section"])
        for variant in entry.get("variants") or []:
            if isinstance(variant, dict) and variant.get("sections"):
                variant["sections"] = [to(t) for t in variant["sections"]]
    return config


def followed(config, business_id):
    """follow() on a copy (for the draft paths, which hand back copies)."""
    return follow(copy.deepcopy(config), business_id)

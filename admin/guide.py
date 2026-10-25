"""admin/guide.py — the owner guide as a page (/admin/guide).

The guide (admin/owner_guide.md) was written for owners but only the
owner assistant read it: an owner could ask it questions, never read it
(2026-10-24). This shows the same text the assistant gets
(assistant.guide_text: from "## The pages" on), so the page and the
assistant can't disagree, and assistant_test.py's check that every bold
phrase is a real portal label covers both.

The guide uses a small part of Markdown, so this renders that part rather
than adding a dependency: "##"/"###" headings, paragraphs, "- " lists
(a line indented under an item continues it), **bold** and `code`.
Everything is escaped first; nothing in the file becomes markup any other
way.
"""

import re

from flask import render_template
from markupsafe import Markup, escape

from admin import admin_bp
from admin.auth import login_required


def _inline(text):
    """Escaped text with **bold** and `code`."""
    out = str(escape(text))
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", out)


def anchor(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def render(markdown):
    """(html, contents): the guide as HTML, and [(anchor, title)] for its
    "##" sections, in order."""
    html, contents = [], []
    paragraph, items = [], []

    def flush():
        if paragraph:
            html.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()
        if items:
            html.append("<ul>" + "".join(f"<li>{_inline(' '.join(i))}</li>" for i in items)
                        + "</ul>")
            items.clear()

    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
        elif line.startswith("### "):
            flush()
            title = stripped[4:]
            html.append(f'<h3 id="{anchor(title)}">{_inline(title)}</h3>')
        elif line.startswith("## "):
            flush()
            title = stripped[3:]
            contents.append((anchor(title), title))
            html.append(f'<h2 id="{anchor(title)}">{_inline(title)}</h2>')
        elif line.startswith("- "):
            if paragraph:
                flush()
            items.append([stripped[2:]])
        elif items and line.startswith("  "):
            items[-1].append(stripped)          # continues the item above
        else:
            if items:
                flush()
            paragraph.append(stripped)
    flush()
    return Markup("\n".join(html)), contents


@admin_bp.route("/guide")
@login_required
def guide():
    import assistant
    from admin.auth import current_user
    from db import get_business_by_id
    user = current_user()
    business = get_business_by_id(user["business_id"]) if user.get("business_id") else None
    body, contents = render(assistant.guide_text())
    return render_template("admin/guide.html", body=body, contents=contents,
                           business=business)

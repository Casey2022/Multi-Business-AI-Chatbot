# import_documents.py — seed the documents table from the Markdown files.
# Run once per business. Files remain in the repo as the onboarding seed;
# the database is authoritative afterwards.
# Usage:
#   python3 import_documents.py

import re
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Anchored to this file, not the working directory — same reason db.DB_PATH
# is. Run from elsewhere and a cwd-relative "documents" silently finds
# nothing, which reads exactly like "this business has no documents".
DOCS_ROOT = Path(__file__).resolve().parent / "documents"

from db import init_db, get_all_businesses, get_documents, add_document_section
from config import load_config
from rag import collection_for


def split_sections(text):
    """Split Markdown into (title, body) pairs on ## headings.

    The document title (single #) is dropped — it was never useful as a
    chunk, and the orphan-title filter already discarded it at ingest.
    """
    sections = []
    for block in re.split(r"(?=^##\s)", text, flags=re.MULTILINE):
        block = block.strip()
        if not block.startswith("##"):
            continue
        lines = block.split("\n")
        title = lines[0].lstrip("#").strip()
        body  = "\n".join(lines[1:]).strip()
        if title and body:
            sections.append((title, body))
    return sections


def import_for_business(business, config=None):
    """Fill a business's document table from its Markdown seed files.

    Returns the number of sections imported: 0 means it already had some
    (the database is authoritative once populated) or there's no folder for
    it. Never overwrites — this only ever runs on an empty knowledge base.

    Split out of main() so the app can do this at startup. Registering a
    business and giving it a knowledge base are two halves of one thing, and
    leaving the second half as a command someone has to remember is how a
    business ends up live with a bot that knows nothing about it.
    """
    if get_documents(business["id"]):
        return 0

    if config is None:
        config = load_config(business["config_path"], business["id"])
    docs = DOCS_ROOT / collection_for(config)
    if not docs.exists():
        return 0

    count = 0
    for md in sorted(docs.glob("*.md")):
        for title, body in split_sections(md.read_text(encoding="utf-8")):
            add_document_section(business["id"], title, body,
                                 updated_by="import")
            count += 1
    return count


def main():
    init_db()

    for b in get_all_businesses():
        if get_documents(b["id"]):
            print(f"{b['name']}: already has sections — skipping")
            continue
        config = load_config(b["config_path"], b["id"])
        docs   = DOCS_ROOT / collection_for(config)
        if not docs.exists():
            print(f"{b['name']}: no documents folder at {docs}")
            continue
        count = import_for_business(b, config)
        print(f"{b['name']}: imported {count} sections")


if __name__ == "__main__":
    main()
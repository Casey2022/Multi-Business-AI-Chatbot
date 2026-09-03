# import_documents.py — seed the documents table from the Markdown files.
# Run once per business. Files remain in the repo as the onboarding seed;
# the database is authoritative afterwards.
# Usage:
#   python3 import_documents.py

import re
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from db import init_db, get_all_businesses, get_documents, add_document_section
from config import load_config
from rag import _slugify


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


def main():
    init_db()

    for b in get_all_businesses():
        if get_documents(b["id"]):
            print(f"{b['name']}: already has sections — skipping")
            continue

        config = load_config(b["config_path"])
        docs   = Path("documents") / _slugify(config["business"]["name"])
        if not docs.exists():
            print(f"{b['name']}: no documents folder at {docs}")
            continue

        count = 0
        for md in sorted(docs.glob("*.md")):
            for title, body in split_sections(md.read_text(encoding="utf-8")):
                add_document_section(b["id"], title, body,
                                     updated_by="import")
                count += 1
        print(f"{b['name']}: imported {count} sections")


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""seed_check.py — where the local database differs from the seed, and a reset.

Run it:
    python3 seed_check.py                      every business: what differs
    python3 seed_check.py bobs_plumbing        one business
    python3 seed_check.py bobs_plumbing --reset [--yes] [--no-index]

The live site rebuilds every business from the seed (config/<slug>.yaml and
documents/<slug>/*.md) on each deploy; the local database keeps whatever
was edited in the portal. When the two drift, evals measure local data
customers never see. This week that happened four times: Bob's $90 fee,
$150 leak detection and main-phone override, Belmont's cancellation
override, Crosstown's menu override, a local-only Drywall Repair section.
Each was found by an eval failing for a reason that wasn't the bot.

The check lists, per business:
  - sections whose words differ from the seed (and ones that differ only in
    spacing or line breaks, which the bot can't tell apart), and sections
    that exist on one side only;
  - every owner override, published or draft, with what it changes;
  - unpublished knowledge-base changes and a pending upload.

--reset puts one business back to the seed: sections (updated in place, so
each keeps its History; added, deleted and reordered to match), every
override and draft cleared, the upload proposal dropped, published, and its
search index rebuilt (--no-index skips that). The database is copied to
backups/ first. Appointments, conversations, calendar blocks and accounts
are the business's own records, not seed, and are never touched. It asks
you to type the business's slug unless --yes is given.

Demo copies are throwaway and left out.
"""

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKUPS = ROOT / "backups"

DRAFT_SUFFIX = "_draft"


def seed_sections(slug):
    """[(title, body)] from documents/<slug>/*.md, split as seeding splits."""
    from import_documents import DOCS_ROOT, split_sections
    out = []
    for md in sorted((DOCS_ROOT / slug).glob("*.md")):
        out.extend(split_sections(md.read_text(encoding="utf-8")))
    return out


def _words(text):
    return " ".join((text or "").split())


def _short(value, width=70):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= width else text[:width - 1] + "…"


def _core(title):
    """A section title without its Topics list: "Payment and Change Orders"."""
    import menu
    return menu.section_core(title)


def _changed(was, now, width=60):
    """Where two values first differ, as "…old… -> …new…"."""
    a = was if isinstance(was, str) else json.dumps(was, ensure_ascii=False, sort_keys=True)
    b = now if isinstance(now, str) else json.dumps(now, ensure_ascii=False, sort_keys=True)
    a, b = " ".join(a.split()), " ".join(b.split())
    at = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
    start = max(0, at - 15)

    def window(text):
        piece = text[start:start + width]
        return ("…" if start else "") + piece + ("…" if start + width < len(text) else "")
    return f"{window(a)} -> {window(b)}"


def _match(seed, rows):
    """{seed title: the local row for it}: the same title, else the same
    title before its Topics list (a seed that added topics renamed it)."""
    by_title = {r["title"]: r for r in rows}
    by_core = {}
    for r in rows:
        by_core.setdefault(_core(r["title"]), r)
    out, used = {}, set()
    for title, _ in seed:
        row = by_title.get(title)
        if row is None:
            row = by_core.get(_core(title))
            if row is not None and row["title"] in {t for t, _ in seed}:
                row = None              # that row is another seed section's own
        if row is not None and row["id"] not in used:
            out[title] = row
            used.add(row["id"])
    return out


def differences(business):
    """What differs from the seed for one business, as a dict of lists."""
    import db
    from config import load_config, get_nested
    seed = seed_sections(business["slug"])
    rows = db.get_documents(business["id"])
    matched = _match(seed, rows)
    out = {"sections": [], "titles": [], "spacing": [], "only_local": [], "only_seed": [],
           "overrides": [], "drafts": [], "state": []}
    for title, body in seed:
        row = matched.get(title)
        if row is None:
            out["only_seed"].append(title)
            continue
        if row["title"] != title:
            out["titles"].append(f"{row['title']!r} -> {title!r}")
        if _words(row["body"]) != _words(body):
            out["sections"].append(title)
        elif (row["body"] or "").replace("\r\n", "\n").strip() != body.strip():
            out["spacing"].append(title)
    taken = {r["id"] for r in matched.values()}
    out["only_local"] = [r["title"] for r in rows if r["id"] not in taken]
    if [r["id"] for r in rows if r["id"] in taken] != \
            [matched[t]["id"] for t, _ in seed if t in matched]:
        out["state"].append("sections are in a different order")

    base = load_config(business["config_path"])
    for field, value in sorted(db.get_config_overrides(business["id"]).items()):
        if field.endswith(DRAFT_SUFFIX):
            out["drafts"].append(f"{field}: {_short(value)}")
            continue
        try:
            was = get_nested(base, field)
        except Exception:
            was = None
        out["overrides"].append(f"{field}: {_changed(was, value)}"
                                if was is not None else f"{field}: {_short(value)}")
    fresh = db.get_business_by_id(business["id"])
    if fresh and fresh.get("documents_dirty"):
        out["state"].append("unpublished knowledge-base changes")
    if db.get_upload_proposal(business["id"]):
        out["state"].append("an upload waiting to be reviewed")
    return out


def report(business, diff):
    lines = []
    for title in diff["sections"]:
        lines.append(f"  section differs:        {title}")
    for item in diff["titles"]:
        lines.append(f"  title differs:          {item}")
    for title in diff["only_local"]:
        lines.append(f"  section only here:      {title}")
    for title in diff["only_seed"]:
        lines.append(f"  section missing here:   {title}")
    for item in diff["overrides"]:
        lines.append(f"  override:               {item}")
    for item in diff["drafts"]:
        lines.append(f"  draft (unpublished):    {item}")
    for item in diff["state"]:
        lines.append(f"  also:                   {item}")
    head = f"{business['slug']}: " + ("matches the seed" if not lines else
                                       f"{len(lines)} difference{'' if len(lines) == 1 else 's'}")
    if diff["spacing"]:
        # Line breaks the bot can't tell apart: worth knowing, not a difference.
        n = len(diff["spacing"])
        head += (f" ({n} section{' differs' if n == 1 else 's differ'} only in line breaks;"
                 f" --reset tidies {'it' if n == 1 else 'them'})")
    return "\n".join([head] + lines)


def matters(diff):
    """Differences the bot or the owner could notice (spacing alone can't)."""
    return any(diff[k] for k in ("sections", "titles", "only_local", "only_seed",
                                 "overrides", "drafts", "state"))


def backup():
    """Copy the database to backups/chatbot-<time>.db; returns the path."""
    import db
    BACKUPS.mkdir(exist_ok=True)
    dest = BACKUPS / f"chatbot-{datetime.now():%Y%m%d-%H%M%S}.db"
    shutil.copy2(db.DB_PATH, dest)
    return dest


def reset(business, rebuild_index=True):
    """Put one business back to its seed. Returns a list of what was done."""
    import db
    done = []
    seed = seed_sections(business["slug"])
    current = db.get_documents(business["id"])
    matched = _match(seed, current)
    for position, (title, body) in enumerate(seed):
        row = matched.get(title)
        if row is None:
            db.add_document_section(business["id"], title, body, position=position,
                                    updated_by="seed reset")
            done.append(f"added {title}")
            continue
        if (row["title"], (row["body"] or "").strip()) != (title, body.strip()):
            db.update_document_section(row["id"], title, body, updated_by="seed reset")
            done.append(f"reset {title}")
        if row["position"] != position:
            conn = db.get_connection()
            conn.execute("UPDATE documents SET position = ? WHERE id = ?", (position, row["id"]))
            conn.commit()
            conn.close()
    kept = {r["id"] for r in matched.values()}
    for row in current:
        if row["id"] not in kept:
            db.delete_document_section(row["id"])
            done.append(f"deleted {row['title']} (it's in the backup)")
    for field in db.get_config_overrides(business["id"]):
        db.clear_config_override(business["id"], field)
        done.append(f"cleared {field}")
    if db.get_upload_proposal(business["id"]):
        db.delete_upload_proposal(business["id"])
        done.append("dropped the upload waiting for review")
    db.set_documents_clean(business["id"])
    if rebuild_index:
        import rag
        from config import load_config
        rag.ingest_documents(load_config(business["config_path"], business["id"]),
                             business["id"])
        done.append("rebuilt the search index")
    return done


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    import db
    db.init_db()
    slugs = [a for a in argv if not a.startswith("-")]
    businesses = [b for b in db.get_all_businesses()
                  if not slugs or b["slug"] in slugs]
    unknown = set(slugs) - {b["slug"] for b in businesses}
    if unknown:
        print(f"no such business: {', '.join(sorted(unknown))}")
        return 2

    if "--reset" not in argv:
        drifted = 0
        for b in businesses:
            diff = differences(b)
            drifted += matters(diff)
            print(report(b, diff) + "\n")
        print(f"{len(businesses) - drifted}/{len(businesses)} match the seed"
              + ("" if not drifted else "; reset one with: python3 seed_check.py <slug> --reset"))
        return 1 if drifted else 0

    if len(slugs) != 1:
        print("--reset takes exactly one business: python3 seed_check.py <slug> --reset")
        return 2
    business = businesses[0]
    print(report(business, differences(business)))
    if "--yes" not in argv:
        answer = input(f"\nReset {business['slug']} to the seed? Type its slug to go ahead: ")
        if answer.strip() != business["slug"]:
            print("Nothing changed.")
            return 1
    print(f"Backed up the database to {backup().relative_to(ROOT)}")
    for line in reset(business, rebuild_index="--no-index" not in argv):
        print(f"  {line}")
    print(report(business, differences(business)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

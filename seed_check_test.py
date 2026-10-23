#!/usr/bin/env python3
"""seed_check_test.py — the local-vs-seed check, and the reset.

Run it:  python3 seed_check_test.py

Uses a throwaway database: one business seeded from Bob's files, then
changed the ways the local database drifted this week (a section edited,
one added, one renamed by a longer Topics line, an override, a draft, an
upload waiting) and checked, reset, and checked again. A second business
and the first one's own records (an appointment, a calendar block) must
come through untouched. No network: the index rebuild is skipped.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import db
TMP = Path(tempfile.mkdtemp(prefix="seed_check_test_"))
db.DB_PATH = TMP / "test.db"

import seed_check

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def main():
    from import_documents import import_for_business
    db.init_db()
    bob_id = db.add_business("Bob's Plumbing", "bobs_plumbing", "config/bobs_plumbing.yaml")
    sun_id = db.add_business("Sunrise", "sunrise_bakery_and_cafe",
                             "config/sunrise_bakery_and_cafe.yaml")
    for b in (bob_id, sun_id):
        import_for_business(db.get_business_by_id(b))
    bob = db.get_business_by_slug("bobs_plumbing")

    heading("straight from the seed, nothing differs")
    diff = seed_check.differences(bob)
    check("a business just seeded matches", not seed_check.matters(diff), diff)

    heading("the ways it drifted, each listed")
    rows = {r["title"]: r for r in db.get_documents(bob_id)}
    emergency = rows["Emergency Service"]
    db.update_document_section(emergency["id"], emergency["title"],
                               emergency["body"].replace("$75", "$90"), updated_by="t")
    drain = rows["Drain Cleaning"]
    db.update_document_section(drain["id"], drain["title"],
                               drain["body"].replace("\n", " "), updated_by="t")   # re-wrapped
    leak = rows["Leak Repair"]
    db.update_document_section(leak["id"], "Leak Repair (Topics: old)", leak["body"],
                               updated_by="t")
    db.add_document_section(bob_id, "Drywall Repair", "We patch drywall.", updated_by="t")
    db.set_config_override(bob_id, "business.phone", "(585) 555-0124")
    db.set_config_override(bob_id, "notice_draft", {"drain cleaning": {"hours": 24}})
    db.save_upload_proposal(bob_id, "a photo", [{"kind": "x"}], created_by="t")
    db.save_appointment("web-1", "drain cleaning", "2026-12-01T10:00", bob_id)
    db.add_time_block(bob_id, "2026-12-02 09:00", "2026-12-02 17:00", note="private")
    sunrise_before = [(r["title"], r["body"]) for r in db.get_documents(sun_id)]

    diff = seed_check.differences(bob)
    text = seed_check.report(bob, diff)
    check("a section whose words changed", "Emergency Service" in diff["sections"], diff)
    check("a section only here", diff["only_local"] == ["Drywall Repair"], diff)
    check("a renamed section is one section with a different title, not two",
          any("Leak Repair (Topics: old)" in t for t in diff["titles"])
          and not diff["only_seed"], diff)
    check("line breaks alone aren't a difference, but they're mentioned",
          "Drain Cleaning" in diff["spacing"] and "Drain Cleaning" not in diff["sections"]
          and "only in line breaks" in text, text)
    check("an override, with where it differs",
          any("business.phone" in o and "0123 -> " in o and "0124" in o
              for o in diff["overrides"]), diff["overrides"])
    check("a draft", any(d.startswith("notice_draft") for d in diff["drafts"]), diff["drafts"])
    check("unpublished changes and an upload waiting",
          any("unpublished" in s for s in diff["state"])
          and any("upload" in s for s in diff["state"]), diff["state"])
    check("the check says it differs (exit code 1)", seed_check.main(["bobs_plumbing"]) == 1)

    heading("reset: back to the seed, after a backup")
    before = set((ROOT / "backups").glob("chatbot-*.db"))
    done = seed_check.main(["bobs_plumbing", "--reset", "--yes", "--no-index"])
    ours = sorted(set((ROOT / "backups").glob("chatbot-*.db")) - before)
    backups = ours
    check("it ran", done == 0)
    check("the database was copied to backups/ first", backups and backups[-1].stat().st_size > 0)
    diff = seed_check.differences(db.get_business_by_slug("bobs_plumbing"))
    check("now it matches the seed, line breaks and all",
          not seed_check.matters(diff) and not diff["spacing"], diff)
    titles = [r["title"] for r in db.get_documents(bob_id)]
    check("sections in the seed's order", titles == [t for t, _ in seed_check.seed_sections("bobs_plumbing")],
          titles)
    check("the renamed section was reset in place (its History is kept)",
          any(r["id"] == leak["id"] and r["title"] == "Leak Repair" for r in db.get_documents(bob_id)))
    check("overrides and drafts cleared, the upload dropped, nothing unpublished",
          db.get_config_overrides(bob_id) == {} and db.get_upload_proposal(bob_id) is None
          and not db.get_business_by_id(bob_id)["documents_dirty"])
    check("the business's own records are untouched (appointment, calendar block)",
          len(db.get_appointments(bob_id)) == 1 and len(db.list_time_blocks(bob_id)) == 1)
    check("another business is untouched",
          [(r["title"], r["body"]) for r in db.get_documents(sun_id)] == sunrise_before)
    check("and the check agrees (exit code 0)", seed_check.main(["bobs_plumbing"]) == 0)

    heading("asked, not assumed")
    import builtins
    real_input = builtins.input
    builtins.input = lambda prompt="": "no"
    try:
        db.set_config_override(bob_id, "business.phone", "(585) 555-0199")
        check("without --yes, a wrong answer changes nothing",
              seed_check.main(["bobs_plumbing", "--reset", "--no-index"]) == 1
              and db.get_config_overrides(bob_id).get("business.phone") == "(585) 555-0199")
    finally:
        builtins.input = real_input
    check("--reset needs exactly one business", seed_check.main(["--reset"]) == 2)
    check("an unknown business is refused", seed_check.main(["nobody_here"]) == 2)
    check("backups/ is never committed",
          "backups/" in (ROOT / ".gitignore").read_text())
    for b in ours:
        b.unlink()                       # only the copy this test made of its throwaway database

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""demo_test.py — exercise demo tenancy against a throwaway database.

Run it:  python3 demo_test.py

Everything here happens in a temporary SQLite file, never chatbot.db. That
is not politeness — the code under test deletes businesses, and a test that
deletes is a test that has to be pointed somewhere it can't do harm.

What it checks, in one sentence each:
  * a clone is a separate tenant, not a view of the template
  * the visitor's edits never reach the business they cloned
  * two demos running at once can't see each other's bookings
  * teardown takes every row with it and leaves no orphans
  * teardown refuses a business that isn't a demo
  * a demo that publishes gets its own collection before it writes to one
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# Point the database at a temp file BEFORE anything reads db.DB_PATH.
_tmpdir = tempfile.mkdtemp(prefix="demo_test_")
import db
db.DB_PATH = Path(_tmpdir) / "test.db"

import demo
from config import load_config
from db import (add_business, add_document_section, get_all_businesses,
                get_appointments, get_business_by_id, get_config_overrides,
                get_connection, get_documents, get_recent_messages,
                get_state, get_user_by_email, init_db, save_appointment,
                save_message, set_config_override, set_state,
                verify_password)

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    mark = "PASS" if condition else "FAIL"
    line = f"  [{mark}] {name}"
    if detail and not condition:
        line += f"\n         {detail}"
    print(line)


def heading(text):
    print(f"\n{text}")
    print("-" * len(text))


# ---------------------------------------------------------------------------
# Fixture: one template business that looks like a real client
# ---------------------------------------------------------------------------

def build_template():
    init_db()
    business_id = add_business(
        name="Bob's Plumbing",
        slug="bobs_plumbing",
        config_path="config/bobs_plumbing.yaml",
        twilio_number="+15550001111",
    )
    set_config_override(business_id, "business.phone", "555-0100",
                        updated_by="owner@example.com")
    set_config_override(business_id, "booking.services",
                        ["Drain cleaning", "Water heater repair"],
                        updated_by="owner@example.com")
    add_document_section(business_id, "Hours", "Mon-Fri 8am-6pm.")
    add_document_section(business_id, "Service area", "Buffalo and suburbs.")
    db.set_documents_clean(business_id)
    return get_business_by_id(business_id)


def row_counts(business_id):
    """How many rows each tenant-scoped table holds for one business."""
    conn = get_connection()
    counts = {}
    for table, column in db._TENANT_TABLES:
        counts[table] = conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = ?",
            (business_id,)).fetchone()["n"]
    counts["documents"] = conn.execute(
        "SELECT COUNT(*) AS n FROM documents WHERE business_id = ?",
        (business_id,)).fetchone()["n"]
    conn.close()
    return counts


def orphan_versions():
    """document_versions rows whose document no longer exists."""
    conn = get_connection()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM document_versions v "
        "WHERE NOT EXISTS (SELECT 1 FROM documents d WHERE d.id = v.document_id)"
    ).fetchone()["n"]
    conn.close()
    return n


def age(business_id, minutes):
    """Backdate a business's last activity, to simulate an idle visitor."""
    stamp = (datetime.now() - timedelta(minutes=minutes)).isoformat()
    conn = get_connection()
    conn.execute("UPDATE businesses SET last_seen_at = ? WHERE id = ?",
                 (stamp, business_id))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------

def main():
    template = build_template()

    heading("Cloning")
    minted = demo.clone_template("bobs_plumbing")
    check("clone_template returns a clone", minted is not None)
    if not minted:
        return finish()

    clone = minted["business"]
    check("clone is a different business", clone["id"] != template["id"])
    check("clone is flagged as a demo", clone["is_demo"] == 1)
    check("clone remembers its template", clone["demo_of"] == template["id"])
    check("clone has no phone number", clone["twilio_number"] is None)
    check("clone slug is recognisable and unique",
          clone["slug"].startswith("demo-bobs_plumbing-")
          and clone["slug"] != template["slug"], clone["slug"])
    check("clone shares the template's collection",
          clone["rag_collection"] == template["slug"], clone["rag_collection"])
    check("clone shares the template's config file",
          clone["config_path"] == template["config_path"])

    heading("What the visitor starts with")
    check("overrides copied",
          get_config_overrides(clone["id"]) == get_config_overrides(template["id"]))
    check("documents copied",
          [(d["title"], d["body"]) for d in get_documents(clone["id"])]
          == [(d["title"], d["body"]) for d in get_documents(template["id"])])
    check("clone does not open with unpublished changes",
          get_business_by_id(clone["id"])["documents_dirty"] == 0)
    check("clone has its own owner login",
          verify_password(minted["email"], minted["password"]) is not None)
    user = get_user_by_email(minted["email"])
    check("that login is scoped to the clone, not an operator",
          user["business_id"] == clone["id"] and not user["is_operator"])

    heading("What the visitor can't reach")
    set_config_override(clone["id"], "business.phone", "555-9999",
                        updated_by="demo-visitor")
    check("editing the clone leaves the template's settings alone",
          get_config_overrides(template["id"])["business.phone"] == "555-0100",
          get_config_overrides(template["id"]).get("business.phone"))

    # The costliest thing a demo could reach is not data — it's the real
    # Google calendar named in the template's YAML, which a clone shares by
    # sharing the file. A visitor's pretend booking there would be a real
    # entry in a real business's week.
    demo_config = load_config(clone["config_path"], clone["id"])
    real_config = load_config(template["config_path"], template["id"])
    check("a demo is forced onto the simulated calendar",
          demo_config["calendar"]["provider"] == "simulated",
          demo_config["calendar"].get("provider"))
    check("the template keeps its real calendar",
          real_config["calendar"].get("provider") in (None, "google"),
          real_config["calendar"].get("provider"))

    other = demo.clone_template("bobs_plumbing")["business"]
    save_message("visitor-a", "user", "hello", clone["id"], source="webchat")
    save_appointment("visitor-a", "Drain cleaning", "2026-09-20T10:00:00",
                     clone["id"])
    set_state("visitor-a", clone["id"], "collecting", {"service": "Drain cleaning"})
    check("one demo's messages are invisible to another",
          get_recent_messages("visitor-a", other["id"]) == [])
    check("one demo's bookings are invisible to another",
          get_appointments(other["id"]) == []
          and len(get_appointments(clone["id"])) == 1)
    check("one demo's booking state is invisible to another",
          get_state("visitor-a", other["id"])["state"] == "idle")
    check("the template sees none of it",
          get_appointments(template["id"]) == []
          and get_recent_messages("visitor-a", template["id"]) == [])

    heading("Listing")
    listed = {b["slug"] for b in get_all_businesses()}
    check("demos stay out of the operator dashboard",
          listed == {template["slug"]}, sorted(listed))
    listed_all = {b["slug"] for b in get_all_businesses(include_demo=True)}
    check("demos are there when asked for",
          clone["slug"] in listed_all and other["slug"] in listed_all)

    heading("Forking the knowledge base")
    check("forking is a no-op for a real business",
          demo.fork_collection(template["id"]) == template["slug"]
          and get_business_by_id(template["id"])["rag_collection"] == template["slug"])
    forked = demo.fork_collection(clone["id"])
    check("a publishing demo stops sharing", forked == clone["slug"])
    check("and the template's collection is untouched",
          get_business_by_id(template["id"])["rag_collection"] == template["slug"])
    check("forking twice changes nothing",
          demo.fork_collection(clone["id"]) == clone["slug"])
    check("the demo that never published still shares",
          get_business_by_id(other["id"])["rag_collection"] == template["slug"])

    heading("Refusing to clone the wrong thing")
    check("a template that doesn't exist is refused",
          demo.clone_template("no-such-business") is None)
    check("a demo cannot be cloned", demo.clone_template(clone["slug"]) is None)

    heading("The ceiling")
    ceiling = demo.MAX_LIVE
    demo.MAX_LIVE = 2
    try:
        check("a third demo is refused when two is the limit",
              demo.clone_template("bobs_plumbing") is None)
        # The ceiling must not turn into a permanent lockout the first busy
        # afternoon: a full house of expired demos has to make room. This is
        # the branch where clone_template sweeps and then retries, and it is
        # the one the refusal test above would happily hide.
        age(other["id"], minutes=10_000)
        made_room = demo.clone_template("bobs_plumbing")
        check("an expired demo is cleared out to make room",
              made_room is not None
              and get_business_by_id(other["id"]) is None)
        other = made_room["business"] if made_room else other
    finally:
        demo.MAX_LIVE = ceiling

    heading("Teardown")
    before = row_counts(clone["id"])
    check("the clone had rows worth deleting", all(before.values()), before)

    kept = demo.sweep_idle(idle_minutes=30)
    check("a demo in active use survives the sweep",
          kept == [] and get_business_by_id(clone["id"]) is not None, kept)

    age(clone["id"], minutes=90)
    swept = demo.sweep_idle(idle_minutes=30)
    check("an idle demo is swept", swept == [clone["slug"]], swept)
    check("the swept demo's row is gone", get_business_by_id(clone["id"]) is None)
    after = row_counts(clone["id"])
    check("every scoped row went with it", not any(after.values()), after)
    check("no document versions were orphaned", orphan_versions() == 0)
    check("the sweep left the other demo alone",
          get_business_by_id(other["id"]) is not None)
    check("the sweep left the template alone",
          get_business_by_id(template["id"]) is not None
          and len(get_documents(template["id"])) == 2)

    heading("Refusing to delete the wrong thing")
    check("a real business is not deletable as a demo",
          db.delete_business(template["id"]) is None
          and get_business_by_id(template["id"]) is not None)
    check("its rows are still there",
          len(get_config_overrides(template["id"])) == 2)

    heading("Which collection teardown is allowed to delete")
    # The check that matters most in this file. A demo that never published
    # is still pointing at a real client's vector collection, and dropping it
    # would empty that client's knowledge base with nothing but a slightly
    # stupider bot to show for it. chromadb isn't imported here, so stand a
    # recorder in its place and assert on what teardown *asked* to delete.
    dropped = install_fake_chroma()
    sharing = demo.clone_template("bobs_plumbing")["business"]
    age(sharing["id"], minutes=90)
    demo.sweep_idle(idle_minutes=30)
    check("sweeping a demo that never published drops no collection",
          dropped == [], dropped)

    published = demo.clone_template("bobs_plumbing")["business"]
    demo.fork_collection(published["id"])
    age(published["id"], minutes=90)
    demo.sweep_idle(idle_minutes=30)
    check("sweeping a demo that did publish drops only its own",
          dropped == [published["slug"]], dropped)

    heading("Touch")
    age(other["id"], minutes=90)
    demo.touch(other["id"])
    check("touching a demo saves it from the next sweep",
          demo.sweep_idle(idle_minutes=30) == []
          and get_business_by_id(other["id"]) is not None)

    heading("Sweeping everything (what a restart does)")
    demo.sweep_all()
    check("no demos survive", get_demo_count() == 0)
    check("the template is still there",
          get_business_by_id(template["id"]) is not None)

    return finish()


def install_fake_chroma():
    """Stand a recorder where rag.get_chroma_client() would be.

    Returns the list that collects every collection name teardown tries to
    delete. The contract copied here is the one rag.py itself uses against
    the real client — client.delete_collection(name) — so a change in that
    API breaks both together rather than leaving this quietly passing.
    """
    import types
    dropped = []

    class _Client:
        def delete_collection(self, name):
            dropped.append(name)

    fake = types.ModuleType("rag")
    fake.get_chroma_client = lambda: _Client()
    sys.modules["rag"] = fake
    return dropped


def get_demo_count():
    return len(db.get_demo_businesses())


def finish():
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for name in FAILED:
            print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        import shutil
        shutil.rmtree(_tmpdir, ignore_errors=True)

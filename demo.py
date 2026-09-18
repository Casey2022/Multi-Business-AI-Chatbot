# demo.py — one throwaway business per visitor to the public demo.
#
# The problem this solves: a demo that shows a real client's portal lets a
# stranger edit that client's settings, and a single shared demo business lets
# two visitors overwrite each other's edits mid-sentence. Neither is a demo
# anyone would want to leave running. So each visitor gets their own business
# — a full tenant, cloned from a template, deleted when they stop using it.
#
# It's the cheapest possible clone on purpose. A business is a row plus its
# overrides plus its documents; none of that costs anything to copy. The one
# expensive part is the vector collection, which would cost an embedding call
# per section per visitor — so a fresh clone reads its template's collection
# instead, and only builds one of its own if the visitor publishes a change
# (see fork_collection, which is the sharp edge of that arrangement).
#
# Nothing here touches a business that isn't flagged as a demo.

import logging
import os
import secrets
from datetime import datetime, timedelta

from db import (
    add_business,
    add_document_section,
    create_user,
    MIN_PASSWORD_LENGTH,
    delete_business,
    get_business_by_id,
    get_business_by_slug,
    get_config_overrides,
    get_demo_businesses,
    get_documents,
    set_config_override,
    set_documents_clean,
    set_rag_collection,
    touch_business,
)

log = logging.getLogger("demo")

SLUG_PREFIX = "demo-"

# How long a demo survives with nobody talking to it. Long enough that a
# visitor can read the docs mid-conversation and come back; short enough that
# an afternoon of traffic doesn't leave hundreds of tenants lying around.
IDLE_MINUTES = int(os.environ.get("DEMO_IDLE_MINUTES", "60"))

# A ceiling on live demos. /demo is a public endpoint that creates database
# rows, and the rate limiter caps how fast one caller can do that but not how
# many exist in total. Without a ceiling, a slow trickle from many addresses
# fills the database and nothing ever says so.
MAX_LIVE = int(os.environ.get("DEMO_MAX_LIVE", "50"))


# The businesses offered on the picker, in the order they appear. The blurb
# says what this one does that the others don't — the point of showing five
# is five different mechanics, not five different logos. Names and any other
# detail come from the live row, so nothing here can drift from what the
# visitor actually gets.
TEMPLATES = [
    {
        "slug":  "bobs_plumbing",
        "kind":  "Home services",
        "blurb": "One job at a time, 45 minutes to drive between them, and "
                 "the customer's address checked against a 20-mile radius "
                 "before anyone agrees to come out.",
    },
    {
        "slug":  "belmont_hair_studio",
        "kind":  "Salon",
        "blurb": "Three chairs, and appointments that aren't the same "
                 "length — a blowout takes half an hour, a balayage takes "
                 "three, and the diary knows the difference.",
    },
    {
        "slug":  "crosstown_pizza",
        "kind":  "Takeaway",
        "blurb": "Six orders in the oven at once on a 15-minute clock, and "
                 "the same address check deciding whether it's a delivery "
                 "or a pickup.",
    },
    {
        "slug":  "ridgeline_contracting",
        "kind":  "Trades",
        "blurb": "Books the free estimate, never the job — 90-minute visits "
                 "days out, across 35 miles, with a bot that refuses to "
                 "quote a price.",
    },
    {
        "slug":  "sunrise_bakery_and_cafe",
        "kind":  "Food",
        "blurb": "Orders rather than appointments: three pickups can share "
                 "one slot, and the lead time depends on what you ordered.",
    },
]


def catalogue():
    """The templates to show on the picker, each with its live name.

    A template missing from the database is skipped rather than raised on:
    a half-seeded install should show four cards, not a 500.
    """
    out = []
    for entry in TEMPLATES:
        row = get_business_by_slug(entry["slug"])
        if row and not is_demo(row):
            out.append({**entry, "name": row["name"]})
    return out


def is_demo(business):
    """True if this business row is a demo clone."""
    return bool((business or {}).get("is_demo"))


# ---------------------------------------------------------------------------
# Cloning
# ---------------------------------------------------------------------------

def clone_template(template_slug, now=None):
    """Clone a template business into a throwaway one. Returns a dict:

        {"business": <row>, "email": <str>, "password": <str>}

    email/password are a freshly minted owner login scoped to the clone, so
    the caller can drop the visitor straight into the portal. They are
    returned once and never stored in readable form — the users table keeps
    only a bcrypt hash, exactly as it does for a real client.

    Returns None if the template doesn't exist or the demo ceiling is full.
    """
    template = get_business_by_slug(template_slug)
    if not template:
        log.warning("No template business with slug %r", template_slug)
        return None
    if is_demo(template):
        # Cloning a clone would copy a forked collection and a visitor's
        # half-finished edits, and demo_of would point at something that is
        # about to be swept. Templates are real businesses only.
        log.warning("Refused to clone %r — it is itself a demo", template_slug)
        return None

    live = get_demo_businesses()
    if len(live) >= MAX_LIVE:
        # Try to make room from expired ones before turning anybody away.
        sweep_idle(now=now)
        live = get_demo_businesses()
        if len(live) >= MAX_LIVE:
            log.warning("Demo ceiling reached (%d live) — refusing a new clone",
                        len(live))
            return None

    token = secrets.token_hex(3)
    slug = f"{SLUG_PREFIX}{template['slug']}-{token}"

    business_id = add_business(
        name=template["name"],
        slug=slug,
        config_path=template["config_path"],
        twilio_number=None,          # demos are web chat only, never a number
        is_demo=True,
        demo_of=template["id"],
        # Share the template's knowledge base until this visitor changes it.
        rag_collection=template["rag_collection"] or template["slug"],
    )

    # Config: copy the overrides, not the YAML. The YAML is reached through
    # the shared config_path, so the clone inherits any later fix to it; the
    # overrides are what makes the template look the way an owner left it.
    for field, value in get_config_overrides(template["id"]).items():
        set_config_override(business_id, field, value, updated_by="demo")

    # Documents: copied as rows so the visitor sees a populated knowledge base
    # and can edit it. Copying marks the clone dirty ("needs publishing"),
    # which is false — nothing has changed yet — so clear it.
    for section in get_documents(template["id"]):
        add_document_section(business_id, section["title"], section["body"],
                             position=section["position"], updated_by="demo")
    set_documents_clean(business_id)

    email = f"{slug}@demo.invalid"
    # Long enough to satisfy the password policy whatever it is set to;
    # token_urlsafe(n) yields roughly 1.3 characters per byte.
    password = secrets.token_urlsafe(max(16, MIN_PASSWORD_LENGTH))
    create_user(email, password, business_id=business_id, is_operator=False)

    business = get_business_by_id(business_id)
    log.info("Cloned %s -> %s (id=%s)", template["slug"], slug, business_id)
    return {"business": business, "email": email, "password": password}


def fork_collection(business_id):
    """Give a demo its own vector collection before it writes to one.

    This is the one thing a shared collection cannot survive. A demo clone
    retrieves from its template's collection, and re-ingesting rebuilds the
    collection named on the config — so a visitor pressing Publish would
    rebuild the *template's* knowledge base out of their own edited
    documents. The client's bot would then start answering from a stranger's
    text, with nothing in any log saying why.

    So every path that re-ingests calls this first. For a real business it is
    a no-op; for a demo still sharing, it repoints the row at a collection
    named after its own slug, which nothing else uses. Returns the collection
    name to ingest into.
    """
    business = get_business_by_id(business_id)
    if not business:
        return None
    current = business["rag_collection"] or business["slug"]
    if not is_demo(business) or current == business["slug"]:
        return current
    set_rag_collection(business_id, business["slug"])
    log.info("Demo %s forked off collection %r", business["slug"], current)
    return business["slug"]


def touch(business_id):
    """Mark a demo as still in use. Safe to call for any business."""
    touch_business(business_id)


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------

def sweep_idle(idle_minutes=None, now=None):
    """Delete demos nobody has used lately. Returns the slugs removed.

    Called opportunistically rather than on a timer: there is no scheduler in
    this app, and adding a background thread that deletes rows is a much
    larger promise than a sweep that runs when someone asks for a demo. The
    cost of a late sweep is a row that lives too long, which is the failure
    this can afford.
    """
    minutes = IDLE_MINUTES if idle_minutes is None else idle_minutes
    now = now or datetime.now()
    cutoff = (now - timedelta(minutes=minutes)).isoformat()

    removed = []
    for row in get_demo_businesses(idle_before=cutoff):
        _drop_forked_collection(row)
        if delete_business(row["id"]):
            removed.append(row["slug"])
    if removed:
        log.info("Swept %d idle demo(s): %s", len(removed), ", ".join(removed))
    return removed


def sweep_all():
    """Delete every demo. For a restart: no visitor's session outlives one."""
    removed = []
    for row in get_demo_businesses():
        _drop_forked_collection(row)
        if delete_business(row["id"]):
            removed.append(row["slug"])
    if removed:
        log.info("Removed %d demo(s) left from a previous run: %s",
                 len(removed), ", ".join(removed))
    return removed


def _drop_forked_collection(business):
    """Delete a demo's vector collection, but only one it owns.

    The guard is the whole point: a demo that never published is still
    pointing at its template's collection, and deleting that would take a
    real client's knowledge base with it. Best-effort otherwise — a leftover
    collection wastes disk, while an exception here would abandon the sweep
    partway and leave the database rows behind too.
    """
    collection = business.get("rag_collection")
    if not collection or collection != business["slug"]:
        return
    try:
        # Imported here, not at module scope: chromadb is slow to import and
        # pulls the embedding backend with it, and everything above this
        # function works without either.
        from rag import get_chroma_client
        get_chroma_client().delete_collection(collection)
        log.info("Dropped demo collection %r", collection)
    except Exception as e:
        log.warning("Could not drop collection %r: %s", collection, e)

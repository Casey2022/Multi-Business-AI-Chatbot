# seed_businesses.py — register businesses in the database.
#
# Idempotent: a business already registered is left alone, so this is safe
# to run on every boot and safe to run by hand any number of times. That
# matters more than it sounds. The old version only ran when the businesses
# table was completely empty, which meant adding a new business to the list
# below did nothing at all on a machine that already had two — the row never
# appeared, nothing said why, and the demo picker quietly showed three cards
# short.
#
# Usage:
#   python3 seed_businesses.py

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Nothing here needs an API key. Running outside the virtualenv should
    # still seed the database rather than dying on an import three lines in.
    pass

import db
from db import init_db, add_business, get_business_by_slug, get_connection

BUSINESSES = [
    {
        "name":           "Bob's Plumbing",
        "slug":           "bobs_plumbing",
        "twilio_number":  "+15855550123",   # E.164 — must match Twilio's "To" field exactly
        "config_path":    "config/bobs_plumbing.yaml",
    },
    {
        "name":           "Sunrise Bakery & Café",
        "slug":           "sunrise_bakery_and_cafe",
        "twilio_number":  "+15855550188",
        "config_path":    "config/sunrise_bakery_and_cafe.yaml",
    },

    # Demo templates. No Twilio number — they exist to be cloned for
    # visitors to the public demo, not to answer a real phone. Each one is
    # here because it exercises something the others don't:
    #   Ridgeline  — long estimate visits over a wide service radius
    #   Belmont    — appointments of different lengths sharing 3 chairs
    #   Crosstown  — the radius deciding delivery rather than travel,
    #                on a 15-minute clock with 6 orders at a time
    {
        "name":           "Ridgeline Contracting",
        "slug":           "ridgeline_contracting",
        "twilio_number":  None,
        "config_path":    "config/ridgeline_contracting.yaml",
    },
    {
        "name":           "Belmont Hair Studio",
        "slug":           "belmont_hair_studio",
        "twilio_number":  None,
        "config_path":    "config/belmont_hair_studio.yaml",
    },
    {
        "name":           "Crosstown Pizza Co.",
        "slug":           "crosstown_pizza",
        "twilio_number":  None,
        "config_path":    "config/crosstown_pizza.yaml",
    },
]


def seed(quiet=False):
    """Register any business in BUSINESSES that isn't in the database yet.

    Returns the list of slugs added. Existing businesses are checked by slug
    and skipped — not re-inserted and caught as a constraint error, which is
    what the old version did and which made a normal run look like a failure.
    """
    init_db()

    def say(*args):
        if not quiet:
            print(*args)

    # Printed because getting this wrong is silent and expensive: run from
    # the wrong directory with the old relative path and you seeded a
    # different file than the one the app reads.
    say(f"\n[seed] Database: {db.DB_PATH}\n")

    added = []
    for b in BUSINESSES:
        if get_business_by_slug(b["slug"]):
            say(f"  · {b['name']} — already registered")
            continue
        try:
            business_id = add_business(
                name          = b["name"],
                slug          = b["slug"],
                config_path   = b["config_path"],
                twilio_number = b["twilio_number"],
            )
            added.append(b["slug"])
            say(f"  + {b['name']} registered (id={business_id}, "
                f"slug={b['slug']})")
        except Exception as e:
            say(f"  ! Could not register '{b['name']}': {e}")

    say(f"\n[seed] {len(added)} added, "
        f"{len(BUSINESSES) - len(added)} already present.")
    return added


def show():
    """Print the businesses table, so a run ends with the actual state."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name, slug, twilio_number, active, "
        "COALESCE(is_demo, 0) AS is_demo FROM businesses ORDER BY id"
    ).fetchall()
    conn.close()

    print("\n[seed] Businesses now in the database:")
    print(f"  {'id':<4} {'name':<28} {'twilio':<16} {'active':<7} kind")
    print(f"  {'-'*4} {'-'*28} {'-'*16} {'-'*7} ----")
    for row in rows:
        kind = "demo clone" if row["is_demo"] else "business"
        print(f"  {row['id']:<4} {row['name']:<28} "
              f"{str(row['twilio_number'] or '—'):<16} {row['active']:<7} {kind}")
    print()


if __name__ == "__main__":
    seed()
    show()

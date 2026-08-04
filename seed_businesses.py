# seed_businesses.py — register businesses in the database.
#
# Run this ONCE after deleting chatbot.db to populate the businesses table.
# Safe to run again — existing businesses are skipped, not duplicated.
#
# Usage:
#   python3 seed_businesses.py

from dotenv import load_dotenv
load_dotenv()

from db import init_db, add_business, get_connection

def seed():
    # Make sure all tables exist before we try to insert.
    init_db()

    businesses = [
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
    ]

    print("\n[seed] Registering businesses...\n")

    for b in businesses:
        try:
            business_id = add_business(
                name          = b["name"],
                slug          = b["slug"],
                config_path   = b["config_path"],
                twilio_number = b["twilio_number"],
            )
            print(f"  ✓ {b['name']} registered (id={business_id})")
            print(f"    slug:          {b['slug']}")
            print(f"    twilio_number: {b['twilio_number']}")
            print(f"    config_path:   {b['config_path']}\n")

        except Exception as e:
            # Most likely a UNIQUE constraint violation — business already exists.
            print(f"  ⚠ Skipped '{b['name']}' — already registered or error: {e}\n")

    # Print the current state of the businesses table so we can verify.
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name, slug, twilio_number, config_path, active FROM businesses"
    ).fetchall()
    conn.close()

    print("[seed] Current businesses table:")
    print(f"  {'id':<4} {'name':<28} {'twilio_number':<16} {'active'}")
    print(f"  {'-'*4} {'-'*28} {'-'*16} {'-'*6}")
    for row in rows:
        print(f"  {row['id']:<4} {row['name']:<28} {str(row['twilio_number']):<16} {row['active']}")



if __name__ == "__main__":
    seed()
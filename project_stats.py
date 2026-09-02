# project_stats.py — real usage figures from the database.
# Replaces guesswork in the portfolio write-up with numbers that can be
# regenerated rather than hand-counted once and going stale.
# Usage:
#  python3 project_stats.py

from collections import Counter
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from db import get_connection


def main():
    conn = get_connection()

    def q(sql, params=()):
        return conn.execute(sql, params).fetchall()

    print("=" * 62)
    print("  PROJECT STATISTICS")
    print(f"  Generated {datetime.now():%B %d, %Y}")
    print("=" * 62)

    # --- Businesses ---
    businesses = q("SELECT id, name, active FROM businesses ORDER BY id")
    active = [b for b in businesses if b["active"]]
    print(f"\nBUSINESSES")
    print(f"  Configured:            {len(businesses)}")
    print(f"  Active:                {len(active)}")
    for b in businesses:
        print(f"    - {b['name']}")

    # --- Conversations and messages ---
    total_msgs = q("SELECT COUNT(*) n FROM messages")[0]["n"]
    convos     = q("SELECT COUNT(DISTINCT phone || '|' || business_id) n FROM messages")[0]["n"]
    customer   = q("SELECT COUNT(*) n FROM messages WHERE role = 'user'")[0]["n"]
    bot        = q("SELECT COUNT(*) n FROM messages WHERE role = 'assistant'")[0]["n"]

    print(f"\nCONVERSATIONS")
    print(f"  Unique conversations:  {convos}")
    print(f"  Total messages:        {total_msgs}")
    print(f"    Customer messages:   {customer}")
    print(f"    Bot replies:         {bot}")
    if convos:
        print(f"  Avg messages/convo:    {total_msgs / convos:.1f}")

    # --- Channel split, inferred from the sender id format ---
    web = q("SELECT COUNT(DISTINCT phone) n FROM messages WHERE phone LIKE 'web_%'")[0]["n"]
    sms = q("SELECT COUNT(DISTINCT phone) n FROM messages WHERE phone NOT LIKE 'web_%'")[0]["n"]
    print(f"\nCHANNELS")
    print(f"  Web chat sessions:     {web}")
    print(f"  SMS numbers:           {sms}")

    # --- Which layer answered ---
    # Only rows written since the `source` column was added are meaningful;
    # 'unknown' predates it and would distort the percentages.
    sources = q("""
        SELECT source, COUNT(*) n FROM messages
        WHERE role = 'assistant' AND source != 'unknown'
        GROUP BY source ORDER BY n DESC
    """)
    tracked = sum(s["n"] for s in sources)

    print(f"\nWHICH LAYER PRODUCED THE REPLY   ({tracked} tracked)")
    if tracked:
        labels = {"rule": "Keyword rules", "llm": "LLM + RAG",
                  "scheduler": "Booking flow"}
        for s in sources:
            pct = 100 * s["n"] / tracked
            print(f"  {labels.get(s['source'], s['source']):<22} "
                  f"{s['n']:>5}  ({pct:5.1f}%)")

        # Careful with this one. Only the rules engine is genuinely
        # model-free — the booking flow writes its own reply text, but every
        # booking turn still runs slot extraction, and datetime turns also
        # run the date parser. Counting those as "no LLM" would overstate
        # the architecture's efficiency by about 25x.
        rule_only = next((s["n"] for s in sources if s["source"] == "rule"), 0)
        print(f"\n  Replies with no model call at all: "
              f"{100 * rule_only / tracked:.1f}%")
        print(f"  (booking-flow replies are template text, but each of those "
              f"turns still calls the model for slot extraction)")
    else:
        print("  No tracked replies yet.")

    # --- Bookings ---
    appts     = q("SELECT COUNT(*) n FROM appointments")[0]["n"]
    booked    = q("SELECT COUNT(*) n FROM appointments WHERE status = 'booked'")[0]["n"]
    cancelled = q("SELECT COUNT(*) n FROM appointments WHERE status = 'cancelled'")[0]["n"]
    synced    = q("SELECT COUNT(*) n FROM appointments WHERE sync_status = 'synced'")[0]["n"]
    reconciled= q("SELECT COUNT(*) n FROM appointments WHERE calendar_changed IS NOT NULL")[0]["n"]

    print(f"\nBOOKINGS")
    print(f"  Completed:             {appts}")
    print(f"    Active:              {booked}")
    print(f"    Cancelled:           {cancelled}")
    print(f"  Synced to calendar:    {synced}")
    print(f"  Reconciled from cal:   {reconciled}")

    # --- Custom booking details captured ---
    with_details = q("""
        SELECT COUNT(*) n FROM appointments
        WHERE details IS NOT NULL AND details != '{}'
    """)[0]["n"]
    print(f"  With custom details:   {with_details}")

    # --- Knowledge base ---
    print(f"\nKNOWLEDGE BASE")
    try:
        from pathlib import Path
        from rag import _slugify, chunk_text
        from config import load_config
        total_chunks = 0
        for b in active:
            cfg  = load_config(
                q("SELECT config_path FROM businesses WHERE id = ?", (b["id"],))[0]["config_path"]
            )
            slug = _slugify(cfg["business"]["name"])
            docs = Path("documents") / slug
            if not docs.exists():
                continue
            n = sum(len(chunk_text(f.read_text(encoding="utf-8")))
                    for f in docs.glob("*.md"))
            print(f"  {b['name']:<24} {n} chunks")
            total_chunks += n
        print(f"  {'Total':<24} {total_chunks} chunks")
    except Exception as e:
        print(f"  (could not read documents: {e})")

    # --- Users and config edits ---
    users     = q("SELECT COUNT(*) n FROM users")[0]["n"]
    operators = q("SELECT COUNT(*) n FROM users WHERE is_operator = 1")[0]["n"]
    overrides = q("SELECT COUNT(*) n FROM config_overrides")[0]["n"]

    print(f"\nADMIN")
    print(f"  User accounts:         {users} ({operators} operator)")
    print(f"  Config fields edited:  {overrides}")

    conn.close()
    print("\n" + "=" * 62)
    print("  Note: figures reflect development and testing, not live")
    print("  customer traffic. The booking-flow share is high because")
    print("  booking was the most heavily tested feature; a real mix")
    print("  would be dominated by questions.")
    print("=" * 62 + "\n")


if __name__ == "__main__":
    main()
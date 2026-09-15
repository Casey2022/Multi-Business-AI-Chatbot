# check_address.py — ask whether an address is inside a business's service area.
#
# The same code path the booking flow will use, driven from a terminal so you
# can sanity-check an address (or the API key setup) without starting a
# conversation with the bot.
#
# Usage:
#   python3 check_address.py "1 Manhattan Square Dr, Rochester NY"
#   python3 check_address.py bobs_plumbing "300 Pearl St, Buffalo NY"

import sys

from dotenv import load_dotenv
load_dotenv()

from logging_setup import setup_logging
setup_logging()

from config import load_config
from db import init_db, get_business_by_slug, get_geocode_usage
from geocode import check_service_area, api_key, daily_limit, radius_config

DEFAULT_SLUG = "bobs_plumbing"

SYMBOL = {"inside": "✓", "outside": "✗", "unverified": "?"}


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1

    slug, address = (args[0], " ".join(args[1:])) if len(args) > 1 \
        else (DEFAULT_SLUG, args[0])

    init_db()                       # makes sure the cache/usage tables exist

    business = get_business_by_slug(slug)
    if not business:
        print(f"No business with slug {slug!r}.")
        return 1

    config = load_config(business["config_path"], business["id"])
    settings = radius_config(config)

    print()
    print(f"  business : {config['business']['name']}  ({slug})")
    print(f"  API key  : {'set' if api_key() else 'MISSING'}")
    if not settings:
        print("  radius   : not configured — the check is off for this business")
    else:
        origin, miles = settings
        print(f"  radius   : {miles:g} mi from {origin}")
    print(f"  budget   : {daily_limit()}/day")
    print()

    result = check_service_area(address, config, business_id=business["id"])

    print()
    print(f"  {SYMBOL.get(result['status'], '?')} {result['status'].upper()}")
    print(f"    typed     : {result['address']}")
    print(f"    resolved  : {result['formatted'] or '—'}")
    print(f"    distance  : {result['miles'] if result['miles'] is not None else '—'} mi")
    print(f"    reason    : {result['reason']}")

    from datetime import date
    used = get_geocode_usage(str(business["id"]), date.today().isoformat())
    print(f"\n  geocode calls spent today for this business: {used}/{daily_limit()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

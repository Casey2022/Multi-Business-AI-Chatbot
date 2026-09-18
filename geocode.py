# geocode.py — turn a customer's typed address into coordinates, and decide
# whether it's inside the business's service radius.
#
# Two jobs, deliberately separated:
#
#   geocode(query, config)      "56 Test street" -> lat/lon + how sure we are
#   check_service_area(...)     those coordinates -> inside / outside / unknown
#
# The second one is the point. Validating that an address exists is table
# stakes; knowing whether Bob will drive there is the thing that stops a
# truck going 35 miles for a job he doesn't take.
#
# Failure is asymmetric on purpose. A geocoder that doesn't recognise a new
# subdivision must never turn away a paying customer, so anything short of a
# confident "outside" resolves to "unverified" and the booking continues
# with a flag for the owner. Only a confident out-of-area answer changes
# what the bot says.
#
# Uses stdlib urllib rather than requests: one GET doesn't justify a new
# direct dependency.

import json
import logging
import math
import os
import urllib.parse
import urllib.request

log = logging.getLogger("geocode")

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
TIMEOUT_SECONDS = 6
EARTH_RADIUS_MILES = 3958.8

# Google's own confidence signals. A result flagged partial_match means the
# geocoder had to guess; a rooftop or range-interpolated match means it
# found the actual address rather than the middle of a town.
CONFIDENT_TYPES = {"ROOFTOP", "RANGE_INTERPOLATED"}

# Past this distance, a match is likelier to be a misreading than a customer.
#
# Learned the hard way: "56 Test street" resolved to Council Bluffs, Iowa —
# 940 miles out, rooftop precision, no partial-match flag. Google was
# confident about that address; it just wasn't the address the customer
# meant. The viewport bias doesn't prevent this, because bounds is a
# preference between ambiguous candidates, not a restriction, and a bare
# street name with no local match has nothing to prefer.
#
# So distance itself becomes evidence. Someone 30 miles outside a 20-mile
# radius is a real customer we can't serve. Someone 900 miles out is a
# parsing accident, and telling them we don't serve their area would be
# refusing a customer over a geocoder's guess — the one outcome this whole
# design is built to avoid.
IMPLAUSIBLE_MULTIPLE    = 5
IMPLAUSIBLE_FLOOR_MILES = 100


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------

def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles between two points.

    Straight-line, not driving distance. Cheap, deterministic and testable;
    the trade is that twenty miles across a lake is a forty-minute drive.
    Good enough to catch the cases that matter, and honest about what it is.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi       = math.radians(lat2 - lat1)
    dlambda    = math.radians(lon2 - lon1)

    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def _area_of(components):
    """Pull the town and state out of a geocoding result.

    Kept on every result because they're two short strings, they survive
    caching, and the business's own pair is what lets a doubtful customer
    address be re-asked in local terms.
    """
    area = {"locality": None, "state": None}
    for component in components:
        types = component.get("types") or []
        if area["locality"] is None and (
                "locality" in types or "postal_town" in types
                or "sublocality" in types):
            area["locality"] = component.get("long_name")
        if area["state"] is None and "administrative_area_level_1" in types:
            area["state"] = component.get("short_name")
    return area


def _area_hint(result):
    """'Rochester, NY' from a geocoded origin, or None."""
    if not result:
        return None
    locality, state = result.get("locality"), result.get("state")
    if locality and state:
        return f"{locality}, {state}"
    return locality or state


def _bounds_around(lat, lon, miles):
    """A viewport box around a point, as Google's 'bounds' parameter wants it.

    This is what fixes bare addresses. A customer types "56 Test street"
    with no city; without a hint the geocoder picks whichever Test Street it
    likes best worldwide. Biasing toward the business's own neighbourhood
    makes the obvious interpretation the winning one.
    """
    lat_delta = miles / 69.0
    lon_delta = miles / (69.0 * max(math.cos(math.radians(lat)), 0.01))
    return (f"{lat - lat_delta},{lon - lon_delta}|"
            f"{lat + lat_delta},{lon + lon_delta}")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def api_key():
    return os.getenv("GOOGLE_MAPS_API_KEY")


def daily_limit():
    """Geocoding calls one business may spend per day.

    Lives here rather than in Google's console on purpose. A vendor quota
    protects Google's infrastructure: it's per-project, frequently not
    adjustable at all, and announces itself by failing inside a customer's
    booking. This one is per-business, always adjustable, identical on a
    laptop and on Render, and when it trips the booking simply continues
    unverified — which is already the designed behaviour for any address we
    can't check.
    """
    try:
        return int(os.getenv("GEOCODE_DAILY_LIMIT", "100"))
    except ValueError:
        log.warning("GEOCODE_DAILY_LIMIT is not a number — using 100")
        return 100


def _within_daily_limit(usage_key):
    """Reserve one call for this business today. False means the cap is hit.

    Fails OPEN: if the counter can't be read or written, the lookup goes
    ahead. A bookkeeping table being unavailable shouldn't silently disable
    address checking for every business — and the outer protections (the
    cache, Google's own free allowance, and a trial account that cannot be
    charged) all still apply.
    """
    if not usage_key:
        return True
    limit = daily_limit()
    if limit <= 0:
        return True                       # 0 or negative disables the cap
    try:
        from datetime import date
        from db import reserve_geocode_call
        used = reserve_geocode_call(str(usage_key), date.today().isoformat())
    except Exception as e:
        log.warning("Geocode usage counter unavailable (%s) — allowing call", e)
        return True

    if used > limit:
        # The transition below already raised a warning. Every further
        # attempt today is the cap doing its job, not news — log it, but
        # don't bury the day's real warnings under hundreds of copies.
        log.info("Geocode call refused for %s — over daily limit (%d attempts, "
                 "limit %d)", usage_key, used, limit)
        return False
    if used == limit:
        log.warning("Daily geocode limit reached for %s (%d calls) — further "
                    "addresses today will go unverified", usage_key, limit)
    return True


def radius_config(config):
    """Return (origin_address, radius_miles) or None if this business has no radius.

    Deliberately a separate key from business.service_area, which stays the
    free-text line customers are shown and the LLM reads. Sunrise's service
    area is "delivery within 5 miles for orders over $50" — a sentence no
    radius field could hold. Adding a structured key alongside it means the
    check turns on only where it makes sense, and nothing that reads the
    prose has to change.
    """
    business = config.get("business") or {}
    radius   = business.get("service_radius") or {}
    # Explicitly switched off wins over any distance still stored alongside
    # it, so turning the feature off doesn't depend on also clearing miles.
    if radius.get("enabled") is False:
        return None
    miles    = radius.get("miles")
    if not miles:
        return None
    origin = radius.get("origin") or business.get("address")
    if not origin:
        return None
    return origin, float(miles)


def is_enabled(config):
    """True when this business can actually have addresses checked."""
    return bool(api_key()) and radius_config(config) is not None


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------

def _fetch(params):
    """One GET to the Geocoding API. Returns the parsed body, or None."""
    url = f"{GEOCODE_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def geocode(query, bias=None, use_cache=True, usage_key=None):
    """Resolve an address string to coordinates. See _geocode_detailed."""
    result, _ = _geocode_detailed(query, bias=bias, use_cache=use_cache,
                                  usage_key=usage_key)
    return result


def _geocode_detailed(query, bias=None, use_cache=True, usage_key=None):
    """Resolve an address, returning (result, detail).

    detail says WHY there's no result — "not found", "lookup failed",
    "quota or key problem" — because that string ends up on the owner's
    appointment as the reason it wasn't checked. "Address not found" when
    the truth is "we ran out of API quota" sends them looking at the
    customer's typing instead of their billing.

    Returns a dict with lat, lon, formatted, confident and partial — or None
    when the address could not be resolved at all. Never raises for an
    ordinary failure: the caller's job is to keep the booking moving.

    bias is an optional 'sw|ne' bounds string from _bounds_around().
    """
    query = (query or "").strip()
    if not query:
        return None, "empty address"

    key = api_key()
    if not key:
        log.warning("GOOGLE_MAPS_API_KEY not set — skipping an address lookup")
        return None, "no API key"

    cache_key = f"{query.lower()}|{bias or ''}"
    if use_cache:
        # A cache is an optimisation. If the database is locked, or the
        # table doesn't exist yet on an older schema, the right answer is to
        # pay for the lookup — not to fail the booking.
        try:
            from db import get_cached_geocode
            cached = get_cached_geocode(cache_key)
        except Exception as e:
            log.warning("Geocode cache unavailable (%s) — looking up live", e)
            cached = None
        if cached is not None:
            log.debug("Cache hit for %r", query)
            result = cached.get("result")
            detail = cached.get("detail")
            if result is None and detail is None:
                # Rows cached before the detail was stored. A miss was only
                # ever remembered for ZERO_RESULTS, so that's what it was —
                # and without this, an old row reads as a reasonless failure,
                # which the booking flow treats as our problem rather than
                # something the customer could fix.
                detail = "address not found"
            return result, detail

    # Deliberately after the cache check — a cached answer makes no request,
    # so it shouldn't spend the day's budget.
    if not _within_daily_limit(usage_key):
        return None, "daily geocode limit reached"

    params = {"address": query, "key": key, "region": "us"}
    if bias:
        params["bounds"] = bias

    try:
        body = _fetch(params)
    except Exception as e:
        # Network trouble is not the customer's problem. Return None and let
        # the caller fall through to "unverified".
        log.warning("Geocode request failed: %s", e)
        log.debug("The address that failed: %r", query)
        return None, "lookup failed"      # not cached: a transient error

    status = body.get("status")
    if status != "OK" or not body.get("results"):
        if status == "ZERO_RESULTS":
            log.info("Geocode found nothing for the address given")
            log.debug("No match for: %r", query)
            _remember(cache_key, None, use_cache, "address not found")
            return None, "address not found"
        # OVER_QUERY_LIMIT / REQUEST_DENIED / INVALID_REQUEST are our
        # problems, not the address's — and they must not be cached, or a
        # billing hiccup would poison every address it touched.
        log.debug("Geocode for %r returned %s: %s",
                    query, status, body.get("error_message", ""))
        return None, "geocoder unavailable"

    top      = body["results"][0]
    location = top["geometry"]["location"]
    result = {
        "lat":       location["lat"],
        "lon":       location["lng"],
        "formatted": top.get("formatted_address"),
        "partial":   bool(top.get("partial_match")),
        "confident": (not top.get("partial_match")
                      and top["geometry"].get("location_type") in CONFIDENT_TYPES),
    }
    result.update(_area_of(top.get("address_components") or []))
    log.debug("Geocoded %r -> %s (confident=%s)",
             query, result["formatted"], result["confident"])
    _remember(cache_key, result, use_cache)
    return result, None


def _remember(cache_key, result, use_cache, detail=None):
    """Cache a lookup, hit or miss.

    The detail travels with it. Without that, the first miss reported
    "address not found" and every later one — served from cache — reported
    nothing at all, which is how appointment 36 ended up flagged with a null
    reason.
    """
    if not use_cache:
        return
    try:
        from db import save_cached_geocode
        save_cached_geocode(cache_key, result, detail)
    except Exception as e:                       # caching must never break a booking
        log.warning("Could not cache geocode result: %s", e)


# ---------------------------------------------------------------------------
# The question the booking flow actually asks
# ---------------------------------------------------------------------------

def check_service_area(address, config, business_id=None):
    """Decide whether an address is one this business serves.

    Returns a dict: status is "inside", "outside" or "unverified", plus
    miles, formatted and the raw address as typed. "unverified" covers every
    uncertainty — no API key, no radius configured, geocoder down, address
    not found, or a match Google itself flagged as partial. The caller
    accepts those and flags them; only "outside" should change the reply.
    """
    typed   = (address or "").strip()
    # customer_can_fix separates "you could retype this" from "our side is
    # broken". Asking someone to re-enter a perfectly good address because
    # our API key expired is rude and useless.
    outcome = {"status": "unverified", "miles": None, "formatted": None,
               "address": typed, "reason": None, "locality": None,
               "state": None, "customer_can_fix": False}

    settings = radius_config(config)
    if not settings:
        outcome["reason"] = "no service radius configured"
        return outcome
    if not api_key():
        outcome["reason"] = "no API key"
        return outcome

    origin_address, radius_miles = settings

    usage_key = business_id or (config.get("business") or {}).get("slug")

    origin, origin_detail = _geocode_detailed(origin_address, usage_key=usage_key)
    if not origin:
        if origin_detail == "daily geocode limit reached":
            # Not a config problem. Don't send the owner to check their own
            # address when the real answer is "we've spent today's budget".
            outcome["reason"] = origin_detail
            return outcome
        # A business's own address failing to resolve IS a config problem,
        # and it silently disables the check for every customer until fixed.
        log.warning("Business origin %r could not be geocoded (%s) — "
                    "service area check disabled", origin_address, origin_detail)
        outcome["reason"] = f"business address not geocodable ({origin_detail})"
        return outcome

    bias  = _bounds_around(origin["lat"], origin["lon"], radius_miles * 2)
    match, detail = _geocode_detailed(typed, bias=bias, usage_key=usage_key)
    if not match:
        outcome["reason"] = detail
        outcome["customer_can_fix"] = (detail == "address not found")
        return outcome

    miles = haversine_miles(origin["lat"], origin["lon"],
                            match["lat"], match["lon"])
    outcome["miles"]     = round(miles, 1)
    outcome["formatted"] = match["formatted"]
    outcome["locality"]  = match.get("locality")
    outcome["state"]     = match.get("state")

    if not match["confident"]:
        # We found something, but not precisely: either Google flagged the
        # match as partial, or it resolved to a town centre rather than a
        # building. Record the distance for the owner; don't act on it.
        outcome["reason"] = ("partial match — Google wasn't sure this is the "
                             "right address" if match["partial"] else
                             "approximate match — resolved to an area, not a "
                             "street address")
        outcome["customer_can_fix"] = True
        return outcome

    implausible = max(radius_miles * IMPLAUSIBLE_MULTIPLE, IMPLAUSIBLE_FLOOR_MILES)
    if miles > implausible:
        log.warning("An address resolved %.0f mi away, past the %.0f mi "
                    "plausibility limit — asking again in local terms.",
                    miles, implausible)
        log.debug("Implausible: %r -> %r", typed, match["formatted"])

        # Ask again in local terms. A customer typing "56 Test street" means
        # the one in their town; Google answered globally because nothing
        # local matched. Appending the business's own town and state turns an
        # open question into a local one, and "no such address here" is a far
        # more useful answer than a confident address 900 miles away.
        #
        # Costs one extra call, and only in this rare case — the ordinary
        # path never reaches here.
        area = _area_hint(origin)
        retried = None
        if area and area.split(",")[0].lower() not in typed.lower():
            log.info("Retrying the lookup with the business's own town appended")
            log.debug("Re-asking as %r", f"{typed}, {area}")
            retried, retried_detail = _geocode_detailed(
                f"{typed}, {area}", bias=bias, usage_key=usage_key)

        if retried:
            retried_miles = haversine_miles(origin["lat"], origin["lon"],
                                            retried["lat"], retried["lon"])
            if retried_miles <= implausible and retried["confident"]:
                log.debug("Local re-ask resolved %r -> %s (%.1f mi)",
                         typed, retried["formatted"], retried_miles)
                match = retried
                miles = retried_miles
                outcome["miles"]     = round(miles, 1)
                outcome["formatted"] = match["formatted"]
            else:
                retried = None

        if not retried:
            outcome["customer_can_fix"] = True
            if area:
                outcome["reason"] = (f"no address like this near {area} — the "
                                     f"closest match was {match['formatted']}, "
                                     f"{outcome['miles']} mi away")
            else:
                outcome["reason"] = (f"resolved {outcome['miles']} mi away "
                                     f"({match['formatted']}) — too far to be "
                                     f"right, so probably the wrong address")
            return outcome

    outcome["status"] = "inside" if miles <= radius_miles else "outside"
    outcome["reason"] = f"{outcome['miles']} mi from {origin_address}"
    log.info("Service area check: %s (%.1f mi, radius %.0f)",
             outcome["status"], miles, radius_miles)
    log.debug("Checked address: %r", typed)
    return outcome

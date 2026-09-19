#!/usr/bin/env python3
"""scheduling_test.py — the availability rules, checked as arithmetic.

Run it:  python3 scheduling_test.py

scheduling.py is pure: config in, answer out, no network and no database.
That makes it the one part of booking that can be tested exhaustively and
instantly, so it's worth doing properly. The cases here are the ones where a
wrong answer is expensive rather than merely wrong — a salon double-booking
a stylist, a long job accepted into a short gap.

Times are built relative to the next Monday so the suite never goes red on a
Saturday or drifts past a date someone hard-coded.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

import scheduling

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n" + "-" * len(text))


def next_monday():
    """09:00 on the next Monday that's at least a day away."""
    d = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    d += timedelta(days=1)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d


MON = next_monday()


def at(hour, minute=0, day_offset=0):
    return (MON + timedelta(days=day_offset)).replace(
        hour=hour, minute=minute).strftime("%Y-%m-%d %H:%M")


def dt(hour, minute=0, day_offset=0):
    return (MON + timedelta(days=day_offset)).replace(hour=hour, minute=minute)


SALON = {
    "services": ["haircut", "colour", "cut and colour"],
    "calendar": {
        "default_duration_minutes": 45,
        "service_durations": {"colour": 120, "cut and colour": 150},
        "scheduling": {
            "model": "capacity",
            "slots_per_time": 2,          # two chairs
            "buffer_minutes": 0,
            "slot_granularity": 30,
            "max_alternatives": 3,
            "business_hours": {i: ["09:00", "17:00"] for i in range(5)},
        },
    },
}

PLUMBER = {
    "services": ["drain cleaning", "water heater installation"],
    "calendar": {
        "default_duration_minutes": 60,
        "service_durations": {"water heater installation": 180},
        "scheduling": {
            "model": "exclusive",
            "buffer_minutes": 45,
            "slot_granularity": 30,
            "max_alternatives": 3,
            "business_hours": {i: ["09:00", "17:00"] for i in range(5)},
        },
    },
}


def main():
    heading("How long is a service")
    check("a service with no entry gets the default",
          scheduling.duration_for(SALON, "haircut") == 45)
    check("a service with an entry gets its own length",
          scheduling.duration_for(SALON, "colour") == 120)
    check("case and spacing don't matter",
          scheduling.duration_for(SALON, "  COLOUR ") == 120)
    check("no service named means the default",
          scheduling.duration_for(SALON) == 45)
    check("an unknown service falls back rather than raising",
          scheduling.duration_for(SALON, "beard trim") == 45)
    # The reason matching is exact: "cut and colour" contains "colour", and
    # containment would hand a 45-minute haircut the 150-minute booking.
    check("a longer service name is not matched by a shorter one",
          scheduling.duration_for(SALON, "cut and colour") == 150)
    check("a non-numeric duration falls back instead of crashing",
          scheduling.duration_for(
              {"calendar": {"default_duration_minutes": 45,
                            "service_durations": {"colour": "two hours"}}},
              "colour") == 45)

    heading("A long booking holds the time it actually takes")
    # One chair taken by a 2-hour colour from 10:00. With two chairs, an
    # 11:00 haircut still fits; a second colour at 11:00 would need the
    # chair that's still busy — the capacity count is what decides.
    colour_at_ten = [(dt(10), dt(12))]
    two_colours = [(dt(10), dt(12)), (dt(10, 30), dt(12, 30))]
    check("an 11am cut fits beside one 2-hour colour",
          scheduling.is_slot_available(SALON, at(11), colour_at_ten, "haircut"))
    check("an 11am cut does not fit when both chairs are mid-colour",
          not scheduling.is_slot_available(SALON, at(11), two_colours, "haircut"))
    check("the second colour of the morning still sees the first",
          not scheduling.is_slot_available(SALON, at(11), two_colours, "colour"))

    heading("A long booking needs a long gap")
    # Exclusive model, 45-minute buffers, a gap from 10:00 to 13:00 free.
    busy = [(dt(9), dt(10)), (dt(13), dt(14))]
    check("a 1-hour job fits in the gap",
          scheduling.is_slot_available(PLUMBER, at(11), busy, "drain cleaning"))
    check("a 3-hour job does not fit the same gap",
          not scheduling.is_slot_available(PLUMBER, at(11), busy,
                                           "water heater installation"))
    check("asking without the service would have wrongly said yes",
          scheduling.is_slot_available(PLUMBER, at(11), busy),
          "the default length fits, which is exactly the bug")

    heading("Closing time")
    check("a 3-hour job at 3pm runs past close and is refused",
          not scheduling.is_slot_available(PLUMBER, at(15), [],
                                           "water heater installation"))
    check("a 1-hour job at 3pm is fine",
          scheduling.is_slot_available(PLUMBER, at(15), [], "drain cleaning"))
    check("and the reason given is 'closed', not 'conflict'",
          scheduling.slot_rejection_reason(
              PLUMBER, at(15), [], "water heater installation") == "closed")

    heading("Alternatives are offered at the right length")
    alts = scheduling.find_alternatives(PLUMBER, at(15), busy,
                                        "water heater installation")
    check("every alternative offered actually fits the job",
          all(scheduling.is_slot_available(PLUMBER, iso, busy,
                                           "water heater installation")
              for iso in alts),
          alts)
    check("some alternatives were found at all", len(alts) > 0, alts)

    heading("Catching a duration that names nothing")
    check("a key matching a service is not reported",
          scheduling.unknown_duration_services(SALON) == [],
          scheduling.unknown_duration_services(SALON))
    renamed = {"services": ["haircut", "color"],     # owner respelled it
               "calendar": {"service_durations": {"colour": 120}}}
    check("a key left behind by a rename is reported",
          scheduling.unknown_duration_services(renamed) == ["colour"],
          scheduling.unknown_duration_services(renamed))

    heading("Every shipped config, checked against itself")
    # The fixtures above prove the rules work. This proves the configs we
    # actually ship obey them — a duration key that no longer names a
    # service is invisible in production and free to catch here.
    import yaml
    for path in sorted(Path("config").glob("*.yaml")):
        if path.name == "personas.yaml":
            continue
        config = yaml.safe_load(path.read_text())
        stale = scheduling.unknown_duration_services(config)
        check(f"{path.name}: every service_duration names a real service",
              stale == [], f"unmatched: {stale}")
        for service in (config.get("services") or []):
            minutes = scheduling.duration_for(config, service)
            check(f"{path.name}: {service!r} has a sane length",
                  0 < minutes <= 480, f"{minutes} minutes")

    heading("A question asks about the same service a duration does")
    # The point of same_service existing at all: if a question and a
    # duration disagreed about what "cut and colour" matches, a booking
    # could be asked the colour questions and scheduled for a trim.
    check("case and spacing don't matter",
          scheduling.same_service("Cut  And  COLOUR", "cut and colour"))
    check("a substring is not a match",
          not scheduling.same_service("colour", "cut and colour"),
          "containment here would let 'cut' claim what 'cut and colour' owns")
    check("blank matches nothing real",
          not scheduling.same_service("", "colour"))

    heading("Every configured condition names a service that exists")
    for path in sorted(Path("config").glob("*.yaml")):
        if path.name == "personas.yaml":
            continue
        config = yaml.safe_load(path.read_text())
        orphaned = scheduling.unknown_condition_services(config)
        # A condition naming a service the business doesn't offer is never
        # satisfied, so the question is never asked -- for anyone, forever,
        # with nothing anywhere saying why.
        check(f"{path.name}: no question is set for a service it doesn't offer",
              orphaned == [],
              "; ".join(f"{k} -> {v!r}" for k, v in orphaned))

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

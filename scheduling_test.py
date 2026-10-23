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
        if path.name in ("personas.yaml", "defaults.yaml"):
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

    heading("The business's clock, not the server's")
    # Render runs on UTC. A UTC server's datetime.now() at 2pm Eastern says
    # 6pm, so every same-day slot in the next four hours was refused as
    # "past". clock.utc_now is replaced to fix the moment; the business's
    # zone comes from its config.
    import clock
    from datetime import date, timezone
    belmont = yaml.safe_load(Path("config/belmont_hair_studio.yaml").read_text())
    real_utc_now = clock.utc_now
    try:
        # Tuesday 22 Sep 2026, 2pm Eastern (EDT) = 6pm UTC.
        clock.utc_now = lambda: datetime(2026, 9, 22, 18, 0, tzinfo=timezone.utc)
        check("at 6pm UTC it is 2pm for an Eastern business",
              clock.business_now(belmont) == datetime(2026, 9, 22, 14, 0),
              str(clock.business_now(belmont)))
        check("a 4pm slot at 2pm Eastern is not refused as 'past'",
              scheduling.slot_rejection_reason(belmont, "2026-09-22 16:00", []) is None,
              str(scheduling.slot_rejection_reason(belmont, "2026-09-22 16:00", [])))
        check("... and is bookable",
              scheduling.is_slot_available(belmont, "2026-09-22 16:00", []))
        check("a 1pm slot at 2pm Eastern IS past",
              scheduling.slot_rejection_reason(belmont, "2026-09-22 13:00", []) == "past")
        alts = scheduling.find_alternatives(belmont, "2026-09-22 13:00", [])
        check("no alternative offered before 2pm Eastern",
              alts and all(a >= "2026-09-22 14:00" for a in alts), str(alts))
        check("the 2:30pm slot is offered, which a UTC clock would have hidden",
              "2026-09-22 14:30" in alts, str(alts))

        # 9:30pm Eastern Tuesday = 1:30am UTC Wednesday.
        clock.utc_now = lambda: datetime(2026, 9, 23, 1, 30, tzinfo=timezone.utc)
        check("at 1:30am UTC Wednesday it is still Tuesday in Rochester",
              clock.business_today(belmont) == date(2026, 9, 22),
              str(clock.business_today(belmont)))
        check("Wednesday 9am is bookable the evening before",
              scheduling.is_slot_available(belmont, "2026-09-23 09:00", []))

        # Winter: EST is UTC-5, and the zone database, not a fixed offset,
        # decides that.
        clock.utc_now = lambda: datetime(2026, 12, 15, 19, 0, tzinfo=timezone.utc)
        check("in December 7pm UTC is 2pm Eastern (EST, UTC-5)",
              clock.business_now(belmont) == datetime(2026, 12, 15, 14, 0),
              str(clock.business_now(belmont)))

        check("a config with no timezone falls back to America/New_York",
              clock.timezone_for({}) == "America/New_York")
    finally:
        clock.utc_now = real_utc_now

    heading("Every configured condition names a service that exists")
    for path in sorted(Path("config").glob("*.yaml")):
        if path.name in ("personas.yaml", "defaults.yaml"):
            continue
        config = yaml.safe_load(path.read_text())
        orphaned = scheduling.unknown_condition_services(config)
        # A condition naming a service the business doesn't offer is never
        # satisfied, so the question is never asked -- for anyone, forever,
        # with nothing anywhere saying why.
        check(f"{path.name}: no question is set for a service it doesn't offer",
              orphaned == [],
              "; ".join(f"{k} -> {v!r}" for k, v in orphaned))
        check(f"{path.name}: no notice is set for a service it doesn't offer",
              scheduling.unknown_notice_services(config) == [],
              scheduling.unknown_notice_services(config))

    notice_checks()

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


def notice_checks():
    """A booking inside its service's notice is refused, and the times
    offered instead all meet it (notes/booking_notice.md). Before, the
    booking flow never looked: a custom cake for tomorrow (72 hours needed)
    and a Friday 8pm tray at 5pm (order by 4pm) were both taken."""
    import clock
    import scheduler
    from datetime import timezone
    from zoneinfo import ZoneInfo
    from config import load_config
    heading("notice: a booking inside it is refused, and the alternatives meet it")
    sunrise = load_config("config/sunrise_bakery_and_cafe.yaml")
    crosstown = load_config("config/crosstown_pizza.yaml")
    real = clock.utc_now

    def pin(*local):
        moment = datetime(*local, tzinfo=ZoneInfo("America/New_York"))
        clock.utc_now = lambda: moment.astimezone(timezone.utc)
    try:
        pin(2026, 9, 22, 10, 0)                                  # Tuesday 10am
        cake = "custom birthday cakes"
        check("a custom cake tomorrow: 'notice'",
              scheduling.slot_rejection_reason(sunrise, "2026-09-23 14:00", [], cake)
              == "notice")
        check("72 hours on: fine",
              scheduling.slot_rejection_reason(sunrise, "2026-09-25 10:00", [], cake) is None)
        alts = scheduling.find_alternatives(sunrise, "2026-09-23 14:00", [], cake)
        check("every alternative offered meets the 72 hours",
              alts and all(a >= "2026-09-25 10:00" for a in alts), alts)
        alts = scheduling.find_alternatives(sunrise, "2026-09-23 14:00", [], "wedding cakes")
        check("two weeks' notice: alternatives start two weeks out, not none",
              alts and alts[0] == "2026-10-06 10:00", alts)
        check("a service with no notice entry is unaffected (the shop's hours only)",
              scheduling.slot_rejection_reason(sunrise, "2026-09-23 14:00", []) is None)
        lead = scheduler._unavailable_message("2026-09-23 14:00", alts, sunrise,
                                              reason="notice", service=cake)
        check("the refusal says the notice and that the time is too soon",
              lead.startswith("We need 3 days (72 hours) of notice for custom birthday "
                              "cakes, so Wednesday, September 23 at 2:00 PM is too soon."),
              lead)

        heading("notice variants: the order's own words can need longer")
        # journey_eval, 2026-10-22: gluten-free muffins for Thursday on a
        # Tuesday (96 hours needed, 48 given) were taken, 3 runs in 3.
        muffins, gf = "muffins by the dozen", "a dozen gluten-free muffins"
        check("plain muffins Thursday 10am: fine (24 hours)",
              scheduling.slot_rejection_reason(sunrise, "2026-09-24 10:00", [], muffins) is None)
        check("gluten-free muffins Thursday 10am: 'notice' (96 hours)",
              scheduling.slot_rejection_reason(sunrise, "2026-09-24 10:00", [], muffins, gf)
              == "notice")
        alts = scheduling.find_alternatives(sunrise, "2026-09-24 10:00", [], muffins, gf)
        check("every alternative meets the 96 hours (Saturday 10am on)",
              alts and all(a >= "2026-09-26 10:00" for a in alts), alts)
        check("is_slot_available agrees",
              not scheduling.is_slot_available(sunrise, "2026-09-24 10:00", [], muffins, gf))
        lead = scheduler._unavailable_message("2026-09-24 10:00", alts, sunrise,
                                              reason="notice", service=muffins, details=gf)
        check("the refusal names the variant and its notice",
              lead.startswith("We need 4 days (96 hours) of notice for gluten-free muffins, "
                              "so Thursday, September 24 at 10:00 AM is too soon."), lead)
        check("a themed cupcake display Thursday 11am: 'notice' (72 hours, not 24)",
              scheduling.slot_rejection_reason(sunrise, "2026-09-24 11:00", [],
                                               "cupcake orders (dozen minimum)",
                                               "a themed display, a number 5") == "notice")
        pending = {"service": muffins, "datetime": "Thursday at 10am",
                   "datetime_parsed": "2026-09-24 10:00", "customer_name": "Riley",
                   "notes": "they need to be gluten free", "_asked": "a dozen muffins",
                   "_offered": ["gluten free decoy"]}
        details = scheduler._order_details(pending)
        check("the order's words: what was asked, and every answer",
              "a dozen muffins" in details and "they need to be gluten free" in details
              and muffins in details, details)
        check("…but not the times we offered or the parsed time",
              "decoy" not in details and "2026-09-24" not in details, details)
        check("the booking flow keeps the customer's opening words",
              'pending["_asked"] = message.strip()[:300]'
              in (Path(__file__).resolve().parent / "scheduler.py").read_text())

        pin(2026, 9, 25, 17, 0)                                  # Friday 5pm
        tray = "party tray"
        check("a Friday 8pm tray at 5pm: 'cutoff' (order by 4pm)",
              scheduling.slot_rejection_reason(crosstown, "2026-09-25 20:00", [], tray)
              == "cutoff")
        alts = scheduling.find_alternatives(crosstown, "2026-09-25 20:00", [], tray)
        check("no Friday-night tray is offered instead",
              alts and not any(a.startswith("2026-09-25") for a in alts), alts)
        check("a pizza at the same time: fine",
              scheduling.slot_rejection_reason(crosstown, "2026-09-25 20:00", [], "pizza")
              is None)
        lead = scheduler._unavailable_message("2026-09-25 20:00", alts, crosstown,
                                              reason="cutoff", service=tray)
        check("the refusal says the cutoff",
              lead.startswith("On Fridays, we need party tray orders by 4pm"), lead)
        pin(2026, 9, 25, 15, 0)                                  # Friday 3pm
        check("before the cutoff, with three hours to spare: fine",
              scheduling.slot_rejection_reason(crosstown, "2026-09-25 20:00", [], tray) is None)
    finally:
        clock.utc_now = real


if __name__ == "__main__":
    sys.exit(main())

"""config_check.py — what's wrong with a business's config, in words.

A business's YAML is read on every request. Before this (measured
2026-10-23, 21 plausible mistakes on copies of the real files), half of
them crashed somewhere a customer would hit it (a typo'd time zone, a menu
price written "$17", a question with no prompt) and the rest did something
wrong without a word: hours written 17:00-09:00 were told to customers as
"Mon, 5pm-9am"; services written on one line became the services "d", "r",
"a"...; a 150% cancellation fee was quoted. The only signal was a log line
at startup, for four of them.

problems(path) returns each one as {"level", "where", "says"}:
  "broken"  — the business can't work right (a crash, or customers told
              something false); fix before deploying.
  "warning" — something is ignored or falls back (an unknown section, a
              persona name nobody has).
Shown on the operator's Businesses page, logged at startup, and
config_check_test.py holds every shipped YAML to none at all.
"""

import difflib
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent

# Every top-level section the code reads. Anything else is ignored, so a
# misspelled section ("buisness") is said, with the likely one.
SECTIONS = {"business", "services", "menu_enabled", "menu", "calendar", "bot",
            "rules", "booking", "policies"}
DAYS = "Monday Tuesday Wednesday Thursday Friday Saturday Sunday".split()
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def problems(path, business_id=None):
    """Everything wrong with this config file (and, with business_id, the
    owner's changes on top of it). [] when it's fine."""
    out = []

    def say(level, where, says):
        out.append({"level": level, "where": where, "says": says})

    try:
        raw = yaml.safe_load(Path(path).read_text())
    except FileNotFoundError:
        say("broken", "file", f"There's no config file at {path}.")
        return out
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line = f" (line {mark.line + 1})" if mark else ""
        what = getattr(e, "problem", None) or "it isn't valid YAML"
        say("broken", "file", f"The file can't be read{line}: {what}.")
        return out
    if not isinstance(raw, dict):
        say("broken", "file", "The file should be sections (business:, services:, ...), "
                              "not a single value or a list.")
        return out

    _check_file(raw, say)
    if any(p["level"] == "broken" for p in out):
        return out            # loading would only fail on the same thing

    # Loaded the way the app does: anything the checks above didn't foresee
    # still shows here, in a sentence, instead of on a customer's screen.
    from config import load_config
    try:
        config = load_config(str(path), business_id)
    except Exception as e:
        say("broken", "file", f"Loading it failed: {type(e).__name__}: {e}")
        return out
    _check_links(config, say)
    return out


def summary(found):
    """"2 broken, 1 warning", for a log line or a chip."""
    counts = [(sum(p["level"] == level for p in found), level)
              for level in ("broken", "warning")]
    return ", ".join(f"{n} {level}{'' if level == 'broken' or n == 1 else 's'}"
                     for n, level in counts if n)


# ---------------------------------------------------------------------------
# The file itself
# ---------------------------------------------------------------------------

def _is_number(value, at_least=0, whole=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value >= at_least and (not whole or float(value).is_integer())


def _text(value):
    return isinstance(value, str) and value.strip() != ""


def _check_file(raw, say):
    for key in raw:
        if key == "faq":
            say("warning", "faq", "The faq: section isn't read any more: move those "
                                  "answers into the knowledge base.")
        elif key not in SECTIONS:
            near = difflib.get_close_matches(str(key), SECTIONS, n=1)
            say("warning", str(key), f"There's no section called '{key}'; it's ignored."
                + (f" Did you mean '{near[0]}'?" if near else ""))

    business = raw.get("business")
    if not isinstance(business, dict):
        say("broken", "business", "The business: section is missing (name, phone, ...).")
    else:
        for key in ("name", "phone"):
            if not _text(business.get(key)):
                say("broken", f"business.{key}", f"The business has no {key}.")

    services = raw.get("services")
    if services is not None and not (isinstance(services, list) and all(
            _text(s) or (isinstance(s, dict) and _text(s.get("name"))) for s in services)):
        say("broken", "services", "Services should be a list, one per line starting "
                                  "with '- ' (one line of names becomes one letter each).")

    _check_calendar(raw.get("calendar"), say)
    _check_bot(raw.get("bot"), say)
    _check_rules(raw.get("rules"), say)
    _check_booking(raw.get("booking"), say)
    _check_cancellation(((raw.get("policies") or {}).get("cancellation")
                         if isinstance(raw.get("policies"), dict) else None), say)
    _check_menu(raw.get("menu"), say)


def _check_calendar(calendar, say):
    if calendar is None:
        return
    if not isinstance(calendar, dict):
        say("broken", "calendar", "calendar: should be a section of settings.")
        return
    zone = calendar.get("timezone")
    if zone is not None:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(str(zone))
        except Exception:
            say("broken", "calendar.timezone",
                f"'{zone}' isn't a time zone (like America/New_York).")
    if "default_duration_minutes" in calendar and not _is_number(
            calendar["default_duration_minutes"], 1, whole=True):
        say("broken", "calendar.default_duration_minutes",
            "The default length should be a number of minutes, like 60.")
    for name, minutes in (calendar.get("service_durations") or {}).items():
        if not _is_number(minutes, 1, whole=True):
            say("broken", f"calendar.service_durations.{name}",
                f"The length of '{name}' should be a number of minutes.")

    sched = calendar.get("scheduling") or {}
    if not isinstance(sched, dict):
        say("broken", "calendar.scheduling", "scheduling: should be a section of settings.")
        return
    for key, least in (("buffer_minutes", 0), ("slot_granularity", 1),
                       ("max_alternatives", 1), ("slots_per_time", 1)):
        if key in sched and not _is_number(sched[key], least, whole=True):
            say("broken", f"calendar.scheduling.{key}", f"{key} should be a whole number.")

    hours = sched.get("business_hours")
    where = "calendar.scheduling.business_hours"
    if hours is not None and not isinstance(hours, dict):
        say("broken", where, "Opening hours should be one line per day: 0 (Monday) to "
                             "6 (Sunday), each [\"09:00\", \"17:00\"].")
    for day, span in (hours or {}).items() if isinstance(hours, dict) else ():
        number = int(day) if str(day).isdigit() else None
        if number is None or not 0 <= number <= 6:
            say("broken", f"{where}.{day}", f"'{day}' isn't a day: days are 0 (Monday) "
                                            "to 6 (Sunday).")
            continue
        name = DAYS[number]
        if not (isinstance(span, list) and len(span) == 2
                and all(isinstance(t, str) and _HHMM.match(t) for t in span)):
            say("broken", f"{where}.{day}", f"{name}'s hours should be an opening and a "
                f"closing time, 24-hour: [\"09:00\", \"17:00\"] (it says {span!r}).")
        elif span[0] >= span[1]:
            say("broken", f"{where}.{day}", f"{name} closes ({span[1]}) before it opens "
                f"({span[0]}); customers would be told {span[0]}-{span[1]}.")

    for i, window in enumerate(sched.get("blackout") or []):
        times = [window.get(k) for k in ("start", "end")] if isinstance(window, dict) else []
        if not (len(times) == 2 and all(isinstance(t, str) and _HHMM.match(t) for t in times)
                and times[0] < times[1]):
            say("broken", f"calendar.scheduling.blackout.{i}",
                "A break needs a start and end, 24-hour, start first: "
                "start: \"12:00\", end: \"13:00\".")


def _check_bot(bot, say):
    if not isinstance(bot, dict):
        return
    preset = bot.get("persona_preset")
    if preset is not None:
        personas = yaml.safe_load((ROOT / "config" / "personas.yaml").read_text()) or {}
        if preset not in personas:
            say("warning", "bot.persona_preset",
                f"There's no persona '{preset}', so the assistant sounds 'warm'. "
                f"The choices: {', '.join(sorted(personas))}.")
    universal = (bot.get("guardrails") or {}).get("universal") \
        if isinstance(bot.get("guardrails"), dict) else None
    if isinstance(universal, list):
        defaults = yaml.safe_load((ROOT / "config" / "defaults.yaml").read_text()) or {}
        known = ((defaults.get("bot") or {}).get("guardrail_rules") or {})
        for item in universal:
            if isinstance(item, dict) and item.get("rule") not in known:
                near = difflib.get_close_matches(str(item.get("rule")), known, n=1)
                say("broken", "bot.guardrails.universal",
                    f"There's no shared rule '{item.get('rule')}' in config/defaults.yaml."
                    + (f" Did you mean '{near[0]}'?" if near else ""))


def _check_rules(rules, say):
    if rules is None:
        return
    if not isinstance(rules, list):
        say("broken", "rules", "rules: should be a list of shortcuts, each starting '- name:'.")
        return
    for i, rule in enumerate(rules):
        name = rule.get("name", i) if isinstance(rule, dict) else i
        where = f"rules.{name}"
        if not isinstance(rule, dict):
            say("broken", where, "Each shortcut needs a name, keywords and a reply.")
            continue
        if not _text(rule.get("reply")):
            say("broken", where, f"The '{name}' shortcut has no reply.")
        keywords = rule.get("keywords")
        if not (isinstance(keywords, list) and keywords and all(_text(k) for k in keywords)):
            say("broken", where, f"The '{name}' shortcut's keywords should be a list: "
                                 "[\"hi\", \"hello\"].")


def _check_booking(booking, say):
    if not isinstance(booking, dict):
        return
    for i, question in enumerate(booking.get("extra_questions") or []):
        if not (isinstance(question, dict) and _text(question.get("key"))
                and _text(question.get("prompt"))):
            key = question.get("key", i) if isinstance(question, dict) else i
            say("broken", f"booking.extra_questions.{key}",
                "Each booking question needs a key and a prompt (what the customer is asked).")


def _check_cancellation(rules, say):
    if not isinstance(rules, dict):
        return
    for i, window in enumerate(rules.get("windows") or []):
        where = f"policies.cancellation.windows.{i}"
        if not isinstance(window, dict):
            say("broken", where, "Each cancellation window needs at_least_hours and a charge.")
            continue
        if not _is_number(window.get("at_least_hours"), 0):
            say("broken", where, "at_least_hours should be a number of hours, like 24 "
                f"(it says {window.get('at_least_hours')!r}).")
        for kind, outcome in window.items():
            if isinstance(outcome, dict) and "charge_percent" in outcome:
                percent = outcome["charge_percent"]
                if not _is_number(percent, 0) or percent > 100:
                    say("broken", where, f"A charge of {percent!r}% isn't possible: "
                                         "0 to 100.")


def _check_menu(menu, say):
    if menu is None:
        return
    if not isinstance(menu, list):
        say("broken", "menu", "menu: should be a list of items, each starting '- item:'.")
        return
    from menu import parse_price
    for i, entry in enumerate(menu):
        name = entry.get("item", i) if isinstance(entry, dict) else i
        where = f"menu.{name}"
        if not isinstance(entry, dict) or not _text(entry.get("item")):
            say("broken", where, "Each menu entry needs an item name.")
            continue
        options = entry.get("options")
        if not (isinstance(options, dict) and options) and entry.get("each") is None:
            say("broken", where, f"'{name}' has no options (sizes and prices) or price each.")
        prices = [(f"{name}, {k}", v) for k, v in (options or {}).items()
                  if isinstance(options, dict)]
        for add_on, spec in (entry.get("add_ons") or {}).items():
            table = spec.get("prices") if isinstance(spec, dict) and "prices" in spec \
                else {"any": spec}
            prices += [(f"{name}, {add_on} ({k})", v) for k, v in (table or {}).items()]
        for label, value in prices:
            try:
                parse_price(value)
            except (TypeError, ValueError):
                say("broken", where, f"The price for {label} should be a number, like 17 "
                                     f"(it says {value!r}).")


# ---------------------------------------------------------------------------
# The loaded config: links between its parts
# ---------------------------------------------------------------------------

def _check_links(config, say):
    import scheduling
    for name in scheduling.unknown_notice_services(config):
        say("warning", "booking.notice", f"Booking notice is set for '{name}', which isn't "
                                         "a service, so nothing uses it.")
    for name in scheduling.unknown_duration_services(config):
        say("warning", "calendar.service_durations", f"A length is set for '{name}', which "
            "isn't a service; bookings take the default length.")
    for key, name in scheduling.unknown_condition_services(config):
        say("warning", f"booking.extra_questions.{key}", f"The question '{key}' is for "
            f"'{name}', which isn't a service, so it's never asked.")

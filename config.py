# config.py — YAML configuration loader.
#
# No global CONFIG — configs load per-request so one server can serve
# many businesses from different YAML files simultaneously.
# app.py calls load_config() at the start of every request and passes
# the result through to every function that needs it.

import re
import yaml
from pathlib import Path

import logging
log = logging.getLogger("config")


# Fields an owner may edit. Anything not listed is operator-only —
# guardrails, keyword rules, booking templates, and calendar config all
# stay out of reach because breaking them has consequences an owner has
# no way to anticipate.
EDITABLE_FIELDS = {
    "business.name":        {"label": "Business name",   "type": "text"},
    "business.phone":       {"label": "Phone",           "type": "text"},
    "business.address":     {"label": "Address",         "type": "text"},
    "business.hours":       {"label": "Hours (as shown to customers)",
                             "type": "text"},
    "business.service_area":{"label": "Service area",    "type": "text"},
    "services":             {"label": "Services offered","type": "list"},
    "faq":                  {"label": "Frequently asked questions",
                             "type": "faq"},
    "booking.noun":         {"label": "What you call a booking",
                             "type": "text"},
    "bot.persona_preset":   {"label": "Bot personality", "type": "choice"},
    "booking.extra_questions": {"label": "Extra booking questions",
                                "type": "questions"},
}


# Guardrails for owner-authored booking questions.
#
# The booking flow fills "service" and "datetime" itself, and stashes the
# parsed timestamp under "datetime_parsed". A question whose key collided
# with one of those would quietly overwrite a real slot, so those names are
# off limits. The cap keeps a booking conversation from turning into a
# ten-question interrogation.
MAX_EXTRA_QUESTIONS = 5
RESERVED_SLOT_KEYS  = {"service", "datetime", "datetime_parsed"}


def slugify(label):
    """Turn an owner-facing label into a storage key.

    'Service address' -> 'service_address'. The key is what gets stored in
    the appointment's details JSON and what the admin humanizes back into a
    row label, so it has to be lowercase, underscore-separated, and free of
    anything that would look wrong in JSON.
    """
    return re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")


def humanize(key):
    """Fallback display label for a question key: 'service_address' -> 'Service address'."""
    return key.replace("_", " ").strip().capitalize()


def question_label(question):
    """The owner-facing label for one extra question.

    A label is only stored when it differs from what the key humanizes to,
    so configs written by hand in YAML (key + prompt, no label) still get a
    sensible label without anyone having to type one.
    """
    return (question.get("label") or "").strip() or humanize(question["key"])


def question_labels(config):
    """Map each configured question key to its owner-facing label.

    One place decides how a stored answer is named to a human. The
    customer's confirmation message, the calendar event description, and
    the admin all read from here, so a label corrected in settings shows up
    everywhere that answer appears rather than in one of the three.
    """
    questions = (config.get("booking") or {}).get("extra_questions") or []
    return {q["key"]: question_label(q) for q in questions}


def extra_question_rows(config):
    """Build the settings editor's rows: every saved question, padded to the cap.

    The form always posts MAX_EXTRA_QUESTIONS rows, so the cap is visible on
    the page instead of being a rule the owner discovers by breaking it.
    """
    questions = (config.get("booking") or {}).get("extra_questions") or []
    rows = []
    for index in range(MAX_EXTRA_QUESTIONS):
        question = questions[index] if index < len(questions) else None
        rows.append({
            "number": index + 1,
            "index":  index,
            "key":    question["key"] if question else "",
            "label":  question_label(question) if question else "",
            "prompt": (question.get("prompt") or "") if question else "",
        })
    return rows


def parse_extra_questions(form, field="booking.extra_questions"):
    """Read the settings editor's rows back into a booking.extra_questions list.

    Each row posts three inputs — 'field[i].label', 'field[i].prompt', and a
    hidden 'field[i].key'. The hidden key is what makes a rename safe: it is
    minted once, from the label, the first time a row is filled in, and then
    carried on every save afterwards. Editing the label of an existing row
    therefore relabels its past answers instead of orphaning them under a
    key nobody asks for anymore.

    Returns (questions, errors); when errors is non-empty the caller should
    reject the whole field rather than save a partly-understood list.
    """
    # If the form carries no question rows at all, it isn't the questions
    # editor — say so with None rather than reading "no rows" as "delete
    # every question this business has".
    if not any(f"{field}[{index}].prompt" in form
               for index in range(MAX_EXTRA_QUESTIONS)):
        return None, []

    questions = []
    errors    = []
    seen      = set()

    for index in range(MAX_EXTRA_QUESTIONS):
        row    = index + 1
        label  = (form.get(f"{field}[{index}].label")  or "").strip()
        prompt = (form.get(f"{field}[{index}].prompt") or "").strip()
        posted = (form.get(f"{field}[{index}].key")    or "").strip()

        if not label and not prompt:
            continue                    # an empty row is simply no question

        if not label or not prompt:
            errors.append(f"Row {row}: fill in both the label and the "
                          f"question, or clear them both.")
            continue

        # Trust the hidden key only if it still looks like one we minted;
        # it arrives from the browser like any other form value.
        key = posted if posted and posted == slugify(posted) else slugify(label)

        if not key:
            errors.append(f"Row {row}: '{label}' doesn't contain any letters "
                          f"or numbers to build a name from.")
            continue
        if key in RESERVED_SLOT_KEYS:
            errors.append(f"Row {row}: '{label}' is reserved — the booking "
                          f"flow already collects that.")
            continue
        if key in seen:
            errors.append(f"Row {row}: '{label}' repeats an earlier question.")
            continue

        seen.add(key)
        question = {"key": key, "prompt": prompt}
        # Store the label only when the key can't produce it. Keeps a config
        # that matches its YAML byte-identical, so the override gets cleared
        # and the business goes back to inheriting from the file.
        if label != humanize(key):
            question["label"] = label
        questions.append(question)

    return questions, errors


def _set_nested(config, dotted_field, value):
    """Set a dotted path into a nested dict: 'business.phone' → config['business']['phone']."""
    parts = dotted_field.split(".")
    target = config
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value


def load_personas():
    """Load the shared persona presets."""
    with open("config/personas.yaml", "r") as f:
        return yaml.safe_load(f) or {}


def load_config(config_path, business_id=None):
    """Load a business config: YAML as the base, database overrides on top.

    YAML is the initial state from onboarding; the config_overrides table is
    the current state after any owner edits. Overrides win.

    business_id is optional so scripts that only need the file (ingestion,
    seeding) can skip the database entirely.
    """
    path = Path(config_path)
    with open(path, "r") as f:
        config = yaml.safe_load(f)

    if business_id is not None:
        from db import get_config_overrides
        for field, value in get_config_overrides(business_id).items():
            if field in EDITABLE_FIELDS:      # ignore anything stale or unexpected
                _set_nested(config, field, value)

        # Stamp the business's immutable slug onto the config, AFTER the
        # overrides so nothing an owner types can reach it. Anything that
        # needs a stable per-business identifier — the vector collection
        # above all — reads this rather than re-deriving one from the
        # business name, which is an editable display string.
        from db import get_business_by_id
        row = get_business_by_id(business_id)
        if row:
            config.setdefault("business", {})["slug"] = row["slug"]

    # Resolve the persona preset into actual persona text.
    preset = config.get("bot", {}).get("persona_preset")
    if preset:
        personas = load_personas()
        config.setdefault("bot", {})["persona"] = personas.get(
            preset, personas.get("warm", "")
        )

    log.info(f"Loaded config for: {config['business']['name']}")
    return config

def get_nested(config, dotted_field, default=None):
    """Read a dotted path from a nested dict: 'business.phone'."""
    target = config
    for part in dotted_field.split("."):
        if not isinstance(target, dict) or part not in target:
            return default
        target = target[part]
    return target



def substitute(text, config):
    """Replace {placeholder} tokens in a rule reply template with real values.

    Called at rule-match time (not load time) so every reply gets the
    current request's business values. Accepts config as an explicit
    parameter rather than reading from a global.

    Supported placeholders:
        {business_name}     business.name
        {phone}             business.phone
        {hours}             business.hours
        {address}           business.address
        {service_area}      business.service_area
        {services_examples} first two services joined with ", "
    """
    business = config["business"]
    services = config.get("services", [])

    # Build a comma-separated sample of services for booking prompts
    # e.g. "drain cleaning, leak repair"
    services_examples = ", ".join(services[:2]) if services else "our services"

    replacements = {
        "business_name":     business.get("name", ""),
        "phone":             business.get("phone", ""),
        "hours":             business.get("hours", ""),
        "address":           business.get("address", ""),
        "service_area":      business.get("service_area", ""),
        "services_examples": services_examples,
    }

    for key, value in replacements.items():
        text = text.replace(f"{{{key}}}", str(value))

    return text
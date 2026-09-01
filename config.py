# config.py — YAML configuration loader.
#
# No global CONFIG — configs load per-request so one server can serve
# many businesses from different YAML files simultaneously.
# app.py calls load_config() at the start of every request and passes
# the result through to every function that needs it.

import yaml
from pathlib import Path


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
}


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

    # Resolve the persona preset into actual persona text.
    preset = config.get("bot", {}).get("persona_preset")
    if preset:
        personas = load_personas()
        config.setdefault("bot", {})["persona"] = personas.get(
            preset, personas.get("warm", "")
        )

    print(f"[config] Loaded config for: {config['business']['name']}")
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
# config.py — YAML configuration loader.
#
# No global CONFIG — configs load per-request so one server can serve
# many businesses from different YAML files simultaneously.
# app.py calls load_config() at the start of every request and passes
# the result through to every function that needs it.

import yaml
from pathlib import Path


def load_config(config_path):
    """Load and return a business config dict from the given YAML file path.

    Called once per incoming request (not at module import) so the server
    can serve different businesses on different requests simultaneously.
    The returned dict is a plain Python object — no special class needed.
    """
    path = Path(config_path)
    with open(path, "r") as f:
        config = yaml.safe_load(f)
    print(f"[config] Loaded config for: {config['business']['name']}")
    return config


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
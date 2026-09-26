# calendar_sync.py — picks a calendar backend and forwards to it.
#
# Every caller in the app already says `calendar_sync.create_event(config, ...)`
# and passes config as the first argument, so config is the natural place to
# decide WHICH calendar that means. This module is that decision and nothing
# else: no scheduling logic, no API calls.
#
#     calendar:
#       provider: "google"        # the default when the key is absent
#       provider: "simulated"     # DB-backed, no network, for the demo
#
# Why a facade rather than an if-statement in each caller: there are fifteen
# call sites across scheduler.py, admin/routes.py and reconcile.py. Fifteen
# copies of the same branch is fifteen chances for one of them to forget.
# Here the branch exists once, and adding a third backend later touches only
# this file.
#
# The design rule the backends inherit: raise on failure rather than
# swallowing errors. The caller decides what to tell the customer — a silent
# failure that lets the bot confirm a booking nobody can see is the one
# outcome worth crashing to avoid.

import logging

import calendar_google
import calendar_sim

log = logging.getLogger("calendar")

# Backends must implement every name in _FORWARDED below.
_BACKENDS = {
    "google":    calendar_google,
    "simulated": calendar_sim,
}

DEFAULT_PROVIDER = "google"


def _backend(config):
    """Return the backend module this business's config asks for.

    An unknown provider falls back to Google with a warning rather than
    raising: a typo in one business's YAML shouldn't take down booking for
    a business whose config is fine.
    """
    name = (config.get("calendar") or {}).get("provider") or DEFAULT_PROVIDER
    backend = _BACKENDS.get(name)
    if backend is None:
        log.warning(
            "Unknown calendar provider %r — falling back to %s. "
            "Known providers: %s",
            name, DEFAULT_PROVIDER, ", ".join(sorted(_BACKENDS)),
        )
        backend = _BACKENDS[DEFAULT_PROVIDER]
    return backend


def register_backend(name, module):
    """Add a backend. Called by the simulated backend at import time."""
    _BACKENDS[name] = module


# ---------------------------------------------------------------------------
# The public surface — forwarded verbatim
# ---------------------------------------------------------------------------
#
# Written out one function at a time rather than generated with __getattr__
# so that the module's interface is readable here, and so a backend missing
# a function fails at the call with a clear AttributeError instead of
# somewhere stranger.

def is_enabled(config):
    return _backend(config).is_enabled(config)


def create_event(config, service_name, start_iso, customer_id, details=None,
                 customer_name=None):
    return _backend(config).create_event(
        config, service_name, start_iso, customer_id, details=details,
        customer_name=customer_name,
    )


def is_slot_available(config, start_iso, busy=None, service=None):
    return _backend(config).is_slot_available(
        config, start_iso, busy=busy, service=service
    )


def find_alternatives(config, desired_iso, service=None):
    return _backend(config).find_alternatives(
        config, desired_iso, service=service
    )


def slot_rejection_reason(config, start_iso, busy=None, service=None):
    return _backend(config).slot_rejection_reason(
        config, start_iso, busy=busy, service=service
    )


def delete_event(config, event_id):
    return _backend(config).delete_event(config, event_id)


def update_event_time(config, event_id, new_start_iso, service=None):
    return _backend(config).update_event_time(
        config, event_id, new_start_iso, service=service
    )


def fetch_changes(config, sync_token=None):
    return _backend(config).fetch_changes(config, sync_token=sync_token)

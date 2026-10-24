"""analytics.py — counting demo visitors with Cloudflare Web Analytics.

Cookieless and free: a small beacon script reports a page view (the page's
address, the referrer, the browser), nothing typed on the page. On only
when CF_WEB_ANALYTICS_TOKEN is set (Render), so a server on this machine
never reports, and only on the demo sandbox (2026-10-24):

  - the demo picker, and a demo's chat and text pages;
  - portal pages seen by a demo visitor.

Never a real business's customer chat (their customers aren't ours to
count), a real owner's or the operator's portal, or a page signed out of
the portal: the password-reset page's address carries its one-time token.
"""

import os
import re

SCRIPT_HOST = "https://static.cloudflareinsights.com"
REPORT_HOST = "https://cloudflareinsights.com"
SCRIPT = SCRIPT_HOST + "/beacon.min.js"


def token():
    """The site token from Cloudflare (Web Analytics, Add a site), or None.
    Public by design (it's in the page), but checked to be the shape
    Cloudflare gives (32 hex characters) so the setting can't put anything
    else on the page."""
    value = os.environ.get("CF_WEB_ANALYTICS_TOKEN", "").strip()
    return value if re.fullmatch(r"[0-9a-f]{32}", value) else None

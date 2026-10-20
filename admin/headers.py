# admin/headers.py — what the browser is told it may do with our pages.
#
# Until 2026-10-20 no security headers were sent at all. The ones that
# matter here, each against something concrete:
#
#   Content-Security-Policy  scripts run only from our own /static files, or
#       an inline <script> carrying this response's nonce. Injected markup
#       can't run code, even if an escaping mistake lets it onto a page.
#       The first sweep found one: menu.html put a menu item's name inside
#       onsubmit="return confirm('Remove {{ name }}…')", where HTML escaping
#       doesn't help (the browser unescapes &#39; before the JavaScript
#       runs), and item names come from photo and website imports. Inline
#       handlers are gone (static/confirm-submit.js), and the policy now
#       refuses any that come back.
#   frame-ancestors 'none' / X-Frame-Options  no other site may show our
#       pages in a frame, which is how a page tricks a signed-in owner into
#       clicking Cancel or Publish on a portal they can't see. When the
#       marketing-site demo widget arrives (notes/future_directions.md), the
#       chat page alone will need that site named here instead.
#   Strict-Transport-Security  once seen over HTTPS, the browser won't try
#       plain HTTP for a year. Browsers ignore it over http://127.0.0.1.
#   X-Content-Type-Options  an upload served back can't be sniffed into a
#       script. Referrer-Policy  a portal URL (business ids) isn't sent to
#       other sites in full.
#
# Hooked app-wide (after_app_request), like csrf.py, so pages outside /admin
# get the same headers.

from secrets import token_urlsafe

from flask import g

from admin import admin_bp


def nonce():
    """This response's script nonce, minted on first use."""
    if "csp_nonce" not in g:
        g.csp_nonce = token_urlsafe(16)
    return g.csp_nonce


# A template global rather than a context processor, for the reason csrf.py
# gives: imported macro files don't see the page's context.
admin_bp.add_app_template_global(nonce, "csp_nonce")


def policy(script_nonce):
    return "; ".join([
        "default-src 'self'",
        f"script-src 'self' 'nonce-{script_nonce}'",
        # Google Fonts: the stylesheet from one host, the font files from
        # another. No inline styles anywhere (style="" was removed earlier).
        "style-src 'self' https://fonts.googleapis.com",
        "font-src https://fonts.gstatic.com",
        # blob: is photo-upload.js shrinking a photo before it's sent.
        "img-src 'self' data: blob:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Strict-Transport-Security": "max-age=31536000",
}


@admin_bp.after_app_request
def add_headers(response):
    response.headers["Content-Security-Policy"] = policy(nonce())
    for name, value in HEADERS.items():
        response.headers.setdefault(name, value)
    return response

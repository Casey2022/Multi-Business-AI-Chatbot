"""links.py — absolute links for emails (a reset link, a support alert).

url_for(_external=True) takes its scheme from the request the app was
handed, and on Render that is plain http: the visitor's https ends at
Render's proxy. So the reset email said http://simonai-q92o.onrender.com/
admin/reset/<token> (measured 2026-10-24), and opening it sent the one-time
token unencrypted before Render's 301 to https. A link that leaves the
site is https, except for a server on this machine (localhost), which has
no certificate to answer it with.
"""

from flask import request, url_for

LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def is_local(host):
    """localhost, 127.0.0.1, [::1] or *.localhost, with or without a port."""
    host = host or ""
    name = host[:host.find("]") + 1] if host.startswith("[") else host.split(":")[0]
    return name in LOCAL_HOSTS or name.endswith(".localhost")


def public_url(endpoint, **values):
    """url_for(endpoint, **values) as a whole link, https unless local."""
    scheme = "http" if is_local(request.host) else "https"
    return url_for(endpoint, _external=True, _scheme=scheme, **values)

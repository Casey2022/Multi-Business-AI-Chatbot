# admin/auth.py — authentication and authorization for the admin portal.
#
# Two roles:
#   operator — sees every business (that's us)
#   owner    — scoped to a single business (that's the client)
#
# Authentication proves who you are; authorization decides what you may see.
# Both matter: without the second, any logged-in owner could read another
# business's conversations by editing the URL.

from functools import wraps
from flask import (render_template, request, redirect, url_for,
                   session, abort)

from admin import admin_bp
from db import verify_password, get_user_by_email

import logging
log = logging.getLogger("admin")
log_sec = logging.getLogger("security")


def current_user():
    """Return the logged-in user dict, or None."""
    email = session.get("user_email")
    return get_user_by_email(email) if email else None


@admin_bp.context_processor
def viewer():
    """Who's looking, available to every admin template.

    A context processor rather than an argument threaded through fifteen
    render_template calls: this is a property of the session, not of the
    page, and a page that forgot to pass it would silently render the
    wrong nav.

    home_url is where the wordmark goes, which depends on the role.
    Operators have a list of businesses to go back to; an owner has one
    business, and sending them to a list that immediately redirects them
    back is a link that does nothing twice.
    """
    # Set when starting a demo displaced somebody's real login — see
    # demo_start in app.py. It's what puts "Back to your account" in the nav.
    displaced = session.get("displaced_user")

    user = current_user()
    if not user:
        return {"viewer": None, "is_operator": False, "home_url": None,
                "displaced_user": displaced}

    if user["is_operator"]:
        home = url_for("admin.dashboard")
    elif user["business_id"]:
        home = url_for("admin.appointments", business_id=user["business_id"])
    else:
        # An owner with no business — a malformed row, or one whose demo was
        # swept while they were signed in. Without this guard url_for raises
        # and takes down every admin page for them, which turns a bad row
        # into a broken portal.
        log_sec.warning("User %s has neither a business nor operator rights",
                        user["email"])
        home = None

    return {
        "viewer": user,
        "is_operator": bool(user["is_operator"]),
        "home_url": home,
        "displaced_user": displaced,
    }


def login_required(f):
    """Require a logged-in user."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user():
            return redirect(url_for("admin.login"))
        return f(*args, **kwargs)
    return decorated


def operator_required(f):
    """Require an operator — for views that span businesses."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("admin.login"))
        if not user["is_operator"]:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def require_business_access(business_id):
    """Abort 403 unless the current user may view this business.

    Call this in every route that takes a business_id. Operators pass;
    owners pass only for their own business. Without this check the
    business_id in the URL is a suggestion rather than a permission.
    """
    user = current_user()
    if not user:
        abort(401)
    if user["is_operator"]:
        return
    if user["business_id"] != business_id:
        log_sec.warning(f"{user['email']} denied access to business {business_id}")
        abort(403)


def _login_ip():
    """The caller's address, honouring the proxy header Render sets.

    A plain helper, NOT a view. It was once accidentally sitting directly
    under the /login route decorator, which made it the login view: GET
    /admin/login returned the caller's IP address as the page, and
    url_for("admin.login") raised BuildError because no endpoint by that
    name existed. Nothing noticed for a week, because a logged-in session
    never visits the login page and the demo signs people in directly.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("admin.dashboard"))

    error = None
    if request.method == "POST":
        email    = request.form.get("email", "")
        password = request.form.get("password", "")

        # Throttle before checking the password, not after. bcrypt is
        # deliberately slow, so an unthrottled login form is both a
        # guessing oracle and a way to pin the CPU. Counted per IP and per
        # account named: per-IP alone lets a botnet spread one account's
        # guesses across many addresses, per-account alone lets one address
        # walk through a list of emails.
        import ratelimit
        allowed, wait = ratelimit.login_attempt(_login_ip(), email.lower())
        if not allowed:
            log_sec.warning("Login attempts throttled for %s / %r",
                            _login_ip(), email)
            return render_template(
                "admin/login.html",
                error=f"Too many attempts. Try again in {max(wait, 1)} seconds."
            ), 429

        user = verify_password(email, password)
        if user:
            session["user_email"] = user["email"]
            # permanent=True is what arms PERMANENT_SESSION_LIFETIME. It
            # reads backwards: permanent=False means a browser-session
            # cookie, which sounds safer but has no timeout at all — it
            # lives as long as the browser does, and browsers restore
            # sessions after a restart. permanent=True plus a lifetime is
            # an idle timeout, refreshed on each request.
            session.permanent = True
            log.info(f"Login: {user['email']} "
                  f"(operator={bool(user['is_operator'])})")
            return redirect(url_for("admin.dashboard"))

        # Deliberately vague — don't confirm whether the email exists.
        error = "Invalid credentials."
        log.warning(f"Failed login attempt for {email!r}")

    return render_template("admin/login.html", error=error)


@admin_bp.route("/logout")
def logout():
    session.pop("user_email", None)
    return redirect(url_for("admin.login"))
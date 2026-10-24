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
    """Return the logged-in user dict, or None.

    A session also carries the version it signed in under. Changing the
    password (a reset link) bumps the user's version, so every session from
    before ends here (2026-10-23). A session from before versions existed
    counts as version 0, which every account starts at."""
    email = session.get("user_email")
    if not email:
        return None
    user = get_user_by_email(email)
    if user and session.get("session_version", 0) != (user.get("session_version") or 0):
        log_sec.info("Ended a session from before %s's password changed", user["email"])
        session.pop("user_email", None)
        return None
    return user


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
    import ratelimit
    # Counted from the right, past the proxies (ratelimit.client_address):
    # the first entry is whatever the visitor sends (2026-10-23).
    return ratelimit.client_address(request.headers.get("X-Forwarded-For", ""),
                                    request.remote_addr)


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
            session["session_version"] = user.get("session_version") or 0
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


# ---------------------------------------------------------------------------
# Password reset (2026-10-23)
# ---------------------------------------------------------------------------

SAME_REPLY = ("If an account exists for that address, we've emailed a link to set a "
              "new password. It works once, for {minutes} minutes.")


def _reset_email(user, link):
    from email.message import EmailMessage
    from db import RESET_MINUTES
    msg = EmailMessage()
    msg["Subject"] = "Set a new password for Simon Says"
    msg["To"] = user["email"]
    msg.set_content(
        "Someone asked to set a new password for this Simon Says account.\n\n"
        f"To set one, open this link (it works once, for {RESET_MINUTES} minutes):\n"
        f"{link}\n\n"
        "If that wasn't you, ignore this email: your password hasn't changed.\n")
    return msg


@admin_bp.route("/forgot", methods=["GET", "POST"])
def forgot_password():
    """Ask for a reset link. The reply is the same whether or not the
    address has an account, and so is the rate limit, so this page can't be
    used to find out who has one. A demo account (no real inbox) never gets
    mail. Without SMTP set up nothing can be sent: that's logged for the
    operator, and the page still says the same."""
    from db import RESET_MINUTES, create_password_reset
    if request.method == "GET":
        return render_template("admin/forgot.html", sent=None)
    import ratelimit
    import support
    email = " ".join((request.form.get("email") or "").split()).lower()
    allowed, wait = ratelimit.reset_request(_login_ip(), email)
    if not allowed:
        log_sec.warning("Reset requests throttled for %s", _login_ip())
        return render_template("admin/forgot.html", sent=None,
                               error=f"Too many requests. Try again in {max(wait, 1)} "
                                     f"seconds."), 429
    user = get_user_by_email(email) if email else None
    if user and not user["email"].endswith("@demo.invalid"):
        settings = support.smtp_settings()
        if not settings:
            log_sec.warning("A password reset was asked for, but email isn't set up "
                            "(SMTP_HOST): nothing sent")
        else:
            token = create_password_reset(user["id"])
            msg = _reset_email(user, url_for("admin.reset_password_page", token=token,
                                             _external=True))
            msg["From"] = settings["from"]
            try:
                support.send_mail(msg, settings)
                log.info("Password reset link emailed to %s", user["email"])
            except Exception as e:
                log_sec.warning("Password reset email failed: %s", type(e).__name__)
    return render_template("admin/forgot.html", sent=SAME_REPLY.format(minutes=RESET_MINUTES))


@admin_bp.route("/reset/<token>", methods=["GET", "POST"])
def reset_password_page(token):
    """Set a new password with a link from forgot_password. Saving it spends
    the link and signs out every session; then the owner signs in fresh."""
    from db import user_for_reset, reset_password, MIN_PASSWORD_LENGTH
    user = user_for_reset(token)
    if not user:
        return render_template("admin/reset.html", expired=True), 410
    error = None
    if request.method == "POST":
        password = request.form.get("password") or ""
        if password != (request.form.get("confirm") or ""):
            error = "The two passwords don't match."
        else:
            try:
                reset_password(token, password)
                session.pop("user_email", None)
                log_sec.info("Password changed by reset link for %s", user["email"])
                from flask import flash
                flash("Password changed. Sign in with your new one.", "success")
                return redirect(url_for("admin.login"))
            except ValueError as e:
                error = str(e)
        return render_template("admin/reset.html", expired=False, error=error,
                               email=user["email"], min_length=MIN_PASSWORD_LENGTH), 400
    return render_template("admin/reset.html", expired=False, email=user["email"],
                           min_length=MIN_PASSWORD_LENGTH)


def is_demo_account(user):
    """A demo visitor's account: its password was made up for them and never
    shown, so there's nothing to change and no inbox to reset it from."""
    return bool(user) and user["email"].endswith("@demo.invalid")


@admin_bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    """Change your password while signed in (2026-10-23). Asks for the
    current one; tries count against the sign-in limits, so this page isn't
    a way round them to guess it. Every other session ends; this one stays."""
    from flask import flash
    from db import change_password as save, MIN_PASSWORD_LENGTH
    user = current_user()
    page = dict(min_length=MIN_PASSWORD_LENGTH, demo=is_demo_account(user), error=None)
    if page["demo"] and request.method == "POST":
        return render_template("admin/password.html", **page), 403
    if request.method == "GET":
        return render_template("admin/password.html", **page)
    import ratelimit
    allowed, wait = ratelimit.login_attempt(_login_ip(), user["email"])
    if not allowed:
        log_sec.warning("Password change attempts throttled for %s", user["email"])
        page["error"] = f"Too many attempts. Try again in {max(wait, 1)} seconds."
        return render_template("admin/password.html", **page), 429
    new = request.form.get("password") or ""
    if new != (request.form.get("confirm") or ""):
        page["error"] = "The two new passwords don't match."
        return render_template("admin/password.html", **page), 400
    try:
        changed = save(user["email"], request.form.get("current") or "", new)
    except ValueError as e:
        page["error"] = str(e)
        return render_template("admin/password.html", **page), 400
    session["session_version"] = changed["session_version"]
    log_sec.info("Password changed in the portal by %s", user["email"])
    flash("Password changed. Anywhere else you were signed in, you'll need the new one.",
          "success")
    return redirect(url_for("admin.change_password"))


@admin_bp.route("/logout")
def logout():
    session.pop("user_email", None)
    return redirect(url_for("admin.login"))
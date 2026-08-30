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


def current_user():
    """Return the logged-in user dict, or None."""
    email = session.get("user_email")
    return get_user_by_email(email) if email else None


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
        print(f"[security] {user['email']} denied access to business {business_id}")
        abort(403)


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("admin.dashboard"))

    error = None
    if request.method == "POST":
        email    = request.form.get("email", "")
        password = request.form.get("password", "")

        user = verify_password(email, password)
        if user:
            session["user_email"] = user["email"]
            session.permanent = False
            print(f"[admin] Login: {user['email']} "
                  f"(operator={bool(user['is_operator'])})")
            return redirect(url_for("admin.dashboard"))

        # Deliberately vague — don't confirm whether the email exists.
        error = "Invalid credentials."
        print(f"[admin] Failed login attempt for {email!r}")

    return render_template("admin/login.html", error=error)


@admin_bp.route("/logout")
def logout():
    session.pop("user_email", None)
    return redirect(url_for("admin.login"))
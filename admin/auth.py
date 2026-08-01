# admin/auth.py — login and logout for the admin section.
#
# Authentication strategy: single admin user, password stored in .env.
# Uses secrets.compare_digest for constant-time comparison (prevents
# timing attacks that could reveal the correct password character by character).
# Login state is stored in Flask's signed session cookie.

import os
import secrets
from flask import render_template, request, redirect, url_for, session, flash
from admin import admin_bp


def _check_password(submitted: str) -> bool:
    """Compare submitted password against ADMIN_PASSWORD in .env.

    secrets.compare_digest takes the same time regardless of where strings
    differ — prevents timing attacks. Always use this instead of == for
    password comparison.
    """
    correct = os.environ.get("ADMIN_PASSWORD", "")
    if not correct:
        return False
    return secrets.compare_digest(
        submitted.encode("utf-8"),
        correct.encode("utf-8")
    )


def login_required(f):
    """Decorator that redirects to login if the admin session isn't active.

    Usage: @login_required above any admin route that needs protection.
    """
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin.login"))
        return f(*args, **kwargs)
    return decorated


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    """Show login form (GET) or process credentials (POST)."""
    # If already logged in, skip to dashboard.
    if session.get("admin_logged_in"):
        return redirect(url_for("admin.dashboard"))

    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if _check_password(password):
            session["admin_logged_in"] = True
            session.permanent = False  # session ends when browser closes
            print("[admin] Login successful")
            return redirect(url_for("admin.dashboard"))
        else:
            # Vague error on purpose — don't confirm whether the username
            # or password was wrong.
            error = "Invalid credentials."
            print("[admin] Failed login attempt")

    return render_template("admin/login.html", error=error)


@admin_bp.route("/logout")
def logout():
    """Clear the admin session and redirect to login."""
    session.pop("admin_logged_in", None)
    print("[admin] Logged out")
    return redirect(url_for("admin.login"))
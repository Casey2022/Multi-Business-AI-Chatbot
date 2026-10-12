# admin/support_routes.py — the Help page, the operator's Support inbox, and
# the error page that links the two. Logic lives in support.py.
#
# Added 2026-10-12. Owners ask for help from any page ("Help" in the nav);
# the request lands in the operator's inbox and, when SMTP is set up, their
# email. The operator marks it resolved with a note the owner sees on their
# Help page.

import logging

from flask import abort, flash, redirect, render_template, request, url_for

import ratelimit
import support
from admin import admin_bp
from admin.auth import (_login_ip, current_user, login_required,
                        operator_required)
from db import (count_open_support_requests, get_business_by_id,
                get_support_request, list_support_requests,
                reopen_support_request, resolve_support_request)

log = logging.getLogger("admin")


@admin_bp.app_template_global()
def open_support_count():
    """Open requests, for the operator's nav. Never takes a page down."""
    try:
        return count_open_support_requests()
    except Exception:
        log.exception("Couldn't count support requests")
        return 0


def _render_help(user, business, page, error_id, message="", contact="", status=200):
    is_demo = bool(business and business.get("is_demo"))
    return render_template(
        "admin/help.html",
        business=business, page=page, error_id=error_id,
        attached=support.context_for(business, page, error_id),
        needs_contact=is_demo,
        reply_to=None if is_demo else (user["email"] if support.looks_like_email(user["email"]) else None),
        message=message, contact=contact, max_message=support.MAX_MESSAGE,
        requests=list_support_requests(user_email=user["email"], limit=20),
    ), status


@admin_bp.route("/help", methods=["GET", "POST"])
@login_required
def help_page():
    """Ask the operator for help. The page you came from and any error
    reference ride along in the query string (and back in hidden fields)."""
    user = current_user()
    business = get_business_by_id(user["business_id"]) if user["business_id"] else None
    page = support.clean_page(request.values.get("from"))
    error_id = support.clean_error_id(request.values.get("error"))

    if request.method == "POST":
        needs_contact = bool(business and business.get("is_demo"))
        message, contact, errors = support.check_form(
            request.form.get("message"), request.form.get("contact"), needs_contact)
        if not errors:
            allowed, wait = ratelimit.support(user["email"], _login_ip())
            if not allowed:
                minutes = max(1, round(wait / 60))
                errors.append(f"You've sent several requests in a short time. Try again "
                              f"in about {minutes} minute{'' if minutes == 1 else 's'}.")
        if errors:
            for e in errors:
                flash(e, "error")
            return _render_help(user, business, page, error_id, message,
                                contact or "", status=400)
        support.file_request(user, business, message, contact, page, error_id,
                             inbox_url=url_for("admin.support_inbox", _external=True))
        flash("Sent. We'll take a look and get back to you"
              + ("." if needs_contact and not contact else " by email.")
              + " You can see its status below.", "success")
        return redirect(url_for("admin.help_page"))

    return _render_help(user, business, page, error_id)


@admin_bp.route("/support")
@operator_required
def support_inbox():
    """Every help request, open ones first."""
    return render_template("admin/support.html",
                           requests=list_support_requests(),
                           email_ready=support.email_settings() is not None)


@admin_bp.route("/support/<int:request_id>/resolve", methods=["POST"])
@operator_required
def support_resolve(request_id):
    if not get_support_request(request_id):
        abort(404)
    resolve_support_request(request_id, current_user()["email"],
                            reply=(request.form.get("reply") or "")[:support.MAX_MESSAGE])
    flash(f"Request #{request_id} marked resolved.", "success")
    return redirect(url_for("admin.support_inbox"))


@admin_bp.route("/support/<int:request_id>/reopen", methods=["POST"])
@operator_required
def support_reopen(request_id):
    if not get_support_request(request_id):
        abort(404)
    reopen_support_request(request_id)
    flash(f"Request #{request_id} reopened.", "success")
    return redirect(url_for("admin.support_inbox"))


@admin_bp.errorhandler(500)
def server_error(e):
    """Something broke on an admin page. Give it a reference the owner can
    quote, write the same reference beside the traceback in the log, and
    offer the Help page with both already filled in.

    (Local `python3 app.py` runs in debug mode, which shows Flask's debugger
    instead; this page is what Render's visitors see.)"""
    error_id = support.new_error_id()
    original = getattr(e, "original_exception", None) or e
    log.error("Error %s on %s %s", error_id, request.method, request.path,
              exc_info=(type(original), original, original.__traceback__))
    page = support.clean_page(request.path)
    help_url = url_for("admin.help_page", error=error_id, **({"from": page} if page else {}))
    return render_template("admin/error.html", error_id=error_id, help_url=help_url), 500

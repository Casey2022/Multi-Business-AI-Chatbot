# admin/routes.py — dashboard and conversation viewer routes.

from flask import render_template, request, abort
from admin import admin_bp
from admin.auth import login_required
from db import get_all_businesses, get_conversation_list, get_conversation, get_business_by_number, get_appointments


@admin_bp.route("/")
@login_required
def dashboard():
    """Show all registered businesses."""
    businesses = get_all_businesses()
    return render_template("admin/dashboard.html", businesses=businesses)


@admin_bp.route("/business/<int:business_id>/conversations")
@login_required
def conversations(business_id):
    """Show conversation list or a specific conversation for a business.

    Without a `phone` query parameter: shows all unique phone numbers
    that have messaged this business, with last-message previews.

    With ?phone=<number>: shows the full message thread for that number.
    """
    businesses  = get_all_businesses()
    business    = next((b for b in businesses if b["id"] == business_id), None)

    if not business:
        abort(404)

    phone        = request.args.get("phone")
    thread       = None
    conversation_list = get_conversation_list(business_id)

    if phone:
        thread = get_conversation(business_id, phone)

    return render_template(
        "admin/conversations.html",
        business          = business,
        conversation_list = conversation_list,
        selected_phone    = phone,
        thread            = thread,
    )

@admin_bp.route("/business/<int:business_id>/appointments")
@login_required
def appointments(business_id):
    """Show all appointments/orders for a business."""
    businesses = get_all_businesses()
    business   = next((b for b in businesses if b["id"] == business_id), None)

    if not business:
        abort(404)

    appointment_list = get_appointments(business_id)

    return render_template(
        "admin/appointments.html",
        business         = business,
        appointment_list = appointment_list,
    )
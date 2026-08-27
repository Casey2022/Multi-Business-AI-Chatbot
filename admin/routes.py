# admin/routes.py — dashboard and conversation viewer routes.

from flask import render_template, request, abort, redirect, url_for, flash
from admin import admin_bp
from admin.auth import login_required
from config import load_config
import calendar_sync
from db import (get_all_businesses, get_conversation_list, get_conversation, get_business_by_number, get_appointments, get_appointment, cancel_appointment, reschedule_appointment)


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

    # Pull any owner-made calendar changes before rendering. Opportunistic
    # rather than scheduled — Render's free tier has no cron. Sync-token
    # polling makes this cheap: usually one API call returning nothing.
    from reconcile import reconcile_business
    try:
        reconcile_business(business)
    except Exception as e:
        print(f"[reconcile] Failed for {business['name']}: {e}")
    appointment_list = get_appointments(business_id)

    return render_template(
        "admin/appointments.html",
        business         = business,
        appointment_list = appointment_list,
    )
@admin_bp.route("/appointment/<int:appointment_id>/cancel", methods=["POST"])
@login_required

def cancel(appointment_id):
    """Cancel an appointment: calendar first, then database.

    Calendar-first because the owner acts on their calendar. A phantom
    appointment they believe is cancelled is worse than a stale admin row.
    """
    appt = get_appointment(appointment_id)
    if not appt:
        abort(404)

    business = next((b for b in get_all_businesses()
                     if b["id"] == appt["business_id"]), None)
    if not business:
        abort(404)

    config = load_config(business["config_path"])

    if appt.get("external_event_id") and calendar_sync.is_enabled(config):
        try:
            calendar_sync.delete_event(config, appt["external_event_id"])
        except Exception as e:
            print(f"[admin] Calendar delete FAILED for appt {appointment_id}: {e}")
            flash(f"Could not remove the calendar event: {e}. "
                  f"Nothing was cancelled.", "error")
            return redirect(url_for("admin.appointments",
                                    business_id=appt["business_id"]))

    cancel_appointment(appointment_id)
    flash("Appointment cancelled.", "success")
    return redirect(url_for("admin.appointments", business_id=appt["business_id"]))


@admin_bp.route("/appointment/<int:appointment_id>/reschedule", methods=["POST"])
@login_required
def reschedule(appointment_id):
    """Move an appointment to a new time: calendar first, then database."""
    appt = get_appointment(appointment_id)
    if not appt:
        abort(404)

    new_dt = (request.form.get("new_datetime") or "").strip()
    # HTML datetime-local gives "2026-08-27T14:00"; we store "2026-08-27 14:00".
    new_dt = new_dt.replace("T", " ")[:16]
    if not new_dt:
        flash("No new time provided.", "error")
        return redirect(url_for("admin.appointments",
                                business_id=appt["business_id"]))

    business = next((b for b in get_all_businesses()
                     if b["id"] == appt["business_id"]), None)
    config = load_config(business["config_path"])

    if appt.get("external_event_id") and calendar_sync.is_enabled(config):
        try:
            calendar_sync.update_event_time(
                config, appt["external_event_id"], new_dt
            )
        except Exception as e:
            print(f"[admin] Calendar update FAILED for appt {appointment_id}: {e}")
            flash(f"Could not move the calendar event: {e}. "
                  f"Nothing was changed.", "error")
            return redirect(url_for("admin.appointments",
                                    business_id=appt["business_id"]))

    reschedule_appointment(appointment_id, new_dt)
    flash(f"Appointment moved to {new_dt}.", "success")
    return redirect(url_for("admin.appointments", business_id=appt["business_id"]))
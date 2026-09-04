# admin/routes.py — dashboard and conversation viewer routes.

from flask import render_template, request, abort, redirect, url_for, flash
from admin import admin_bp
from admin.auth import login_required, operator_required, require_business_access, current_user
from config import load_config
import calendar_sync
from db import (get_all_businesses, get_conversation_list, get_conversation, get_business_by_number, get_appointments, get_appointment, cancel_appointment, reschedule_appointment)


@admin_bp.route("/")
@login_required
def dashboard():
    """Operators see all businesses; owners go straight to their own."""
    user = current_user()
    if not user["is_operator"]:
        return redirect(url_for("admin.appointments",
                                business_id=user["business_id"]))
    businesses = get_all_businesses()
    return render_template("admin/dashboard.html", businesses=businesses)


@admin_bp.route("/business/<int:business_id>/conversations")
@login_required
def conversations(business_id):
    require_business_access(business_id)
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
    require_business_access(business_id)
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
    require_business_access(appt["business_id"])

    business = next((b for b in get_all_businesses()
                     if b["id"] == appt["business_id"]), None)
    if not business:
        abort(404)

    config = load_config(business["config_path"], business["id"])

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
    require_business_access(appt["business_id"])

    new_dt = (request.form.get("new_datetime") or "").strip()
    # HTML datetime-local gives "2026-08-27T14:00"; we store "2026-08-27 14:00".
    new_dt = new_dt.replace("T", " ")[:16]
    if not new_dt:
        flash("No new time provided.", "error")
        return redirect(url_for("admin.appointments",
                                business_id=appt["business_id"]))

    business = next((b for b in get_all_businesses()
                     if b["id"] == appt["business_id"]), None)
    config = load_config(business["config_path"], business["id"])

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


@admin_bp.route("/business/<int:business_id>/settings", methods=["GET", "POST"])
@login_required
def settings(business_id):
    """Let an owner edit the safe subset of their configuration."""
    require_business_access(business_id)

    business = next((b for b in get_all_businesses() if b["id"] == business_id), None)
    if not business:
        abort(404)

    from config import EDITABLE_FIELDS, load_personas, load_config
    from db import set_config_override
    from admin.auth import current_user

    if request.method == "POST":
        user     = current_user()
        personas = load_personas()

        # Load the YAML WITHOUT overrides — this is the baseline we compare
        # against. Storing a value identical to the file would pin the field,
        # so it stops inheriting future YAML improvements for no reason.
        from config import get_nested
        from db import clear_config_override
        base = load_config(business["config_path"])

        for field, spec in EDITABLE_FIELDS.items():
            raw = request.form.get(field)
            if raw is None:
                continue

            if spec["type"] == "list":
                value = [line.strip() for line in raw.splitlines() if line.strip()]
            elif spec["type"] == "faq":
                value = []
                for line in raw.splitlines():
                    if "|" not in line:
                        continue
                    q, a = line.split("|", 1)
                    if q.strip() and a.strip():
                        value.append({"question": q.strip(), "answer": a.strip()})
            elif spec["type"] == "choice":
                if raw not in personas:
                    flash(f"Unknown personality: {raw}", "error")
                    continue
                value = raw
            else:
                value = raw.strip()

            if value == get_nested(base, field):
                # Matches the file — drop any override so the field goes back
                # to inheriting from YAML.
                clear_config_override(business_id, field)
            else:
                set_config_override(business_id, field, value,
                                    updated_by=user["email"])

        flash("Settings saved.", "success")
        return redirect(url_for("admin.settings", business_id=business_id))

    config = load_config(business["config_path"], business_id)
    return render_template(
        "admin/settings.html",
        business = business,
        config   = config,
        fields   = EDITABLE_FIELDS,
        personas = load_personas(),
    )

@admin_bp.route("/business/<int:business_id>/knowledge")
@login_required
def knowledge(business_id):
    """Show and edit the document sections the assistant answers from."""
    require_business_access(business_id)

    business = next((b for b in get_all_businesses() if b["id"] == business_id), None)
    if not business:
        abort(404)

    from db import get_documents
    return render_template(
        "admin/knowledge.html",
        business = business,
        sections = get_documents(business_id),
    )


@admin_bp.route("/business/<int:business_id>/knowledge/add", methods=["POST"])
@login_required
def knowledge_add(business_id):
    require_business_access(business_id)

    from db import add_document_section
    from admin.auth import current_user

    title = (request.form.get("title") or "").strip()
    body  = (request.form.get("body")  or "").strip()

    if not title or not body:
        flash("A section needs both a title and content.", "error")
    else:
        add_document_section(business_id, title, body,
                             updated_by=current_user()["email"])
        flash("Section added. Publish to make it live.", "success")

    return redirect(url_for("admin.knowledge", business_id=business_id))


@admin_bp.route("/knowledge/<int:document_id>/update", methods=["POST"])
@login_required
def knowledge_update(document_id):
    from db import get_connection, update_document_section
    from admin.auth import current_user

    # Derive the business from the section rather than trusting the URL.
    conn = get_connection()
    row  = conn.execute("SELECT business_id FROM documents WHERE id = ?",
                        (document_id,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    require_business_access(row["business_id"])

    title = (request.form.get("title") or "").strip()
    body  = (request.form.get("body")  or "").strip()

    if not title or not body:
        flash("A section needs both a title and content.", "error")
    else:
        update_document_section(document_id, title, body,
                                updated_by=current_user()["email"])
        flash("Section updated. Publish to make it live.", "success")

    return redirect(url_for("admin.knowledge", business_id=row["business_id"]))


@admin_bp.route("/knowledge/<int:document_id>/delete", methods=["POST"])
@login_required
def knowledge_delete(document_id):
    from db import get_connection, delete_document_section, get_documents

    conn = get_connection()
    row  = conn.execute("SELECT business_id FROM documents WHERE id = ?",
                        (document_id,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    business_id = row["business_id"]
    require_business_access(business_id)

    # A business with no sections has a bot that can only answer from its
    # config facts. Warn rather than prevent — it's their content.
    if len(get_documents(business_id)) <= 1:
        flash("That was the last section. Your assistant can no longer "
              "answer detailed questions until you add content.", "error")

    delete_document_section(document_id)
    flash("Section deleted. Publish to make it live.", "success")
    return redirect(url_for("admin.knowledge", business_id=business_id))


@admin_bp.route("/business/<int:business_id>/knowledge/publish", methods=["POST"])
@login_required
def knowledge_publish(business_id):
    """Re-ingest the knowledge base so customers see the current version.

    Blocking rather than backgrounded: re-ingestion is a handful of
    embedding API calls, and there's no worker process on this deployment.
    Slower, but the owner gets a real success or failure rather than a
    silent job they can't see.
    """
    require_business_access(business_id)

    business = next((b for b in get_all_businesses() if b["id"] == business_id), None)
    if not business:
        abort(404)

    from rag import ingest_documents
    from db import set_documents_clean
    from config import load_config

    try:
        ingest_documents(load_config(business["config_path"], business_id),
                         business_id=business_id)
        set_documents_clean(business_id)
        flash("Published — your assistant is now using the updated content.",
              "success")
    except Exception as e:
        # Deliberately do NOT clear the flag: the owner must keep seeing
        # "unpublished changes" until a publish actually succeeds, or they'd
        # believe stale content was live.
        print(f"[knowledge] Publish FAILED for {business['name']}: {e}")
        flash(f"Publishing failed: {e}. Your previous content is still live.",
              "error")

    return redirect(url_for("admin.knowledge", business_id=business_id))


@admin_bp.route("/knowledge/<int:document_id>/history")
@login_required
def knowledge_history(document_id):
    from db import get_connection, get_document_versions

    conn = get_connection()
    doc  = conn.execute("SELECT * FROM documents WHERE id = ?",
                        (document_id,)).fetchone()
    conn.close()
    if not doc:
        abort(404)
    doc = dict(doc)
    require_business_access(doc["business_id"])

    business = next((b for b in get_all_businesses()
                     if b["id"] == doc["business_id"]), None)

    return render_template(
        "admin/knowledge_history.html",
        business = business,
        section  = doc,
        versions = get_document_versions(document_id),
    )
# admin/routes.py — dashboard and conversation viewer routes.

from flask import render_template, request, abort, redirect, url_for, flash
from admin import admin_bp
from admin.auth import login_required, operator_required, require_business_access, current_user
from config import load_config
import calendar_sync
from db import (get_all_businesses, get_business_by_id, get_conversation_list, get_conversation, get_business_by_number, get_appointments, get_appointment, cancel_appointment, reschedule_appointment)

import logging
log = logging.getLogger("admin")
log_rec = logging.getLogger("reconcile")
log_kb = logging.getLogger("knowledge")


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
    business = get_business_by_id(business_id)

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

def _detail_rows(raw, labels):
    """Stored booking answers as [(label, value)], ready to render.

    Returns [] for anything unparseable rather than raising: a malformed
    details blob on one appointment should cost that row its detail line,
    not cost the owner the page.
    """
    import json as _json
    from config import humanize
    if not raw or raw == "{}":
        return []
    try:
        stored = _json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(stored, dict):
        return []

    rows = []
    for key, value in stored.items():
        if key.startswith("_") or value in (None, "", []):
            continue
        rows.append((labels.get(key) or humanize(key), str(value)))
    return rows


@admin_bp.route("/business/<int:business_id>/appointments")
@login_required
def appointments(business_id):
    require_business_access(business_id)
    """Show all appointments/orders for a business."""
    business = get_business_by_id(business_id)

    if not business:
        abort(404)

    # Pull any owner-made calendar changes before rendering. Opportunistic
    # rather than scheduled — Render's free tier has no cron. Sync-token
    # polling makes this cheap: usually one API call returning nothing.
    from reconcile import reconcile_business
    try:
        reconcile_business(business)
    except Exception as e:
        log_rec.warning(f"Failed for {business['name']}: {e}")

    # Sweep expired rate-limit counters while we're already doing
    # housekeeping. The table only ever grows: every window a caller opens
    # leaves a row behind, and nothing had ever deleted one — the function
    # to do it was written and then never called, which is becoming a
    # recognisable shape in this codebase. Riding the same opportunistic
    # hook as reconciliation rather than a timer, for the same reason:
    # there is no scheduler here, and a late prune costs some dead rows.
    try:
        from db import prune_rate_limits, prune_personal_data
        removed = prune_rate_limits()
        if removed:
            log_rec.info("Pruned %d expired rate-limit row(s)", removed)
        # And personal data past its retention window — messages, abandoned
        # booking state, cached addresses. Same hook for the same reason.
        prune_personal_data()
    except Exception as e:
        # Housekeeping must never cost someone their appointments page.
        log_rec.warning("Housekeeping failed: %s", e)
    appointment_list = get_appointments(business_id)

    # Which week each appointment falls in, relative to the current one, so
    # the "Scheduled for" cell can link straight to the right calendar week
    # rather than always landing on today's.
    from datetime import datetime, timedelta
    today       = datetime.now().date()
    this_monday = today - timedelta(days=today.weekday())

    for appt in appointment_list:
        try:
            when = datetime.strptime(appt["datetime"], "%Y-%m-%d %H:%M").date()
            appt_monday = when - timedelta(days=when.weekday())
            appt["week_offset"] = (appt_monday - this_monday).days // 7

        except (ValueError, TypeError):
            appt["week_offset"] = 0

    # Turn the stored answers into something a person reads. The column used
    # to print the raw JSON — {"service_address": "522 Penbrooke Dr", ...} —
    # braces, quotes, underscored keys and all. The labels come from
    # question_labels(), which is the one place that decides how a stored
    # answer is named to a human: the customer's confirmation, the calendar
    # event description and the appointment page all read from it, so an
    # owner who renames a question in settings sees the new name here too.
    # Anything with no configured label falls back to humanize(), which is
    # where the underscores go.
    from config import load_config, question_labels, humanize
    labels = {}
    try:
        labels = question_labels(load_config(business["config_path"], business_id))
    except Exception as e:
        # A broken config shouldn't cost the owner their appointments list;
        # humanize() alone still produces readable labels.
        log.warning("Couldn't load labels for %s: %s", business["name"], e)

    for appt in appointment_list:
        appt["detail_rows"] = _detail_rows(appt.get("details"), labels)

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

    business = get_business_by_id(appt["business_id"])
    if not business:
        abort(404)

    config = load_config(business["config_path"], business["id"])

    if appt.get("external_event_id") and calendar_sync.is_enabled(config):
        try:
            calendar_sync.delete_event(config, appt["external_event_id"])
        except Exception as e:
            log.warning(f"Calendar delete FAILED for appt {appointment_id}: {e}")
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

    business = get_business_by_id(appt["business_id"])
    config = load_config(business["config_path"], business["id"])

    if appt.get("external_event_id") and calendar_sync.is_enabled(config):
        try:
            # Pass the service: the moved event has to keep its own
            # length. Without it a three-hour install rescheduled by the
            # owner would quietly become a one-hour one on the calendar,
            # and the next booking would be let into time that isn't free.
            calendar_sync.update_event_time(
                config, appt["external_event_id"], new_dt,
                service=appt.get("service"),
            )
        except Exception as e:
            log.warning(f"Calendar update FAILED for appt {appointment_id}: {e}")
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

    business = get_business_by_id(business_id)
    if not business:
        abort(404)

    from config import (EDITABLE_FIELDS, MAX_EXTRA_QUESTIONS, choice_value,
                        extra_question_rows, load_personas, load_config)
    from db import set_config_override
    from admin.auth import current_user

    if request.method == "POST":
        user     = current_user()
        personas = load_personas()

        # Load the YAML WITHOUT overrides — this is the baseline we compare
        # against. Storing a value identical to the file would pin the field,
        # so it stops inheriting future YAML improvements for no reason.
        from config import get_nested, parse_extra_questions, coerce_choice
        from db import clear_config_override
        base = load_config(business["config_path"])

        had_error = False

        for field, spec in EDITABLE_FIELDS.items():
            # Extra questions post one input per row rather than a single
            # named field, so they're read from the whole form.
            if spec["type"] == "questions":
                value, errors = parse_extra_questions(request.form)
                if value is None:
                    continue        # this form doesn't carry the questions
                if errors:
                    # Reject the whole field rather than save a partly
                    # understood list — silently dropping a question the
                    # owner typed is worse than changing nothing.
                    for message in errors:
                        flash(message, "error")
                    had_error = True
                    continue
                if value == get_nested(base, field):
                    clear_config_override(business_id, field)
                else:
                    set_config_override(business_id, field, value,
                                        updated_by=user["email"])
                continue

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
            elif spec["type"] == "select":
                if raw not in spec["options"]:
                    flash(f"{spec['label']}: {raw!r} isn't one of the choices.",
                          "error")
                    had_error = True
                    continue
                value = coerce_choice(raw, spec)
            elif spec["type"] == "choice":
                if raw not in personas:
                    flash(f"Unknown personality: {raw}", "error")
                    had_error = True
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

        if had_error:
            flash("Your other settings were saved.", "success")
        else:
            flash("Settings saved.", "success")
        return redirect(url_for("admin.settings", business_id=business_id))

    config = load_config(business["config_path"], business_id)

    # Whether this deployment can check an address at all. The service-area
    # controls are the only settings whose effect depends on something the
    # owner can't see or set, so they're the only ones that have to say so.
    from geocode import key_configured
    return render_template(
        "admin/settings.html",
        address_check_ready = key_configured(),
        business = business,
        config   = config,
        fields   = EDITABLE_FIELDS,
        personas = load_personas(),
        max_extra_questions = MAX_EXTRA_QUESTIONS,
        question_rows       = extra_question_rows(config),
        choice_values       = {
            field: choice_value(config, field, spec)
            for field, spec in EDITABLE_FIELDS.items()
            if spec["type"] == "select"
        },
    )

@admin_bp.route("/business/<int:business_id>/knowledge")
@login_required
def knowledge(business_id):
    """Show and edit the document sections the assistant answers from."""
    require_business_access(business_id)

    business = get_business_by_id(business_id)
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

    business = get_business_by_id(business_id)
    if not business:
        abort(404)

    from rag import ingest_documents
    from db import set_documents_clean
    from config import load_config
    from demo import fork_collection

    # Before anything reads the config, make sure this business owns the
    # collection it is about to rebuild. A demo clone shares its template's
    # collection until this moment; publishing without forking first would
    # rebuild a real client's knowledge base out of a visitor's edits. It has
    # to happen before load_config, not after, because load_config is what
    # stamps the collection name onto the config — the same ordering trap as
    # writing into pending after set_state. No-op for a real business.
    fork_collection(business_id)

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
        log_kb.warning(f"Publish FAILED for {business['name']}: {e}")
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

    business = get_business_by_id(doc["business_id"])

    return render_template(
        "admin/knowledge_history.html",
        business = business,
        section  = doc,
        versions = get_document_versions(document_id),
    )

@admin_bp.route("/business/<int:business_id>/calendar")
@login_required
def calendar_view(business_id):
    """Week view of appointments, rendered from the database.

    Deliberately not read from Google: the appointments table has everything
    needed, is already scoped per business, and avoids an API call per page
    load. The gap is that events the owner creates directly in their calendar
    (a dentist appointment, say) block availability but never appear here,
    because reconciliation only updates appointments the bot created.
    """
    require_business_access(business_id)

    business = get_business_by_id(business_id)
    if not business:
        abort(404)

    from datetime import datetime, timedelta
    from db import get_appointments
    from config import load_config

    config = load_config(business["config_path"], business_id)
    sched  = config.get("calendar", {}).get("scheduling", {})
    hours  = sched.get("business_hours", {})

    # Which week? Offset in weeks from the current one, so the arrows work.
    try:
        offset = int(request.args.get("week", 0))
    except ValueError:
        offset = 0

    # Optional appointment to pick out visually — set when arriving from the
    # appointments list, so the owner sees which one they clicked rather than
    # scanning the week.
    try:
        highlight = int(request.args.get("highlight", 0)) or None
    except ValueError:
        highlight = None

    today       = datetime.now().date()
    week_start  = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    days        = [week_start + timedelta(days=i) for i in range(7)]

    # Grid bounds from business hours — no point rendering hours the
    # business is never open.
    open_hours = []
    for d in days:
        h = hours.get(d.weekday(), hours.get(str(d.weekday())))
        if h:
            open_hours.append((int(h[0][:2]), int(h[1][:2])))
    if open_hours:
        grid_start = min(s for s, _ in open_hours)
        grid_end   = max(e for _, e in open_hours)
    else:
        grid_start, grid_end = 9, 17

    # Bucket appointments by (day index, hour).
    slots = {}
    for appt in get_appointments(business_id):
        if appt["status"] == "cancelled":
            continue
        try:
            when = datetime.strptime(appt["datetime"], "%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            continue
        if not (week_start <= when.date() <= days[-1]):
            continue
        key = (when.weekday(), when.hour)
        slots.setdefault(key, []).append({**appt, "minute": when.minute})

    # Which days are open, for shading closed ones.
    open_days = {
        d.weekday(): bool(hours.get(d.weekday(), hours.get(str(d.weekday()))))
        for d in days
    }

    return render_template(
        "admin/calendar.html",
        business   = business,
        days       = days,
        hours      = list(range(grid_start, grid_end + 1)),
        slots      = slots,
        open_days  = open_days,
        offset     = offset,
        today      = today,
        highlight  = highlight,
    )

@admin_bp.route("/appointment/<int:appointment_id>")
@login_required
def appointment_detail(appointment_id):
    """Everything about one appointment, with room for the actions.

    Exists because the cancel and reschedule controls were crammed into a
    table cell, and because the calendar needed somewhere to link to.
    """
    from db import get_appointment

    appt = get_appointment(appointment_id)
    if not appt:
        abort(404)
    require_business_access(appt["business_id"])

    business = get_business_by_id(appt["business_id"])

    # details is stored as JSON text; parse for display.
    import json
    try:
        details = json.loads(appt.get("details") or "{}")
    except (ValueError, TypeError):
        details = {}

    # Label each answer with what the business currently calls that question,
    # so fixing a typo in the settings editor fixes it on past appointments
    # too. Keys with no matching question left (the owner deleted it) fall
    # back to the humanized key in the template.
    try:
        address_checks = json.loads(appt.get("address_check") or "{}")
    except (ValueError, TypeError):
        address_checks = {}

    from config import question_labels
    detail_labels = {}
    if business:
        detail_labels = question_labels(
            load_config(business["config_path"], appt["business_id"])
        )

    return render_template(
        "admin/appointment_detail.html",
        business = business,
        appt     = appt,
        details  = details,
        detail_labels = detail_labels,
        address_checks = address_checks,
    )
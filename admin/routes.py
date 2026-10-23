# admin/routes.py — dashboard and conversation viewer routes.

from flask import render_template, request, abort, redirect, url_for, flash
from admin import admin_bp
from admin.auth import login_required, operator_required, require_business_access, current_user
from config import load_config
import budget
import calendar_sync
from db import (get_all_businesses, get_business_by_id, get_conversation_list, get_conversation, get_business_by_number, get_appointments, get_appointment, cancel_appointment, reschedule_appointment, notifications_for_appointment)

import copy
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
    from reconcile import maybe_reconcile
    maybe_reconcile(business, min_interval=0)

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
    import clock
    from config import load_config
    # The business's today, not the server's: on Render (UTC) this jumped a
    # day early every evening, putting Sunday-night bookings a week off.
    today       = clock.business_today(
        load_config(business["config_path"], business_id))
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
    from notify import notify_change, status_line
    status, reason = notify_change(appt, config, "cancelled")
    flash(f"Appointment cancelled. {status_line(status, reason)}", "success")
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
    if new_dt != appt.get("datetime"):
        from notify import notify_change, status_line
        status, reason = notify_change(appt, config, "moved", new_datetime=new_dt)
        flash(f"Appointment moved to {new_dt}. {status_line(status, reason)}",
              "success")
    else:
        flash("That's the time it already had; nothing changed.", "success")
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
        # As the page was drawn, before anything below is saved: a service
        # renamed in this same form is still its old name in the questions'
        # ticks (browser check, 2026-10-23: "'Colour history' is set for
        # 'colour', which this business doesn't offer any more").
        shown = load_config(business["config_path"], business_id)

        had_error = False

        for field, spec in EDITABLE_FIELDS.items():
            # Extra questions post one input per row rather than a single
            # named field, so they're read from the whole form.
            if spec["type"] == "services":
                import services
                value, errors = services.parse(request.form, shown, field)
                if value is None:
                    continue        # this form doesn't carry the services
                if errors:
                    for message in errors:
                        flash(f"Services offered: {message}.", "error")
                    had_error = True
                    continue
                if services.same_as_file(value, base.get("services")):
                    clear_config_override(business_id, field)
                else:
                    set_config_override(business_id, field, value,
                                        updated_by=user["email"])
                continue
            if spec["type"] == "hours":
                from config import parse_weekly_hours
                import scheduling
                value, errors = parse_weekly_hours(request.form, field)
                if value is None:
                    continue        # this form doesn't carry the hours table
                if errors:
                    for message in errors:
                        flash(f"Opening hours: {message}.", "error")
                    had_error = True
                    continue
                if scheduling.weekly_hours(value) == scheduling.weekly_hours(
                        get_nested(base, field)):
                    clear_config_override(business_id, field)
                else:
                    set_config_override(business_id, field, value,
                                        updated_by=user["email"])
                continue
            if spec["type"] == "questions":
                value, errors = parse_extra_questions(
                    request.form,
                    # The names the page showed (a renamed service's
                    # current name, not the YAML's).
                    offered_services=shown.get("services") or [])
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
            elif spec["type"] == "select":
                if raw not in spec["options"]:
                    flash(f"{spec['label']}: {raw!r} isn't one of the choices.",
                          "error")
                    had_error = True
                    continue
                value = coerce_choice(raw, spec)
                if field == "menu_enabled" and not value:
                    reason = _menu_cannot_switch_off(business)
                    if reason:
                        flash(reason, "error")
                        had_error = True
                        continue
            elif spec["type"] == "choice":
                if raw not in personas:
                    flash(f"Unknown personality: {raw}", "error")
                    had_error = True
                    continue
                value = raw
            elif spec["type"] == "long_text":
                import booking_control
                value = " ".join(raw.split())
                if len(value) > booking_control.MAX_MESSAGE:
                    flash(f"{spec['label']}: keep it under {booking_control.MAX_MESSAGE} "
                          f"characters.", "error")
                    had_error = True
                    continue
                current = load_config(business["config_path"], business_id)
                if not value or value in (booking_control.DEFAULT_MESSAGE,
                                          booking_control.default_message(current)):
                    clear_config_override(business_id, field)   # the standard wording
                    continue
            elif spec["type"] == "date":
                value = raw.strip()
                if not value:
                    clear_config_override(business_id, field)
                    continue
                from datetime import date as _date
                import clock
                try:
                    chosen = _date.fromisoformat(value)
                except ValueError:
                    flash(f"{spec['label']}: that isn't a date.", "error")
                    had_error = True
                    continue
                if chosen <= clock.business_today(load_config(business["config_path"], business_id)):
                    flash(f"{spec['label']}: choose a day after today.", "error")
                    had_error = True
                    continue
            else:
                value = raw.strip()

            if field == "booking.accepting" and value is True and get_nested(base, field) is None:
                # Taking bookings is the default; no need to pin it.
                clear_config_override(business_id, field)
                continue

            if value == get_nested(base, field):
                # Matches the file — drop any override so the field goes back
                # to inheriting from YAML.
                clear_config_override(business_id, field)
            else:
                set_config_override(business_id, field, value,
                                    updated_by=user["email"])

        # Taking bookings again: a resume date would mean nothing now, and
        # would quietly apply the next time bookings are paused.
        if request.form.get("booking.accepting") == "yes":
            clear_config_override(business_id, "booking.resume_on")

        if had_error:
            flash("Your other settings were saved.", "success")
        else:
            flash("Settings saved.", "success")
        return redirect(url_for("admin.settings", business_id=business_id))

    config = load_config(business["config_path"], business_id)
    import booking_control
    pause = booking_control.pause_state(config)

    # Whether this deployment can check an address at all. The service-area
    # controls are the only settings whose effect depends on something the
    # owner can't see or set, so they're the only ones that have to say so.
    from geocode import key_configured
    from policy import policy_text
    import notice_form
    import services
    return render_template(
        "admin/settings.html",
        address_check_ready = key_configured(),
        # The catalogue the per-question service checkboxes are drawn from.
        offered_services    = config.get("services") or [],
        business = business,
        config   = config,
        # Rules kept as data (policy.py), shown read-only: the owner should
        # see exactly what the assistant applies, and the Knowledge page no
        # longer has them.
        policy_lines = policy_text(config).splitlines(),
        notice_lines = notice_form.describe(config),
        hours_rows = weekly_hours_rows(config),
        service_rows = services.rows(config),
        fields   = EDITABLE_FIELDS,
        personas = load_personas(),
        max_extra_questions = MAX_EXTRA_QUESTIONS,
        question_rows       = extra_question_rows(config),
        choice_values       = {
            field: choice_value(config, field, spec)
            for field, spec in EDITABLE_FIELDS.items()
            if spec["type"] == "select"
        } | {"menu_enabled": "yes" if menu_switched_on(business) else "no",
             "booking.accepting": "no" if pause["paused"] else "yes"},
        pause = pause,
        paused_message = booking_control.message_template(config),
        menu_lock = _menu_cannot_switch_off(business),
    )

@admin_bp.route("/business/<int:business_id>/knowledge")
@login_required
def knowledge(business_id):
    """Show and edit the document sections the assistant answers from."""
    require_business_access(business_id)

    business = get_business_by_id(business_id)
    if not business:
        abort(404)
    return _knowledge_page(business)


def _knowledge_page(business, conflict=None, status=200):
    """The Knowledge base page. `conflict` is an edit that wasn't saved
    because the section changed under it (knowledge_update): {"id",
    "title", "body"}, shown beside the saved version."""
    from db import get_documents, unpublished_changes, get_upload_proposal
    business_id = business["id"]
    return render_template(
        "admin/knowledge.html",
        business = business,
        conflict = conflict,
        sections = get_documents(business_id),
        changes = unpublished_changes(business),
        pending_upload = get_upload_proposal(business_id),
        price_lines = section_price_lines(business, get_documents(business_id)),
        notice_lines = section_notice_lines(business, get_documents(business_id)),
        menu_changes = menu_changes_waiting(business),
        example_title = example_section_title(business),
    ), status


# Where Publish and Discard send you back to: the page you pressed them on.
# Only these named pages, never a URL from the form (an open redirect).
_BACK_TO = {"knowledge": "admin.knowledge", "menu": "admin.menu_page",
            "settings": "admin.settings", "cancellation": "admin.cancellation_rules",
            "notice": "admin.booking_notice"}


def _back_to(business_id):
    # The Help page too: the owner assistant drafts menu changes there, and
    # the banner lets the owner publish them without leaving the page.
    if request.form.get("back") == "help":
        return redirect(url_for("admin.help_page") + "#assistant")
    endpoint = _BACK_TO.get(request.form.get("back", ""), "admin.knowledge")
    return redirect(url_for(endpoint, business_id=business_id))


@admin_bp.app_template_global()
def booking_pause(business):
    """{"resume_on", "reply"} while this business has new bookings paused,
    else None. Drawn as a banner on every business page (base.html)."""
    import booking_control
    try:
        fresh = get_business_by_id(business["id"])
        config = load_config(fresh["config_path"], fresh["id"])
    except Exception:
        return None
    state = booking_control.pause_state(config)
    if not state["paused"]:
        return None
    return {"resume_on": state["resume_on"], "reply": booking_control.paused_reply(config)}


@admin_bp.app_template_global()
def menu_switched_on(business):
    """Whether this business has a Menu page (Settings: "Prices on a menu?").
    The nav and the Businesses list leave the Menu tab out when it's off."""
    import menu
    fresh = get_business_by_id(business["id"]) if business else None
    if not fresh:
        return False
    try:
        config = load_config(fresh["config_path"], fresh["id"])
    except Exception:
        return True          # a broken config shouldn't hide the tab that fixes it
    if config.get(menu.ENABLED_FIELD) is not None:
        return bool(config[menu.ENABLED_FIELD])
    return menu.has_items(config) or menu.has_items(menu.with_draft(config, fresh["id"]))


def _menu_cannot_switch_off(business):
    """Why the menu can't be switched off right now (None when it can).

    Off while items are still there would make prices disappear from every
    answer at once, with nothing on screen saying why. So the owner empties
    the menu (live and draft) first, which they can see and undo."""
    import menu
    config = load_config(business["config_path"], business["id"])
    if menu.has_items(config) or menu.has_items(menu.with_draft(config, business["id"])):
        return ("The menu still has items, so it can't be switched off. Remove them "
                "on the Menu page and publish first, so no prices vanish by surprise.")
    return None


def _menu_switched_off(business):
    """A redirect to Settings when this business has the menu switched off."""
    if menu_switched_on(business):
        return None
    flash("This business doesn't keep prices on a menu. To add one, switch "
          "“Prices on a menu?” on here first.", "error")
    return redirect(url_for("admin.settings", business_id=business["id"]))


@admin_bp.app_template_global()
def pending_changes(business):
    """What Publish would put live, as short lines ([] when nothing waits).
    The banner on the Knowledge base, Menu and Settings pages shows these."""
    from db import unpublished_changes
    fresh = get_business_by_id(business["id"]) if business else None
    if not fresh or not fresh["documents_dirty"]:
        return []
    changes = unpublished_changes(fresh)
    lines = []
    if changes["edited"]:
        n = len(changes["edited"])
        lines.append(f"{n} section{'' if n == 1 else 's'} edited")
    if changes["new"]:
        n = len(changes["new"])
        lines.append(f"{n} new section{'' if n == 1 else 's'}")
    if changes["deleted"]:
        lines.append("deleted: " + ", ".join(changes["deleted"]))
    lines += [f"menu: {line}" for line in menu_changes_waiting(fresh)]
    import policy_form
    import notice_form
    try:
        live = load_config(fresh["config_path"], fresh["id"])
        lines += policy_form.waiting_lines(live, fresh["id"])
        lines += notice_form.waiting_lines(live, fresh["id"])
    except Exception:
        pass
    return lines or ["knowledge-base changes"]


def weekly_hours_rows(config):
    """The Settings hours table: a row per weekday, ticked when open. A
    closed day shows the hours of the nearest open day, so ticking it
    starts from something sensible."""
    import scheduling
    from config import WEEKDAY_NAMES
    week = scheduling.weekly_hours(
        ((config.get("calendar") or {}).get("scheduling") or {}).get("business_hours"))
    usual = next(iter(week.values()), ("09:00", "17:00"))
    return [{"index": d, "name": WEEKDAY_NAMES[d], "open": d in week,
             "start": week.get(d, usual)[0], "end": week.get(d, usual)[1]}
            for d in range(7)]


def section_price_lines(business, sections):
    """{section id: the menu price lines that section's chunk carries}. The
    owner sees them read-only under the section: the menu (config menu:) is
    the only copy of those prices."""
    import menu
    try:
        config = menu.with_draft(load_config(business["config_path"], business["id"]),
                                 business["id"])
    except Exception:
        return {}
    if not menu.items(config):
        return {}
    return {s["id"]: line for s in sections
            if (line := menu.price_lines_for(s["title"], config))}


def section_notice_lines(business, sections):
    """{section id: the notice line that section's chunk carries}, shown
    read-only under it like the price lines: booking.notice is the only copy
    of the numbers (notes/booking_notice.md), so an owner who saw a cake
    section with no notice in it shouldn't type the number back in."""
    import policy
    import notice_form
    try:
        # The draft, like the price lines: the owner sees what Publish will do.
        config = notice_form.with_draft(load_config(business["config_path"], business["id"]),
                                        business["id"])
    except Exception:
        return {}
    return {s["id"]: line for s in sections
            if (line := policy.notice_line_for(s["title"], config))}


def menu_changes_waiting(business):
    """Lines describing the unpublished menu changes (empty if none)."""
    import menu
    try:
        live = load_config(business["config_path"], business["id"])
    except Exception:
        return []
    if menu.draft_menu(business["id"]) is None:
        return []
    return menu.differences(live, menu.with_draft(live, business["id"]))


def clean_section_text(value):
    """A submitted title or body as stored: plain newlines, no surrounding
    whitespace. Browsers send textareas with CRLF line endings."""
    return (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def example_section_title(business):
    """A section title in this business's own words, for the page's hints.

    The hint said "Drain cleaning", not "Our services" for every business,
    which reads oddly on a pizza shop or a salon. The business's first
    listed service is the kind of words a customer would use.
    """
    import re
    try:
        services = load_config(business["config_path"],
                               business["id"]).get("services") or []
    except Exception:
        services = []
    for service in services:
        words = re.sub(r"\s*\(.*?\)", "", str(service)).strip()
        if words:
            return words[0].upper() + words[1:]
    return "Opening hours"


@admin_bp.route("/business/<int:business_id>/knowledge/preview", methods=["POST"])
@login_required
def knowledge_preview(business_id):
    """Answer a few questions from the unpublished sections, beside today's
    answers, so the owner sees what Publish would change before customers
    do. Renders the page directly with the results (nothing is saved)."""
    require_business_access(business_id)
    business = get_business_by_id(business_id)
    if not business:
        abort(404)

    import ratelimit
    import preview
    from db import get_documents, unpublished_changes, get_upload_proposal

    allowed, wait = ratelimit.knowledge_preview(business_id)
    if not allowed:
        flash(f"Previews are limited to a few a minute. Try again in "
              f"{max(wait, 1)} seconds.", "error")
        return redirect(url_for("admin.knowledge", business_id=business_id))
    note = budget.refusal()
    if note:
        flash(note, "error")
        return redirect(url_for("admin.knowledge", business_id=business_id))

    config = load_config(business["config_path"], business_id)
    questions = preview.parse_questions(request.form.get("questions", ""))
    suggested = False
    if not questions:
        changed, removed = preview.changed_sections(config, business_id)
        if not changed and not removed:
            flash("Nothing has changed since you last published. Type a "
                  "question or two to try.", "error")
            return redirect(url_for("admin.knowledge", business_id=business_id))
        questions = preview.suggest_questions(config, changed, removed)
        suggested = True
        if not questions:
            flash("Couldn't suggest questions just now. Type one or two to "
                  "try.", "error")
            return redirect(url_for("admin.knowledge", business_id=business_id))

    try:
        results = preview.run_preview(config, business_id, questions)
    except Exception as e:
        log.exception("Preview failed for business %s", business_id)
        flash(f"Couldn't run the preview: {type(e).__name__}. Nothing was "
              f"changed.", "error")
        return redirect(url_for("admin.knowledge", business_id=business_id))

    return render_template(
        "admin/knowledge.html",
        business = business,
        sections = get_documents(business_id),
        changes = unpublished_changes(business),
        pending_upload = get_upload_proposal(business_id),
        price_lines = section_price_lines(business, get_documents(business_id)),
        notice_lines = section_notice_lines(business, get_documents(business_id)),
        menu_changes = menu_changes_waiting(business),
        example_title = example_section_title(business),
        preview_results = results,
        preview_questions = "\n".join(questions),
        preview_suggested = suggested,
    )


# ---------------------------------------------------------------------------
# The menu (owner-editable prices; see menu.py and menu_form.py)
# ---------------------------------------------------------------------------

def _menu_context(business):
    """(live config, the menu list being edited — the draft if there is one)."""
    import menu
    live = load_config(business["config_path"], business["id"])
    return live, copy.deepcopy(menu.with_draft(live, business["id"]).get("menu") or [])


def _render_menu(business, errors=None, editing=None, status=200):
    import menu
    import menu_form
    from db import get_documents
    live, menu_list = _menu_context(business)
    cards = []
    for i, entry in enumerate(menu_list):
        rows = (editing or {}).get(i) or menu_form.item_rows(entry)
        cards.append({"index": i, "original": entry.get("item", ""), "rows": rows,
                      "fingerprint": menu_form.fingerprint(entry),
                      "errors": (errors or {}).get(i, []),
                      "preview": menu.render_item(menu.items({"menu": [entry]})[0])})
    new_rows = (editing or {}).get("new") or menu_form.item_rows({})
    sections = []
    for d in get_documents(business["id"]):
        core = d["title"].split("(")[0].strip()
        if core and core not in sections:
            sections.append(core)
    return render_template(
        "admin/menu.html", business=business, cards=cards, new_rows=new_rows,
        new_errors=(errors or {}).get("new", []), sections=sections,
        waiting=(menu.differences(live, menu.with_draft(live, business["id"]))
                 if menu.draft_menu(business["id"]) is not None else []),
    ), status


@admin_bp.route("/business/<int:business_id>/menu")
@login_required
def menu_page(business_id):
    """Every menu item, each editable: names, prices, extras, notes."""
    require_business_access(business_id)
    business = get_business_by_id(business_id)
    if not business:
        abort(404)
    if (off := _menu_switched_off(business)):
        return off
    return _render_menu(business)


def _save_menu(business, new_list, message):
    import menu
    from admin.auth import current_user
    menu.save_draft(business["id"], new_list, updated_by=current_user()["email"])
    flash(message, "success")
    return redirect(url_for("admin.menu_page", business_id=business["id"]))


@admin_bp.route("/business/<int:business_id>/menu/<int:index>/save", methods=["POST"])
@login_required
def menu_item_save(business_id, index):
    import menu_form
    require_business_access(business_id)
    business = get_business_by_id(business_id)
    if not business:
        abort(404)
    if (off := _menu_switched_off(business)):
        return off
    _live, menu_list = _menu_context(business)
    others = [e.get("item", "") for j, e in enumerate(menu_list) if j != index]
    entry, errors = menu_form.parse_item_form(request.form, others)
    if errors:
        return _render_menu(business, errors={index: errors},
                            editing={index: menu_form.form_rows(request.form)}, status=400)
    new_list, stale = menu_form.replace_item(menu_list, index, entry,
                                             request.form.get("original", ""),
                                             request.form.get("fingerprint"))
    if stale:
        flash(stale, "error")
        return redirect(url_for("admin.menu_page", business_id=business_id))
    return _save_menu(business, new_list, f"Saved “{entry['item']}”. Publish to "
                      f"make it live.")


@admin_bp.route("/business/<int:business_id>/menu/add", methods=["POST"])
@login_required
def menu_item_add(business_id):
    import menu_form
    require_business_access(business_id)
    business = get_business_by_id(business_id)
    if not business:
        abort(404)
    if (off := _menu_switched_off(business)):
        return off
    _live, menu_list = _menu_context(business)
    entry, errors = menu_form.parse_item_form(
        request.form, [e.get("item", "") for e in menu_list])
    if errors:
        return _render_menu(business, errors={"new": errors},
                            editing={"new": menu_form.form_rows(request.form)}, status=400)
    return _save_menu(business, menu_list + [entry],
                      f"Added “{entry['item']}”. Publish to make it live.")


@admin_bp.route("/business/<int:business_id>/menu/<int:index>/delete", methods=["POST"])
@login_required
def menu_item_delete(business_id, index):
    import menu_form
    require_business_access(business_id)
    business = get_business_by_id(business_id)
    if not business:
        abort(404)
    if (off := _menu_switched_off(business)):
        return off
    _live, menu_list = _menu_context(business)
    new_list, stale = menu_form.replace_item(menu_list, index, None,
                                             request.form.get("original", ""),
                                             request.form.get("fingerprint"))
    if stale:
        flash(stale, "error")
        return redirect(url_for("admin.menu_page", business_id=business_id))
    return _save_menu(business, new_list, f"Removed “{request.form.get('original')}” "
                      f"from the menu. Publish to make it live.")


@admin_bp.route("/business/<int:business_id>/menu/discard", methods=["POST"])
@login_required
def menu_discard(business_id):
    """Drop the menu's unpublished changes (sections are left alone)."""
    import menu
    from db import unpublished_changes, set_documents_clean
    require_business_access(business_id)
    business = get_business_by_id(business_id)
    if not business:
        abort(404)
    menu.discard_draft(business_id)
    # If the menu was the only thing unpublished, the business is clean again.
    changes = unpublished_changes(get_business_by_id(business_id))
    if not (changes["edited"] or changes["new"] or changes["deleted"]):
        set_documents_clean(business_id)
    flash("Menu changes discarded — customers see the published menu.", "success")
    return redirect(url_for("admin.menu_page", business_id=business_id))


@admin_bp.route("/business/<int:business_id>/knowledge/upload", methods=["POST"])
@login_required
def knowledge_upload(business_id):
    """Read an owner's photo or PDF of a menu or price list and propose the
    section changes it implies. Nothing is saved to the knowledge base here:
    the proposals wait on the review page for the owner to accept or skip."""
    require_business_access(business_id)
    business = get_business_by_id(business_id)
    if not business:
        abort(404)

    import ratelimit
    import photo_import
    from db import get_documents, save_upload_proposal
    from admin.auth import current_user

    back = redirect(url_for("admin.knowledge", business_id=business_id) + "#photo")
    upload = request.files.get("photo")
    data = upload.read(photo_import.MAX_UPLOAD_BYTES + 1) if upload else b""
    try:
        media_type = photo_import.check_upload(data)
    except photo_import.UploadProblem as e:
        flash(str(e), "error")
        return back

    allowed, wait = ratelimit.knowledge_upload(business_id)
    if not allowed:
        flash(f"Uploads are limited to a few a minute. Try again in "
              f"{max(wait, 1)} seconds.", "error")
        return back
    note = budget.refusal()
    if note:
        flash(note, "error")
        return back

    config = load_config(business["config_path"], business_id)
    try:
        result = photo_import.build_proposal(config, data, media_type,
                                             get_documents(business_id))
    except photo_import.UploadProblem as e:
        flash(str(e), "error")
        return back
    except Exception as e:
        log.exception("Upload import failed for business %s", business_id)
        what = "PDF" if media_type == photo_import.PDF else "photo"
        flash(f"Couldn't read that {what} just now: {type(e).__name__}. "
              f"Nothing was changed.", "error")
        return back

    save_upload_proposal(business_id, result["transcription"], result["changes"],
                         created_by=current_user()["email"], source=result["source"],
                         hidden=result.get("hidden") or [])
    return redirect(url_for("admin.knowledge_upload_review", business_id=business_id))


@admin_bp.route("/business/<int:business_id>/knowledge/upload/review")
@login_required
def knowledge_upload_review(business_id):
    """The changes proposed from a photo, each with its old and new wording
    side by side, for the owner to accept, edit or skip."""
    require_business_access(business_id)
    business = get_business_by_id(business_id)
    if not business:
        abort(404)

    import photo_import
    from db import get_upload_proposal
    proposal = get_upload_proposal(business_id)
    if proposal is None:
        flash("There are no changes waiting. Upload a photo or PDF to start.", "error")
        return redirect(url_for("admin.knowledge", business_id=business_id))
    import menu
    for c in proposal["changes"]:
        if c.get("kind") == "menu":
            c["price_text"] = menu.money(c["price"])
            c["old_price_text"] = (menu.money(c["old_price"])
                                   if c.get("old_price") is not None else "")
            continue
        c["old_segments"], c["new_segments"] = photo_import.word_diff(
            c["old_body"], c["body"])
    return render_template("admin/knowledge_upload.html",
                           business=business, proposal=proposal,
                           word=photo_import.source_word(proposal["source"]))


@admin_bp.route("/business/<int:business_id>/knowledge/upload/apply", methods=["POST"])
@login_required
def knowledge_upload_apply(business_id):
    """Save the changes the owner ticked, as ordinary edits (History keeps
    the old wording; Discard and Publish work as usual)."""
    require_business_access(business_id)
    from db import get_upload_proposal, delete_upload_proposal
    from admin.auth import current_user

    proposal = get_upload_proposal(business_id)
    if proposal is None:
        flash("Those changes are no longer waiting.", "error")
        return redirect(url_for("admin.knowledge", business_id=business_id))

    import photo_import
    choices, prices = {}, {}
    for i in range(len(proposal["changes"])):
        if request.form.get(f"apply_{i}") == "yes":
            choices[i] = (clean_section_text(request.form.get(f"title_{i}")),
                          clean_section_text(request.form.get(f"body_{i}")))
            if request.form.get(f"price_{i}") is not None:
                prices[i] = request.form.get(f"price_{i}")
    # A menu price the owner corrected on the review page, read the way the
    # Menu page reads one. A typo saves nothing: back to the page to fix it.
    changes, price_errors = photo_import.edited_menu_prices(proposal["changes"], prices)
    if price_errors:
        flash("Nothing was saved. Fix " + ("this price" if len(price_errors) == 1
              else "these prices") + ": " + "; ".join(price_errors) + ".", "error")
        return redirect(url_for("admin.knowledge_upload_review", business_id=business_id))
    business = get_business_by_id(business_id)
    config = load_config(business["config_path"], business_id) if business else None
    word = photo_import.source_word(proposal["source"])
    applied, skipped_stale = photo_import.apply_changes(
        business_id, changes, choices,
        updated_by=f"{current_user()['email']} (from a {word})", config=config)

    delete_upload_proposal(business_id)
    if skipped_stale:
        flash(f"Not applied, because the section changed after the {word} was "
              "read: " + ", ".join(skipped_stale) + ".", "error")
    if applied:
        flash(f"{applied} change{'' if applied == 1 else 's'} from your {word} "
              f"saved. Preview the answers, then publish.", "success")
    elif not skipped_stale:
        flash("No changes were applied.", "success")
    return redirect(url_for("admin.knowledge", business_id=business_id))


@admin_bp.route("/business/<int:business_id>/knowledge/upload/url", methods=["POST"])
@login_required
def knowledge_upload_url(business_id):
    """Read the business's own web page (its menu, say) and propose the
    changes it implies, through the same review page as a photo or PDF.
    web_import does the fetching, and every check that keeps a typed URL
    from reaching anything but a public web site (SSRF)."""
    require_business_access(business_id)
    business = get_business_by_id(business_id)
    if not business:
        abort(404)
    import ratelimit
    import photo_import
    import web_import
    from db import get_documents, save_upload_proposal
    from admin.auth import current_user

    back = redirect(url_for("admin.knowledge", business_id=business_id) + "#photo")
    url = (request.form.get("url") or "").strip()
    try:
        web_import.check_url(url)          # the cheap checks first, before the rate limit
    except web_import.WebProblem as e:
        flash(str(e), "error")
        return back
    allowed, wait = ratelimit.knowledge_upload(business_id)
    if not allowed:
        flash(f"Uploads are limited to a few a minute. Try again in {max(wait, 1)} seconds.",
              "error")
        return back
    note = budget.refusal()
    if note:
        flash(note, "error")
        return back
    config = load_config(business["config_path"], business_id)
    try:
        text, how, hidden, final_url = web_import.read_page(config, url)
        if not text:
            raise web_import.WebProblem("Couldn't find any text on that page.")
        word = photo_import.source_word(how)
        changes = photo_import.suggest(config, text, get_documents(business_id), word)
    except (web_import.WebProblem, photo_import.UploadProblem) as e:
        flash(str(e), "error")
        return back
    except Exception as e:
        log.exception("Web import failed for business %s", business_id)
        flash(f"Couldn't read that page just now: {type(e).__name__}. Nothing was changed.",
              "error")
        return back
    save_upload_proposal(business_id, text, changes, created_by=current_user()["email"],
                         source=how, hidden=hidden, origin=final_url)
    return redirect(url_for("admin.knowledge_upload_review", business_id=business_id))


@admin_bp.route("/business/<int:business_id>/knowledge/upload/resuggest", methods=["POST"])
@login_required
def knowledge_upload_resuggest(business_id):
    """New suggestions from the text as the owner corrected it (a scan that
    read $25 as $26, say): the suggestion step again, without reading the
    file again. Fixing the text once fixes every suggestion built from it."""
    require_business_access(business_id)
    business = get_business_by_id(business_id)
    if not business:
        abort(404)
    import ratelimit
    import photo_import
    from db import get_documents, get_upload_proposal, save_upload_proposal
    from admin.auth import current_user

    review = redirect(url_for("admin.knowledge_upload_review", business_id=business_id))
    proposal = get_upload_proposal(business_id)
    if proposal is None:
        flash("Those changes are no longer waiting. Upload the file again.", "error")
        return redirect(url_for("admin.knowledge", business_id=business_id))
    text = clean_section_text(request.form.get("transcription"))
    if not text:
        flash("The text is empty, so there's nothing to suggest from.", "error")
        return review
    if len(text) > photo_import.MAX_TRANSCRIPTION:
        flash(f"That's too long: keep it under {photo_import.MAX_TRANSCRIPTION} characters.", "error")
        return review
    if text == proposal["transcription"]:
        flash("The text hasn't changed, so the suggestions are the same.", "info")
        return review
    allowed, wait = ratelimit.knowledge_upload(business_id)
    if not allowed:
        flash(f"That's limited to a few a minute. Try again in {max(wait, 1)} seconds.", "error")
        return review
    note = budget.refusal()
    if note:
        flash(note, "error")
        return review
    config = load_config(business["config_path"], business_id)
    word = photo_import.source_word(proposal["source"])
    try:
        changes = photo_import.suggest(config, text, get_documents(business_id), word)
    except Exception as e:
        log.exception("Re-suggesting from corrected text failed for business %s", business_id)
        flash(f"Couldn't make new suggestions just now: {type(e).__name__}. The earlier "
              f"ones are still here.", "error")
        return review
    save_upload_proposal(business_id, text, changes, created_by=current_user()["email"],
                         source=proposal["source"], hidden=proposal["hidden"],
                         origin=proposal.get("origin"))
    flash("New suggestions from your corrected text. Check them before saving.", "success")
    return review


@admin_bp.route("/business/<int:business_id>/knowledge/upload/cancel", methods=["POST"])
@login_required
def knowledge_upload_cancel(business_id):
    require_business_access(business_id)
    from db import delete_upload_proposal
    delete_upload_proposal(business_id)
    flash("Proposed changes discarded. Nothing was changed.", "success")
    return redirect(url_for("admin.knowledge", business_id=business_id))


@admin_bp.errorhandler(413)
def upload_too_large(e):
    """A request over MAX_CONTENT_LENGTH: in practice, a photo the browser
    didn't shrink."""
    flash("That file is too large. Upload a photo or PDF under 5MB.", "error")
    # Not request.referrer: a redirect target the request supplies is an
    # open redirect.
    business_id = (request.view_args or {}).get("business_id")
    if business_id:
        return redirect(url_for("admin.knowledge", business_id=business_id))
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/business/<int:business_id>/cancellation", methods=["GET", "POST"])
@login_required
def cancellation_rules(business_id):
    """The owner's cancellation rules as a form (policy_form). Saving makes
    a draft: customers keep hearing the published rules until Publish."""
    require_business_access(business_id)
    business = get_business_by_id(business_id)
    if not business:
        abort(404)
    import policy
    import policy_form
    from db import get_documents
    from admin.auth import current_user

    live_config = load_config(business["config_path"], business_id)
    live = policy.cancellation(live_config)
    pending = policy_form.draft(business_id)
    removed = bool(pending and pending.get("removed"))
    current = None if removed else (pending or live)
    errors, posted = [], {}

    if request.method == "POST":
        if request.form.get("action") == "remove":
            if live or pending:
                policy_form.save_draft(business_id, None, updated_by=current_user()["email"])
                flash("Removal saved as a draft. Customers keep the current rules until you "
                      "press Publish changes.", "success")
            return redirect(url_for("admin.cancellation_rules", business_id=business_id))
        base = current or policy_form.new_policy(live_config)
        new, errors = policy_form.parse(request.form, base,
                                        policy_form.service_choices(live_config, base))
        if not errors:
            if live and policy.policy_text({"policies": {"cancellation": new}}) == \
                    policy.policy_text(live_config):
                policy_form.discard_draft(business_id)
                flash("That's what customers are told now, so there's nothing to publish.",
                      "info")
            else:
                policy_form.save_draft(business_id, new, updated_by=current_user()["email"])
                flash("Saved as a draft. Customers keep the current rules until you press "
                      "Publish changes; Preview answers on the Knowledge base page shows how "
                      "the assistant will answer with the new ones.", "success")
            return redirect(url_for("admin.cancellation_rules", business_id=business_id))
        posted = request.form
        current = base

    editing = current or (policy_form.new_policy(live_config)
                          if request.args.get("new") or request.method == "POST" else None)
    after = policy_form.with_draft(live_config, business_id) if pending else None
    return render_template(
        "admin/cancellation.html", business=business, errors=errors, posted=posted,
        rows=(policy_form.rows(editing, policy_form.service_choices(live_config, editing))
              if editing else None),
        outcomes=[(k, policy_form.OUTCOMES[k]) for k in policy_form.outcomes_for(editing or {})],
        with_percent=" ".join(sorted(policy_form.WITH_PERCENT)),
        max_windows=policy_form.MAX_WINDOWS, is_new=not live and not pending,
        now_lines=policy.policy_text(live_config).splitlines()[1:] if live else [],
        after_lines=(policy.policy_text(after).splitlines()[1:] if after and not removed else []),
        removed=removed, has_draft=pending is not None,
        overlapping=policy_form.sections_about_cancelling(get_documents(business_id)),
        price_word=(editing or {}).get("price", "the price"))


@admin_bp.route("/business/<int:business_id>/notice", methods=["GET", "POST"])
@login_required
def booking_notice(business_id):
    """How much notice each service needs (notice_form). Saving makes a
    draft: customers and the booking flow keep the published notice until
    Publish, which also rebuilds the knowledge base's generated lines."""
    require_business_access(business_id)
    business = get_business_by_id(business_id)
    if not business:
        abort(404)
    import notice_form
    from db import get_documents
    from admin.auth import current_user

    live_config = load_config(business["config_path"], business_id)
    editing = notice_form.with_draft(live_config, business_id)
    errors, posted = [], {}

    if request.method == "POST":
        new, errors = notice_form.parse(request.form, editing)
        if not errors:
            if notice_form.describe({"booking": {"notice": new}}) == \
                    notice_form.describe(live_config):
                notice_form.discard_draft(business_id)
                flash("That's what customers are told now, so there's nothing to publish.",
                      "info")
            else:
                notice_form.save_draft(business_id, new, updated_by=current_user()["email"])
                flash("Saved as a draft. Bookings and answers keep the current notice until "
                      "you press Publish changes; Preview answers on the Knowledge base page "
                      "shows how the assistant will answer with the new one.", "success")
            return redirect(url_for("admin.booking_notice", business_id=business_id))
        posted = request.form

    has_draft = notice_form.draft(business_id) is not None
    sections = get_documents(business_id)
    return render_template(
        "admin/notice.html", business=business, errors=errors, posted=posted,
        rows=notice_form.rows(editing), days=notice_form.DAYS,
        section_titles=[s["title"] for s in sections],
        now_lines=notice_form.describe(live_config),
        after_lines=notice_form.describe(editing) if has_draft else [],
        has_draft=has_draft,
        overlapping=notice_form.sections_stating_notice(sections))


@admin_bp.route("/business/<int:business_id>/knowledge/discard", methods=["POST"])
@login_required
def knowledge_discard(business_id):
    """Put the sections back as last published. The search index already
    holds that version, so nothing is rebuilt."""
    require_business_access(business_id)
    if not get_business_by_id(business_id):
        abort(404)

    from db import discard_document_changes, unpublished_changes, set_documents_clean
    from admin.auth import current_user

    import menu
    import policy_form
    import notice_form
    had = [name for name, waiting in (("menu", menu.draft_menu(business_id)),
                                      ("cancellation-rule", policy_form.draft(business_id)),
                                      ("booking-notice", notice_form.draft(business_id)))
           if waiting is not None]
    menu.discard_draft(business_id)
    policy_form.discard_draft(business_id)
    notice_form.discard_draft(business_id)
    if discard_document_changes(business_id, updated_by=current_user()["email"]):
        flash("Changes discarded — the sections, menu, cancellation rules and booking "
              "notice are back to what customers see. Anything you'd written is still "
              "in each section's History.", "success")
    elif had:
        changes = unpublished_changes(get_business_by_id(business_id))
        if not (changes["edited"] or changes["new"] or changes["deleted"]):
            set_documents_clean(business_id)
        flash((" and ".join(had).capitalize()) + " changes discarded.", "success")
    else:
        flash("There's no published version saved to go back to yet. Publish "
              "once, and Discard will work from then on.", "error")
    return _back_to(business_id)


@admin_bp.route("/business/<int:business_id>/knowledge/add", methods=["POST"])
@login_required
def knowledge_add(business_id):
    require_business_access(business_id)

    from db import add_document_section
    from admin.auth import current_user

    title = clean_section_text(request.form.get("title"))
    body  = clean_section_text(request.form.get("body"))

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
    from db import get_connection, update_document_section, same_text
    from admin.auth import current_user

    # Derive the business from the section rather than trusting the URL.
    conn = get_connection()
    row  = conn.execute("SELECT business_id, title, body, updated_at FROM documents "
                        "WHERE id = ?", (document_id,)).fetchone()
    current = row
    conn.close()
    if not row:
        abort(404)
    require_business_access(row["business_id"])

    title = clean_section_text(request.form.get("title"))
    body  = clean_section_text(request.form.get("body"))

    # The section changed after this page was loaded: the owner assistant
    # edited it (its pop-out leaves a page with unsaved typing alone), or
    # another tab saved it. Saving would quietly undo that, so nothing is
    # saved and the page shows both versions (2026-10-15).
    loaded_at = request.form.get("loaded_at")
    if (loaded_at is not None and loaded_at != (current["updated_at"] or "")
            and title and body
            and not (same_text(title, current["title"]) and same_text(body, current["body"]))):
        business = get_business_by_id(row["business_id"])
        return _knowledge_page(business, conflict={"id": document_id, "title": title,
                                                   "body": body}, status=409)

    if not title or not body:
        flash("A section needs both a title and content.", "error")
    elif same_text(title, current["title"]) and same_text(body, current["body"]):
        # Saving untouched text would still file a version and mark the
        # knowledge unpublished, sending the owner to Publish for nothing.
        flash("Nothing changed in that section.", "info")
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

    import menu
    try:
        # A menu change from a photo is a draft until now: build with it,
        # and make it the live menu only once the rebuild has succeeded.
        # The booking notice's generated lines are in each section's chunk
        # too, so a notice draft is built in the same way.
        import notice_form
        config = notice_form.with_draft(menu.with_draft(
            load_config(business["config_path"], business_id), business_id), business_id)
        ingest_documents(config, business_id=business_id)
        menu.publish_draft(business_id)
        notice_form.publish_draft(business_id)
        # Cancellation rules aren't in the index (the tool reads them from
        # config), so they only need promoting, once the rest has worked.
        import policy_form
        policy_form.publish_draft(business_id)
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

    return _back_to(business_id)


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

    Drawn from the appointments table, not from Google: it has everything
    needed and is already scoped per business. Changes the owner makes to
    the bot's events in Google are pulled in first by maybe_reconcile (one
    cheap sync-token call, throttled). The gap is that events the owner
    creates directly in their calendar
    (a dentist appointment, say) block availability but never appear here,
    because reconciliation only updates appointments the bot created.
    """
    require_business_access(business_id)

    business = get_business_by_id(business_id)
    if not business:
        abort(404)

    # Pull moves made in Google Calendar first (throttled, so paging through
    # weeks doesn't call Google each time). Until 2026-09-28 only the
    # appointments list did this, and a move made in Google showed here at
    # the old time.
    from reconcile import maybe_reconcile
    maybe_reconcile(business, min_interval=15)

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

    import clock
    today       = clock.business_today(config)
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

    # Time the owner blocked off: shaded on the grid, listed under it.
    import booking_control
    from db import list_time_blocks
    now_str = clock.business_now(config).strftime("%Y-%m-%d %H:%M")
    upcoming = list_time_blocks(business_id, ending_after=now_str)
    grid_hours = list(range(grid_start, grid_end + 1))

    return render_template(
        "admin/calendar.html",
        business   = business,
        days       = days,
        hours      = grid_hours,
        slots      = slots,
        open_days  = open_days,
        offset     = offset,
        today      = today,
        highlight  = highlight,
        blocked    = booking_control.cells(upcoming, days, grid_hours),
        blocks     = [{**b, "label": booking_control.describe(b)} for b in upcoming],
        block_form = request.args,      # what was typed, after a refused block
        holiday_choices = _holiday_choices(config, today),
    )


def _holiday_choices(config, today):
    """The Calendar page's "Holidays we close" list: each holiday, its next
    date, and whether this business has it ticked (holidays.py)."""
    import holidays
    ticked = set(holidays.ticked(config))
    upcoming = holidays.next_dates(today)
    return [{"key": key, "label": label[:1].upper() + label[1:],
             "next": f"{upcoming[key]:%a, %b %-d}", "ticked": key in ticked}
            for key, (label, _) in holidays.HOLIDAYS.items()]


@admin_bp.route("/business/<int:business_id>/calendar/holidays", methods=["POST"])
@login_required
def calendar_holidays(business_id):
    """Save the holidays this business closes for. Live at once, like a
    block: the assistant treats them as closed from the next message."""
    require_business_access(business_id)
    business = get_business_by_id(business_id)
    if not business:
        abort(404)
    import holidays
    from db import set_config_override
    keys = [k for k in holidays.HOLIDAYS if k in request.form.getlist("holiday")]
    set_config_override(business_id, "holidays", keys,
                        updated_by=current_user()["email"])
    flash("Saved. The assistant treats those days as closed every year."
          if keys else "Saved. No holidays are marked as closed.", "success")
    return redirect(url_for("admin.calendar_view", business_id=business_id) + "#holidays")


@admin_bp.route("/business/<int:business_id>/calendar/block", methods=["POST"])
@login_required
def calendar_block_add(business_id):
    """Block off time: a whole day, several days, or specific hours."""
    require_business_access(business_id)
    business = get_business_by_id(business_id)
    if not business:
        abort(404)
    import clock
    import booking_control
    from datetime import datetime, timedelta
    from config import load_config
    from db import add_time_block, get_appointments
    from admin.auth import current_user
    config = load_config(business["config_path"], business_id)
    block, errors = booking_control.parse_block_form(request.form,
                                                     clock.business_today(config))
    if errors:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("admin.calendar_view", business_id=business_id,
                                **{k: v for k, v in request.form.items()
                                   if k in ("from_date", "until_date", "from_time",
                                            "until_time", "all_day", "note")}) + "#block")
    starts_at, ends_at, all_day, note = block
    add_time_block(business_id, starts_at, ends_at, all_day=all_day, note=note,
                   created_by=current_user()["email"])
    saved = {"starts_at": starts_at, "ends_at": ends_at, "all_day": all_day}
    flash(f"Blocked {booking_control.describe(saved)}. The assistant won't offer that time.",
          "success")
    # Not cancelled, but the owner should know.
    clashes = booking_control.appointments_in(saved, get_appointments(business_id), config)
    if clashes:
        who = "; ".join(f"{a.get('customer_name') or a.get('phone')}, {a['datetime']}"
                        for a in clashes[:5])
        flash(f"Heads up: {len(clashes)} booked appointment{'s' if len(clashes) > 1 else ''} "
              f"fall{'s' if len(clashes) == 1 else ''} in that time ({who}). They're not "
              f"cancelled; move or cancel them from Appointments if you need to.", "warning")
    week = 0
    try:
        start_day = datetime.strptime(starts_at, "%Y-%m-%d %H:%M").date()
        today = clock.business_today(config)
        week = ((start_day - timedelta(days=start_day.weekday()))
                - (today - timedelta(days=today.weekday()))).days // 7
    except Exception:
        pass
    return redirect(url_for("admin.calendar_view", business_id=business_id, week=week))


@admin_bp.route("/business/<int:business_id>/calendar/block/<int:block_id>/remove",
                methods=["POST"])
@login_required
def calendar_block_remove(business_id, block_id):
    require_business_access(business_id)
    from db import delete_time_block
    if delete_time_block(business_id, block_id):
        flash("Unblocked. The assistant can offer that time again.", "success")
    return redirect(url_for("admin.calendar_view", business_id=business_id) + "#block")

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

    # A move made in Google Calendar should show here, so reconcile, then
    # read the appointment again.
    if business:
        from reconcile import maybe_reconcile
        if maybe_reconcile(business, min_interval=15):
            appt = get_appointment(appointment_id)

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
        notices  = notifications_for_appointment(appointment_id),
    )
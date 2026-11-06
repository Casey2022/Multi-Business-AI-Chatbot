# admin/usage_routes.py — the operator's AI spend page: each business's model
# spend this month against its budget, and the form that sets the budget
# (budget.py, usage.py; notes/spend_caps.md). Added 2026-11-04.

import logging

from flask import abort, flash, redirect, render_template, request, url_for

from admin import admin_bp
from admin.auth import current_user, operator_required
from db import get_business_by_id

log = logging.getLogger("admin")


@admin_bp.route("/usage")
@operator_required
def usage_page():
    """This month's spend, budget and share for each business; demos summed."""
    import budget
    from db import get_all_businesses, get_config_overrides
    rows, demos = [], {"count": 0, "spent": 0.0}
    for b in get_all_businesses(include_demo=True):
        try:
            state = budget.month_state(b["id"])
        except Exception as e:
            log.warning("Couldn't read business %s's AI use (%s)", b["id"], e)
            continue
        if b.get("is_demo"):
            demos["count"] += 1
            demos["spent"] += state["spent"]
            continue
        chosen = get_config_overrides(b["id"]).get("budget") or {}
        rows.append({"business": b, "state": state, "chosen": chosen})
    return render_template("admin/usage.html", rows=rows, demos=demos,
                           default=budget.DEFAULT_MONTHLY)


@admin_bp.route("/usage/<int:business_id>/budget", methods=["POST"])
@operator_required
def usage_budget(business_id):
    """Set (or, left blank, clear) a business's monthly budget and alert."""
    from db import clear_config_override, set_config_override
    business = get_business_by_id(business_id)
    if not business:
        abort(404)
    values = {}
    for field in ("monthly_usd", "alert_at"):
        raw = (request.form.get(field) or "").strip().lstrip("$")
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            value = None
        if value is None or not 0 < value <= 100_000:
            flash(f"{business['name']}: '{raw}' isn't an amount in dollars.", "error")
            return redirect(url_for("admin.usage_page"))
        values[field] = round(value, 2)
    if "monthly_usd" in values and values.get("alert_at", 0) > values["monthly_usd"]:
        flash(f"{business['name']}: the alert can't be above the limit.", "error")
        return redirect(url_for("admin.usage_page"))
    if values:
        set_config_override(business_id, "budget", values,
                            updated_by=current_user()["email"])
    else:
        clear_config_override(business_id, "budget")
    flash(f"{business['name']}: budget saved.", "success")
    return redirect(url_for("admin.usage_page"))

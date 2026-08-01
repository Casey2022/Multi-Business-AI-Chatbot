# admin/__init__.py — Flask blueprint definition for the admin section.
#
# This file tells Flask "everything in this folder is a blueprint called
# 'admin' that lives at the /admin URL prefix." Routes defined in auth.py
# and routes.py are registered here via import.


from flask import Blueprint
from datetime import datetime

admin_bp = Blueprint(
    "admin",
    __name__,
    template_folder="templates",
    url_prefix="/admin"
)


@admin_bp.app_template_filter("friendly_time")
def friendly_time(iso_string):
    """Convert an ISO timestamp into a readable format.

    '2026-07-10T19:39:12.345678'  ->  'Jul 10, 2026 · 7:39 PM'

    Returns the input unchanged if it can't be parsed — never crash
    a page over a formatting issue.
    """
    if not iso_string:
        return ""
    try:
        dt = datetime.fromisoformat(iso_string)
        # %-I gives hour without leading zero on Mac/Linux ("7:39" not "07:39")
        return dt.strftime("%b %-d, %Y · %-I:%M %p")
    except (ValueError, TypeError):
        return iso_string


# Import routes and auth AFTER creating the blueprint.
from admin import auth, routes  # noqa: E402, F401
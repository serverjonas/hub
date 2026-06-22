from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
)
import sqlite3
from toolbox.files import DB_PATH
from toolbox.user import (
    get_current_user,
    get_user_profile,
)

bp = Blueprint("settings", __name__)


def _pop_profile_flashes():
    """Liest Profile-Flashes aus der Flask-Session (falls verfügbar)."""
    try:
        flashes = session.pop("_profile_flashes", [])
        return flashes if isinstance(flashes, list) else []
    except RuntimeError:
        return []


@bp.route("/", methods=["GET"])
def settings():
    user_data = get_current_user()
    user = user_data["name"] if user_data else None
    profile = None
    if user_data is not None:
        profile = get_user_profile(user_data["id"])

    flashes = _pop_profile_flashes()
    return render_template(
        "settings.html",
        user=user,
        user_id=(user_data or {}).get("id"),
        current_avatar_path=(profile or {}).get("avatar_path"),
        current_bio=(profile or {}).get("bio", ""),
        current_visibility=(profile or {}).get("profile_visibility", "public"),
        flashes=flashes,
    )


@bp.route("/logout-all", methods=["POST"])
def logout_all():
    user_array = get_current_user()
    if user_array is None:
        return redirect("/settings")
    target_id = user_array["id"]

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("DELETE FROM sessions WHERE user_id = ?", (target_id,))
    con.commit()
    con.close()

    try:
        flash("Alle Sitzungen wurden abgemeldet.")
    except RuntimeError:
        pass
    return redirect("/login")

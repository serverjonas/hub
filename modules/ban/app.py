from flask import Blueprint, redirect, render_template, request

from toolbox.user import get_current_user, is_banned

bp = Blueprint("ban", __name__, template_folder="templates")

@bp.route("/")
def ban_page():
    user = get_current_user()
    if user is None:
        return redirect("/hub")

    banned, reason = is_banned(user["id"])

    if not banned:
        return redirect("/hub")

    return render_template("ban.html", reason=reason if reason else "Kein Grund angegeben"), 403

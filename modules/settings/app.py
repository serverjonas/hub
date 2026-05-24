from flask import render_template, Blueprint, request, redirect
import sqlite3
from toolbox.toolbox import get_current_user, DB_PATH

bp = Blueprint("settings", __name__)

@bp.route("/")
def settings():
    user_data = get_current_user()
    return render_template("settings.html", user=user_data["name"] if user_data else None)

@bp.route("/logout-all", methods=["POST"])
def logout_all():
    user_array = get_current_user()
    target_id = user_array["id"]

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("DELETE FROM sessions WHERE user_id = ?", (target_id,))
    con.commit()
    con.close()
    return redirect("/settings")
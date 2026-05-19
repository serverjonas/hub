from flask import Blueprint, render_template, request, redirect, abort
import sqlite3
import time
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from toolbox.toolbox import DB_PATH
from toolbox.user import get_current_user, get_infos

bp = Blueprint("usermanager", __name__, template_folder="templates")

def get_active_bans():
    now = int(time.time())
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        SELECT COUNT(*)
        FROM ban
        WHERE expires_at IS NULL OR expires_at > ?
    """, (now,))
    count = cur.fetchone()[0]
    con.close()
    return count

@bp.before_request
def require_admin():
    user = get_current_user()
    if user is None:
        abort(401)
    infos = get_infos(user["id"])
    if infos is None or not infos["admin"]:
        abort(403)


def get_all_users():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    now = int(time.time())

    cur.execute("""
        SELECT
            u.user_id,
            u.user_name,
            u.admin,
            u.vip,
            u.mod,
            COUNT(CASE WHEN s.expires_at > ? THEN 1 END) AS active_sessions
        FROM users u
        LEFT JOIN sessions s ON u.user_id = s.user_id
        GROUP BY u.user_id
        ORDER BY u.user_id ASC
    """, (now,))

    rows = cur.fetchall()
    con.close()

    users = []
    for row in rows:
        uid, uname, is_admin, is_vip, is_mod, active_sessions = row
        users.append({
            "id":              uid,
            "name":            uname,
            "admin":           bool(is_admin),
            "vip":             bool(is_vip),
            "mod":             bool(is_mod),
            "active_sessions": active_sessions or 0,
        })
    return users


@bp.route("/")
def user_index():
    user = get_current_user()
    users = get_all_users()
    bans = get_active_bans()
    return render_template("admin_users_manager.html", users=users, user=user["name"], bans=bans, user_count=len(users))


@bp.route("/set-role", methods=["POST"])
def set_role():
    target_id = request.form.get("user_id", type=int)
    role      = request.form.get("role")
    value     = 1 if request.form.get("value") == "1" else 0

    if role not in ("admin", "vip", "mod"):
        abort(400)

    # Schutz: eigene Admin-Rolle nicht entziehen
    current_user = get_current_user()
    if target_id == current_user["id"] and role == "admin" and value == 0:
        return redirect("/admin/users")

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(f"UPDATE users SET {role} = ? WHERE user_id = ?", (value, target_id))
    con.commit()
    con.close()
    return redirect("/admin/users")


@bp.route("/delete-sessions", methods=["POST"])
def delete_sessions():
    target_id = request.form.get("user_id", type=int)

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("DELETE FROM sessions WHERE user_id = ?", (target_id,))
    con.commit()
    con.close()
    return redirect("/admin/users")

from flask import Blueprint, render_template, request, redirect, abort, jsonify
import sqlite3
import time
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from toolbox import get_current_user, get_infos

DB_PATH = "/var/www/serverjonas-hub/users.db"

bp = Blueprint("adminban", __name__, template_folder="templates")


@bp.before_request
def require_admin():
    user = get_current_user()
    if user is None:
        abort(401)
    infos = get_infos(user["id"])
    if infos is None or not infos["admin"]:
        abort(403)


@bp.route("/")
def admin_index():
    user = get_current_user()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Alle User holen
    cur.execute("""
        SELECT u.user_id, u.user_name, u.admin, u.vip, u.mod,
               b.reason, b.expires_at
        FROM users u
        LEFT JOIN ban b ON u.user_id = b.user_id
    """)
    rows = cur.fetchall()
    con.close()

    users = []
    now = int(time.time())
    for row in rows:
        uid, uname, is_admin, is_vip, is_mod, ban_reason, ban_expires = row
        banned = ban_reason is not None and (ban_expires is None or ban_expires > now)
        users.append({
            "id":          uid,
            "name":        uname,
            "admin":       bool(is_admin),
            "vip":         bool(is_vip),
            "mod":         bool(is_mod),
            "banned":      banned,
            "ban_reason":  ban_reason,
            "ban_expires": ban_expires
        })

    return render_template("adminban.html", users=users, user=user["name"])


@bp.route("/ban", methods=["POST"])
def ban_user():
    target_id  = request.form.get("user_id", type=int)
    reason     = request.form.get("reason", "Kein Grund angegeben")
    permanent  = request.form.get("permanent") == "1"
    expires_at = None

    if not permanent:
        days = request.form.get("days", type=int)
        if days and days > 0:
            expires_at = int(time.time()) + days * 86400

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    # Alten Ban entfernen falls vorhanden, dann neu setzen
    cur.execute("DELETE FROM ban WHERE user_id = ?", (target_id,))
    cur.execute(
        "INSERT INTO ban (user_id, reason, expires_at) VALUES (?, ?, ?)",
        (target_id, reason, expires_at)
    )
    con.commit()
    con.close()

    return redirect("/admin/ban")


@bp.route("/unban", methods=["POST"])
def unban_user():
    target_id = request.form.get("user_id", type=int)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("DELETE FROM ban WHERE user_id = ?", (target_id,))
    con.commit()
    con.close()
    return redirect("/admin/ban")

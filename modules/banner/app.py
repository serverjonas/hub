import os
import sqlite3
import sys
import time
from datetime import datetime

from flask import Blueprint, abort, redirect, render_template, request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
import os

from toolbox.user import (
    DB_PATH,
    get_current_user,
    get_infos,
    has_active_cooldown,
    get_cooldown_remaining,
    record_mod_cooldown,
    require_admin_or_mod,
)

bp = Blueprint("adminban", __name__, template_folder="templates")


def get_active_bans_count():
    now = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*)
        FROM ban
        WHERE expires_at IS NULL OR expires_at > ?
        """,
        (now,),
    )
    count = cur.fetchone()[0]
    conn.close()
    return count


@bp.before_request
def _role_check():
    """Admin oder Mod – weitere Checks pro Endpoint."""
    require_admin_or_mod()


@bp.route("/")
def admin_index():
    user = get_current_user()
    infos = get_infos(user["id"]) or {}
    now = int(time.time())
    cooldown_remaining = get_cooldown_remaining(user["id"]) if infos.get("mod") else 0

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """
        SELECT u.user_id, u.user_name, u.admin, u.vip, u.mod,
               b.reason, b.expires_at, b.banned_by,
               bv.user_name
        FROM users u
        LEFT JOIN ban b  ON u.user_id = b.user_id
        LEFT JOIN users bv ON bv.user_id = b.banned_by
        ORDER BY u.user_id ASC
        """
    )
    rows = cur.fetchall()
    con.close()

    users = []
    for row in rows:
        uid, uname, is_admin_u, is_vip_u, is_mod_u, ban_reason, ban_expires, banned_by, banned_by_name = row
        banned = ban_reason is not None and (ban_expires is None or ban_expires > now)
        users.append(
            {
                "id": uid,
                "name": uname,
                "admin": bool(is_admin_u),
                "vip": bool(is_vip_u),
                "mod": bool(is_mod_u),
                "banned": banned,
                "ban_reason": ban_reason,
                "ban_expires": ban_expires,
                "banned_by": banned_by,
                "banned_by_name": banned_by_name,
                "banned_by_is_mod": None,  # gefüllt unten
            }
        )

    # Pro gebanntem Nutzer: war der Banner ein Mod? (für UI-Hinweis "Stop Ban")
    banner_ids = {u["banned_by"] for u in users if u["banned"] and u["banned_by"]}
    if banner_ids:
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        placeholders = ",".join("?" for _ in banner_ids)
        cur.execute(
            f"SELECT user_id, admin, mod FROM users WHERE user_id IN ({placeholders})",
            list(banner_ids),
        )
        rows = cur.fetchall()
        con.close()
        banner_roles = {r[0]: (bool(r[1]), bool(r[2])) for r in rows}
        for u in users:
            if u["banned"] and u["banned_by"] in banner_roles:
                a, m = banner_roles[u["banned_by"]]
                u["banned_by_is_mod"] = m and not a

    bans_count = get_active_bans_count()
    user_count = len(users)
    return render_template(
        "adminban.html",
        users=users,
        user=user["name"],
        current_user_id=user["id"],
        is_admin=bool(infos.get("admin")),
        is_mod=bool(infos.get("mod")),
        cooldown_remaining=cooldown_remaining,
        bans=bans_count,
        user_count=user_count,
    )


@bp.route("/ban", methods=["POST"])
def ban_user():
    target_id = request.form.get("user_id", type=int)
    current_user = get_current_user()
    infos = get_infos(current_user["id"]) or {}
    is_admin_u = bool(infos.get("admin"))
    is_mod_u = bool(infos.get("mod"))

    if target_id is None or target_id == current_user["id"]:
        abort(403)

    reason = (request.form.get("reason") or "").strip() or "Kein Grund angegeben"
    permanent = request.form.get("permanent") == "1"
    expires_at = None
    if not permanent:
        days = request.form.get("days", type=int)
        if days and days > 0:
            expires_at = int(time.time()) + days * 86400

    # Zielnutzer laden, damit wir Mod/Admin-Status prüfen können.
    target_infos = get_infos(target_id)
    if target_infos is None:
        abort(404)
    target_is_admin = bool(target_infos.get("admin"))
    target_is_mod = bool(target_infos.get("mod"))

    if is_mod_u and not is_admin_u:
        # Mod-spezifische Checks
        if has_active_cooldown(current_user["id"]):
            abort(403)
        if target_is_admin or target_is_mod:
            abort(403)
    elif not is_admin_u:
        # Weder Admin noch Mod: sollte durch gate bereits abgefangen sein.
        abort(403)

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("DELETE FROM ban WHERE user_id = ?", (target_id,))
    cur.execute(
        "INSERT INTO ban (user_id, reason, expires_at, banned_by) VALUES (?, ?, ?, ?)",
        (target_id, reason, expires_at, current_user["id"]),
    )
    con.commit()
    con.close()

    return redirect("/admin/ban")


@bp.route("/unban", methods=["POST"])
def unban_user():
    """Nur Admins dürfen entbannen. Wenn der ursprüngliche Banner ein Mod war,
    bekommt dieser Mod jetzt einen 7-Tage-Ban-Cooldown."""
    current_user = get_current_user()
    infos = get_infos(current_user["id"]) or {}
    if not infos.get("admin"):
        abort(403)

    target_id = request.form.get("user_id", type=int)
    if target_id is None:
        abort(400)

    target_infos = get_infos(target_id)
    target_name = (target_infos or {}).get("user_name") or ""

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT banned_by FROM ban WHERE user_id = ?", (target_id,))
    row = cur.fetchone()
    original_banner = row[0] if row else None

    cur.execute("DELETE FROM ban WHERE user_id = ?", (target_id,))
    con.commit()
    con.close()

    if original_banner:
        banner_infos = get_infos(original_banner)
        if banner_infos and banner_infos["mod"] and not banner_infos["admin"]:
            record_mod_cooldown(
                original_banner,
                reason=f'Ban für "{target_name}" wurde von einem Admin aufgehoben.',
            )

    return redirect("/admin/ban")

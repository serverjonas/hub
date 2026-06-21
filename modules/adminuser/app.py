from flask import Blueprint, render_template, request, redirect, abort
import sqlite3
import time
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
from toolbox.files import DB_PATH
from toolbox.user import (
    get_current_user,
    get_infos,
    require_admin_or_mod,
    is_mod,
    is_admin,
    create_permission_suggestion,
    list_pending_suggestions,
    review_permission_suggestion,
)

bp = Blueprint("usermanager", __name__, template_folder="templates")


def get_active_bans():
    now = int(time.time())
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """
        SELECT COUNT(*)
        FROM ban
        WHERE expires_at IS NULL OR expires_at > ?
        """,
        (now,),
    )
    count = cur.fetchone()[0]
    con.close()
    return count


@bp.before_request
def _role_check():
    """Admin oder Mod – weitere Checks pro Endpoint."""
    require_admin_or_mod()


def get_all_users():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    now = int(time.time())
    cur.execute(
        """
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
        """,
        (now,),
    )
    rows = cur.fetchall()
    con.close()

    users = []
    for row in rows:
        uid, uname, is_admin_u, is_vip_u, is_mod_u, active_sessions = row
        users.append({
            "id":              uid,
            "name":            uname,
            "admin":           bool(is_admin_u),
            "vip":             bool(is_vip_u),
            "mod":             bool(is_mod_u),
            "active_sessions": active_sessions or 0,
        })
    return users


@bp.route("/")
def user_index():
    user = get_current_user()
    infos = get_infos(user["id"]) or {}
    is_admin_u = bool(infos.get("admin"))
    is_mod_u = bool(infos.get("mod"))

    users = get_all_users()
    bans = get_active_bans()
    pending = list_pending_suggestions() if is_admin_u else []

    return render_template(
        "admin_users_manager.html",
        users=users,
        user=user["name"],
        is_admin=is_admin_u,
        is_mod=is_mod_u,
        pending_suggestions=pending,
        bans=bans,
        user_count=len(users),
    )


@bp.route("/set-role", methods=["POST"])
def set_role():
    """Admin: ändert die Rolle sofort. Mod: legt einen Vorschlag an."""
    target_id = request.form.get("user_id", type=int)
    role = request.form.get("role")
    value = 1 if request.form.get("value") == "1" else 0

    if role not in ("admin", "vip", "mod"):
        abort(400)

    current_user = get_current_user()
    infos = get_infos(current_user["id"]) or {}
    is_admin_u = bool(infos.get("admin"))
    is_mod_u = bool(infos.get("mod"))

    if target_id is None:
        abort(400)

    # Schutz: niemand darf seine eigene Admin-Rolle entziehen.
    if target_id == current_user["id"] and role == "admin" and value == 0:
        return redirect("/admin/users")

    # Mod darf nur vorschlagen, nicht direkt anwenden.
    if is_mod_u and not is_admin_u:
        # Mods können sich selbst keine Rolle vorschlagen.
        if target_id == current_user["id"]:
            return redirect("/admin/users")
        create_permission_suggestion(
            mod_id=current_user["id"],
            target_user_id=target_id,
            role=role,
            value=value,
        )
        return redirect("/admin/suggestions?just_created=1")

    # Admin-Pfad: direkt anwenden.
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(f"UPDATE users SET {role} = ? WHERE user_id = ?", (value, target_id))
    con.commit()
    con.close()
    return redirect("/admin/users")


@bp.route("/review-suggestion", methods=["POST"])
def review_suggestion():
    """Nur Admins. Genehmigt oder lehnt einen Vorschlag ab."""
    current_user = get_current_user()
    infos = get_infos(current_user["id"]) or {}
    if not infos.get("admin"):
        abort(403)

    suggestion_id = request.form.get("suggestion_id", type=int)
    decision = request.form.get("decision")
    if suggestion_id is None or decision not in ("approved", "rejected"):
        abort(400)

    review_permission_suggestion(suggestion_id, current_user["id"], decision)
    return redirect(request.referrer or "/admin/users")


@bp.route("/suggestions")
def suggestions():
    """Dedizierte Review-Seite für Admins."""
    current_user = get_current_user()
    infos = get_infos(current_user["id"]) or {}
    if not infos.get("admin"):
        abort(403)

    items = list_pending_suggestions()
    just_created = request.args.get("just_created") == "1"

    return render_template(
        "admin_suggestions.html",
        user=current_user["name"],
        items=items,
        just_created=just_created,
    )


@bp.route("/delete-sessions", methods=["POST"])
def delete_sessions():
    """Nur Admins dürfen andere User ausloggen."""
    current_user = get_current_user()
    infos = get_infos(current_user["id"]) or {}
    if not infos.get("admin"):
        abort(403)

    target_id = request.form.get("user_id", type=int)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("DELETE FROM sessions WHERE user_id = ?", (target_id,))
    con.commit()
    con.close()
    return redirect("/admin/users")

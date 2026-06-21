import os
import sqlite3
import sys
import time

from flask import Blueprint, abort, render_template, request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
from toolbox.user import (
    get_current_user,
    get_infos,
    get_cooldown_remaining,
    is_admin_or_mod,
    require_admin_or_mod,
)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "users.db")

bp = Blueprint("modspanel", __name__, template_folder="templates")


@bp.before_request
def _role_check():
    """Nur Admins oder Mods sehen das Mod-Panel."""
    require_admin_or_mod()


def _active_bans_count() -> int:
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


def _user_count() -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    (count,) = cur.fetchone()
    con.close()
    return count


def _mod_pending_suggestions(mod_id):
    """Vorschläge, die der aktuelle Mod selbst eingereicht hat."""
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """
        SELECT s.id, s.target_user_id, s.role, s.value, s.comment, s.created_at,
               t.user_name
        FROM permission_suggestions s
        JOIN users t ON t.user_id = s.target_user_id
        WHERE s.mod_id = ? AND s.status = 'pending'
        ORDER BY s.created_at DESC
        """,
        (mod_id,),
    )
    rows = cur.fetchall()
    con.close()
    return [
        {
            "id":              r[0],
            "target_user_id":  r[1],
            "role":            r[2],
            "value":           bool(r[3]),
            "comment":         r[4],
            "created_at":      r[5],
            "target_name":     r[6],
        }
        for r in rows
    ]


@bp.route("/")
def mod_dashboard():
    user = get_current_user()
    infos = get_infos(user["id"]) or {}

    bans = _active_bans_count()
    ucount = _user_count()
    cooldown_remaining = get_cooldown_remaining(user["id"])
    pending_suggestions = _mod_pending_suggestions(user["id"])

    return render_template(
        "modspanel_dashboard.html",
        user=user["name"],
        is_admin=bool(infos.get("admin")),
        is_mod=bool(infos.get("mod")),
        cooldown_remaining=cooldown_remaining,
        banners=bans,
        user_count=ucount,
        pending_suggestions=pending_suggestions,
    )


@bp.route("/suggestions")
def my_suggestions():
    user = get_current_user()
    items = _mod_pending_suggestions(user["id"])
    return render_template(
        "modspanel_suggestions.html",
        user=user["name"],
        items=items,
    )

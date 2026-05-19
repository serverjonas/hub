import os
import sqlite3
import sys
import time

from flask import Blueprint, abort, render_template

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
from toolbox.user import get_current_user, get_infos

bp = Blueprint("admin", __name__, template_folder="templates")
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "users.db")


def get_active_bans():
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

def get_user_count():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM users")

    count = cur.fetchone()[0]
    conn.close()

    return count

# ── Admin-Check für alle Routen unter diesem Blueprint ──────────────────────
@bp.before_request
def require_admin():
    user = get_current_user()
    if user is None:
        abort(401)
    infos = get_infos(user["id"])
    if infos is None or not infos["admin"]:
        abort(403)


# ── Dashboard (Startseite des Admin-Panels) ──────────────────────────────────
@bp.route("/")
def admin_dashboard():
    user = get_current_user()
    
    bans = get_active_bans()
    user_count = get_user_count()
    return render_template("admin_nav.html", user=user["name"], bans=bans, user_count=user_count)


# ── Platzhalter-Routen – werden später durch eigene Blueprints ersetzt ────────
@bp.route("/roles")
def admin_roles():
    abort(501)


@bp.route("/sessions")
def admin_sessions():
    abort(501)


@bp.route("/logs")
def admin_logs():
    abort(501)


@bp.route("/announcements")
def admin_announcements():
    abort(501)


@bp.route("/settings")
def admin_settings():
    abort(501)

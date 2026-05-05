import sqlite3
from flask import Blueprint, render_template

from toolbox import DB_PATH, get_current_user, get_name

bp = Blueprint("news", __name__)

@bp.route("/")
def news():
    user = get_current_user()
    if user is None:
        return render_template("not_logged_in.html")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT sender_id, message, created_at
        FROM notifications
        WHERE user_id = ?
        ORDER BY created_at DESC
    """, (user["id"],))

    rows = cur.fetchall()
    conn.close()

    notifications = []
    for sender_id, message, created_at in rows:
        sender_name = "System"
        if sender_id:
            sender_name = get_name(sender_id) or "Unbekannt"

        notifications.append({
            "sender": sender_name,
            "message": message,
            "time": created_at
        })

    return render_template("news.html", notifications=notifications)

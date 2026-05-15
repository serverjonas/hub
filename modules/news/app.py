import sqlite3
import time
from flask import Blueprint, render_template, redirect

from toolbox.toolbox import DB_PATH, get_current_user, get_name

bp = Blueprint("news", __name__)


@bp.route("/")
def news_page():
    user = get_current_user()
    if user is None:
        return render_template("not_logged_in.html")

    now = int(time.time())
    last_48h = now - 48 * 60 * 60

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 🔥 alle Nachrichten (neu -> alt)
    cur.execute("""
        SELECT id, sender_id, message, created_at, read
        FROM notifications
        WHERE user_id = ?
          AND created_at >= ?
        ORDER BY created_at DESC
    """, (user["id"], last_48h))

    rows = cur.fetchall()
    conn.close()

    notifications = []
    for nid, sender_id, message, created_at, read in rows:
        sender = "System"
        if sender_id:
            sender = get_name(sender_id) or "Unbekannt"

        notifications.append({
            "id": nid,
            "sender": sender,
            "message": message,
            "time": created_at,
            "read": read
        })

    return render_template("news.html", notifications=notifications)


# 🟣 Klick → als gelesen + redirect zurück
@bp.route("/read/<int:nid>")
def mark_read(nid):
    user = get_current_user()
    if user is None:
        return redirect("/login")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        UPDATE notifications
        SET read = 1
        WHERE id = ? AND user_id = ?
    """, (nid, user["id"]))

    conn.commit()
    conn.close()

    return redirect("/news")


# 📂 ältere + bereits gelesene Nachrichten (Toggle-View)
@bp.route("/archive")
def archive():
    user = get_current_user()
    if user is None:
        return render_template("not_logged_in.html")

    now = int(time.time())
    last_48h = now - 48 * 60 * 60

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, sender_id, message, created_at, read
        FROM notifications
        WHERE user_id = ?
          AND created_at < ?
        ORDER BY created_at DESC
    """, (user["id"], last_48h))

    rows = cur.fetchall()
    conn.close()

    notifications = []
    for nid, sender_id, message, created_at, read in rows:
        sender = "System"
        if sender_id:
            sender = get_name(sender_id) or "Unbekannt"

        notifications.append({
            "id": nid,
            "sender": sender,
            "message": message,
            "time": created_at,
            "read": read
        })

    return render_template("news_archive.html", notifications=notifications)

import os
import sqlite3

from flask import Blueprint, redirect, render_template, request, url_for

from toolbox.toolbox import get_current_user

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "users.db")

bp = Blueprint("friends", __name__, template_folder="../../templates/friends")


def get_friends(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT u.user_id, u.user_name FROM users u
        JOIN friendships f ON (
            (f.user_id = ? AND f.friend_id = u.user_id) OR
            (f.friend_id = ? AND f.user_id = u.user_id)
        )
        WHERE f.status = 'accepted'
        ORDER BY u.user_name ASC
    """,
        (user_id, user_id),
    )
    result = cur.fetchall()
    conn.close()
    return result


def get_pending_incoming(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT u.user_id, u.user_name FROM users u
        JOIN friendships f ON f.user_id = u.user_id
        WHERE f.friend_id = ? AND f.status = 'pending'
    """,
        (user_id,),
    )
    result = cur.fetchall()
    conn.close()
    return result


def get_pending_outgoing(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT u.user_id, u.user_name FROM users u
        JOIN friendships f ON f.friend_id = u.user_id
        WHERE f.user_id = ? AND f.status = 'pending'
    """,
        (user_id,),
    )
    result = cur.fetchall()
    conn.close()
    return result


def send_request(from_id, to_username):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT user_id FROM users WHERE user_name = ?", (to_username,))
    to = cur.fetchone()
    if not to:
        conn.close()
        return "❌ Benutzer nicht gefunden"

    to_id = to[0]

    if from_id == to_id:
        conn.close()
        return "❌ Du kannst dir nicht selbst eine Anfrage schicken"

    cur.execute(
        """
        SELECT 1 FROM friendships
        WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)
    """,
        (from_id, to_id, to_id, from_id),
    )

    if cur.fetchone():
        conn.close()
        return "❌ Anfrage existiert bereits oder ihr seid schon befreundet"

    cur.execute(
        "INSERT INTO friendships (user_id, friend_id, status) VALUES (?, ?, 'pending')",
        (from_id, to_id),
    )
    conn.commit()
    conn.close()
    return "✅ Freundschaftsanfrage gesendet"


def accept_request(to_id, from_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE friendships SET status = 'accepted'
        WHERE user_id = ? AND friend_id = ? AND status = 'pending'
    """,
        (from_id, to_id),
    )
    conn.commit()
    conn.close()


def decline_or_remove(user_id, other_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM friendships
        WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)
    """,
        (user_id, other_id, other_id, user_id),
    )
    conn.commit()
    conn.close()


@bp.route("/", methods=["GET", "POST"])
def index():
    user_array = get_current_user()
    if not user_array:
        return render_template("not_logged_in.html")

    user_id = user_array["id"]
    user_name = user_array["name"]
    message = None

    if request.method == "POST":
        action = request.form.get("action")

        if action == "send":
            to_username = request.form.get("to_username", "").strip()
            message = send_request(user_id, to_username)

        elif action == "accept":
            from_id = int(request.form.get("from_id"))
            accept_request(user_id, from_id)

        elif action == "decline":
            other_id = int(request.form.get("other_id"))
            decline_or_remove(user_id, other_id)

        elif action == "remove":
            other_id = int(request.form.get("other_id"))
            decline_or_remove(user_id, other_id)

    friends = get_friends(user_id)
    incoming = get_pending_incoming(user_id)
    outgoing = get_pending_outgoing(user_id)

    return render_template(
        "friends/index.html",
        user=user_name,
        friends=friends,
        incoming=incoming,
        outgoing=outgoing,
        message=message,
    )

import os
import secrets
import sqlite3
import time

from flask import Blueprint, make_response, redirect, render_template, request
from werkzeug.security import check_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "users.db")

bp = Blueprint("login", __name__)


def get_user(username):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, password_hash FROM users WHERE user_name = ?", (username,)
    )
    row = cur.fetchone()
    conn.close()
    return row


def create_session(user_id):
    session_id = secrets.token_urlsafe(32)
    now = int(time.time())
    expires = now + 60 * 60 * 24 * 30  # 30d

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO sessions (session_id, user_id, created_at, expires_at)
        VALUES (?, ?, ?, ?)
    """,
        (session_id, user_id, now, expires),
    )
    conn.commit()
    conn.close()

    return session_id


@bp.route("/", methods=["GET", "POST"])
def login():
    message = None

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        user = get_user(username)

        if not user:
            message = "Benutzer existiert nicht ❌"
        else:
            user_id, pw_hash = user
            if not check_password_hash(pw_hash, password):
                message = "Falsches Passwort ❌"
            else:
                session_id = create_session(user_id)
                resp = make_response(redirect("/"))
                resp.set_cookie(
                    "session_id",
                    session_id,
                    httponly=True,
                    samesite="Lax",
                    max_age=60 * 60 * 24 * 30,  # ← 30d
                )
                return resp

    return render_template("login.html", message=message)

# toolbox.py
import os
import sqlite3
import time
from flask import request, make_response
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")

def get_lang():
    return request.cookies.get("lang", "deu")

def check_pw(username, password):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        "SELECT password_hash FROM users WHERE user_name = ?",
        (username,)
    )
    row = cur.fetchone()

    conn.close()

    # User existiert nicht
    if row is None:
        return False

    stored_hash = row[0]

    # Passwort prüfen
    return check_password_hash(stored_hash, password)


def set_lang(lang):
    resp = make_response()
    resp.set_cookie("lang", lang)
    return resp

def create_user(username, password):
    password_hash = generate_password_hash(password)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO users (user_name, password_hash, admin, vip, mod)
        VALUES (?, ?, 0, 0, 0)
    """,
        (username, password_hash),
    )

    conn.commit()
    conn.close()


def is_user_active(user_id):
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT active FROM user_activation WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()

    # Kein Eintrag = alter Account → erlauben
    if row is None:
        return True

    return row[0] == 1


def get_infos(user_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT admin, vip, mod FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    con.close()

    if row is None:
        return None

    return {"admin": bool(row[0]), "vip": bool(row[1]), "mod": bool(row[2])}


def is_banned(user_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT reason, expires_at FROM ban WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    con.close()

    if row is None:
        return False, None

    reason, expires_at = row

    if expires_at is not None and expires_at < int(time.time()):
        return False, None  # Ban abgelaufen

    return True, reason


def get_name(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT user_name
        FROM users
        WHERE user_id = ?
    """,
        (user_id,),
    )

    row = cur.fetchone()
    conn.close()

    if not row:
        return None  # User existiert nicht

    return row[0]  # username


def get_current_user():
    session_id = request.cookies.get("session_id")
    if not session_id:
        return None

    now = int(time.time())

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT users.user_id, users.user_name
        FROM sessions
        JOIN users ON sessions.user_id = users.user_id
        WHERE sessions.session_id = ?
          AND sessions.expires_at > ?
    """,
        (session_id, now),
    )

    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    # Rückgabe als Dictionary: sowohl ID als auch Name
    return {"id": row[0], "name": row[1]}


def get_friends(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT u.user_id FROM users u
        JOIN friendships f ON (
            (f.user_id = ? AND f.friend_id = u.user_id) OR
            (f.friend_id = ? AND f.user_id = u.user_id)
        )
        WHERE f.status = 'accepted'
    """,
        (user_id, user_id),
    )
    rows = cur.fetchall()
    conn.close()
    return [row[0] for row in rows]


if __name__ == "__main__":
    print(
        "dies ist die ToolBox man soll sie nur importiren nic	ht direckt asuführen"
    )

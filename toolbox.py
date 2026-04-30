# toolbox.py
import os
import sqlite3
import time

from flask import request
from werkzeug.security import generate_password_hash
BASE_DIR = "/var/www/serverjonas-hub"
DB_PATH = os.path.join(BASE_DIR, "users.db")
DATA_PATH = os.path.join(BASE_DIR, "data")

def check_or_create_users_db(db_path=DB_PATH):
    import os
    import sqlite3

    schema_sql = [
        """
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            admin INTEGER NOT NULL DEFAULT 0,
            vip INTEGER NOT NULL DEFAULT 0,
            mod INTEGER NOT NULL DEFAULT 0
        );
        """,
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
        """,
        """
        CREATE TABLE ban (
            user_id INTEGER NOT NULL,
            reason TEXT,
            expires_at INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
        """,
        """
        CREATE TABLE friendships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            friend_id INTEGER NOT NULL,
            status TEXT DEFAULT "pending",
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (friend_id) REFERENCES users(user_id)
        );
        """,
        """
        CREATE TABLE user_activation (
            user_id INTEGER PRIMARY KEY,
            active INTEGER DEFAULT 0,
            created_at INTEGER,
            activated_at INTEGER,
            activated_by INTEGER
        );
        """
    ]

    # --- 1. Existiert DB? ---
    if not os.path.exists(db_path):
        print("DB nicht vorhanden")

        if input("Neu erstellen? (y/n): ").strip().lower() != "y":
            print("Abbruch")
            return "missing"

        print("Erstelle neue Database...")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        for stmt in schema_sql:
            cur.execute(stmt)

        conn.commit()
        conn.close()

        print("Neue DB erstellt")
        return "created"

    # --- 2. Existiert → prüfen ob kaputt ---
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {t[0] for t in cur.fetchall()}

        required = {"users", "sessions", "ban", "friendships", "user_activation"}

        if not required.issubset(tables):
            print("DB existiert aber ist kaputt/unvollständig")

            if input("DB löschen & neu erstellen? (y/n): ").strip().lower() != "y":
                return "corrupt"

            conn.close()

            print("Lösche alte DB...")
            os.remove(db_path)

            print("Erstelle neue DB...")

            conn = sqlite3.connect(db_path)
            cur = conn.cursor()

            for stmt in schema_sql:
                cur.execute(stmt)

            conn.commit()
            conn.close()

            print("DB repariert & neu erstellt")
            return "repaired"

        print("DB OK")
        conn.close()
        return "ok"

    except Exception as e:
        print("DB kaputt (SQLite Fehler)")
        print(e)

        if input("DB löschen & neu erstellen? (y/n): ").strip().lower() != "y":
            return "broken"

        if os.path.exists(db_path):
            os.remove(db_path)

        print("Erstelle neue DB...")

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        for stmt in schema_sql:
            cur.execute(stmt)

        conn.commit()
        conn.close()

        print("DB neu erstellt nach Crash")
        return "recovered"

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

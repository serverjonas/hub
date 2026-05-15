from flask import Blueprint, request, jsonify, abort
import sqlite3
import os
import secrets
import string

from werkzeug.security import generate_password_hash
from toolbox.toolbox import check_pw, BASE_PATH, DB_PATH

bp = Blueprint("mkkey", __name__)

DB_USERS = DB_PATH
DB_API = os.path.join(BASE_PATH,"modules", "api", "v1", "api.db")


def init_db():
    os.makedirs(os.path.dirname(DB_API), exist_ok=True)

    conn = sqlite3.connect(DB_API)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS keys (
        key_id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        key_hash TEXT NOT NULL,
        description TEXT
    )
    """)

    conn.commit()
    conn.close()


def get_user_id(username):
    conn = sqlite3.connect(DB_USERS)
    cur = conn.cursor()

    cur.execute("SELECT user_id FROM users WHERE user_name = ?", (username,))
    row = cur.fetchone()
    conn.close()

    return row[0] if row else None


def generate_key(length=16):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


@bp.route("/", methods=["POST"])
def create_key():
    init_db()

    data = request.json
    if not data:
        return abort(400)

    username = data.get("username")
    password = data.get("password")
    description = data.get("description", "")

    if not username or not password:
        return abort(400)

    # 🔐 Login check
    if not check_pw(username, password):
        return abort(403)

    owner_id = get_user_id(username)
    if not owner_id:
        return abort(404)

    # 🔑 Key erzeugen
    raw_key = generate_key()
    key_hash = generate_password_hash(raw_key)

    conn = sqlite3.connect(DB_API)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO keys (owner_id, key_hash, description)
        VALUES (?, ?, ?)
    """, (owner_id, key_hash, description))

    conn.commit()
    conn.close()

    return jsonify({
        "status": "created",
        "api_key": raw_key
    })

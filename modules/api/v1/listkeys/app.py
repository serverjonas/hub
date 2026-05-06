from flask import Blueprint, request, jsonify, abort
import sqlite3
import os

from werkzeug.security import check_password_hash
from toolbox import BASE_PATH

bp = Blueprint("listkeys", __name__)

# 🔧 zentrale DB (keine wilden relativen Pfade mehr)
DB_API = os.path.join(BASE_PATH, "api.db")

# -----------------------------
# 🧱 DB Init
# -----------------------------
def init_db():
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


# -----------------------------
# 🔐 API KEY AUTH
# -----------------------------
def get_user_from_api_key():
    auth = request.headers.get("Authorization")

    if not auth:
        return None

    if not auth.startswith("Bearer "):
        return None

    api_key = auth.replace("Bearer ", "").strip()

    conn = sqlite3.connect(DB_API)
    cur = conn.cursor()

    # wir prüfen alle Keys (weil hashed)
    cur.execute("SELECT owner_id, key_hash FROM keys")
    rows = cur.fetchall()
    conn.close()

    for owner_id, key_hash in rows:
        if check_password_hash(key_hash, api_key):
            return owner_id

    return None


# -----------------------------
# 📋 LIST KEYS
# -----------------------------

@bp.errorhandler(401)
def unauthorized_error(e):
    return Response("ungültiger api key", status=401, mimetype="text/plain")

@bp.route("/", methods=["GET"])
def list_keys():
    init_db()

    owner_id = get_user_from_api_key()
    if not owner_id:
        return abort(401)

    conn = sqlite3.connect(DB_API)
    cur = conn.cursor()

    cur.execute("""
        SELECT key_id, description
        FROM keys
        WHERE owner_id = ?
    """, (owner_id,))

    rows = cur.fetchall()
    conn.close()

    return jsonify([
        {
            "key_id": r[0],
            "description": r[1]
        }
        for r in rows
    ])

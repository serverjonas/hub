from flask import Blueprint, request, jsonify, abort
import sqlite3
import os

from toolbox.toolbox import get_current_user

bp = Blueprint("rmkeys", __name__)

DB_API = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "api", "v1", "api.db")


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


@bp.route("/", methods=["POST"])
def delete_key():
    init_db()

    user = get_current_user()
    if not user:
        return abort(401)

    data = request.json
    key_id = data.get("key_id")

    if not key_id:
        return abort(400)

    conn = sqlite3.connect(DB_API)
    cur = conn.cursor()

    # nur eigene Keys löschen
    cur.execute("""
        DELETE FROM keys
        WHERE key_id = ? AND owner_id = ?
    """, (key_id, user["id"]))

    conn.commit()
    conn.close()

    return jsonify({"status": "deleted"})

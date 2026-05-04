from flask import Blueprint, request, jsonify, abort
import sqlite3
import os

from toolbox import get_current_user
from werkzeug.security import check_password_hash

bp = Blueprint("listkeys", __name__)

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


@bp.route("/", methods=["GET"])
def list_keys():
    init_db()

    user = get_current_user()
    if not user:
        return abort(401)

    conn = sqlite3.connect(DB_API)
    cur = conn.cursor()

    cur.execute("""
        SELECT key_id, description
        FROM keys
        WHERE owner_id = ?
    """, (user["id"],))

    rows = cur.fetchall()
    conn.close()

    return jsonify([
        {"key_id": r[0], "description": r[1]}
        for r in rows
    ])

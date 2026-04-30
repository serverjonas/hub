import os
import sqlite3
import time

from flask import Blueprint, render_template

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "users.db")

bp = Blueprint("adminpanel", __name__)


def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM users")
    users = cur.fetchall()

    conn.close()
    return users


@bp.route("/")
def admin_index():
    users = get_all_users()
    return render_template("admin_users.html", users=users)

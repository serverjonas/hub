import json
import os
import re
import sqlite3

from flask import Blueprint, render_template, request

from toolbox.toolbox import DB_PATH, create_user

blacklist = ["tpm", "admin", "deinemutter"]
SERVER_KEY = "server"


def valid_password(password):
    checks = []

    # Länge
    if len(password) < 8:
        checks.append("❌ Mindestens 8 Zeichen erforderlich.")

    # Mindestens eine Zahl
    if not re.search(r"\d", password):
        checks.append("❌ Mindestens eine Zahl erforderlich.")

    # Häufige Passwörter
    banned = ["passwort", "password", "12345678", "hallo123"]
    if password.lower() in banned:
        checks.append("❌ Dieses Passwort ist zu einfach.")

    if checks:
        return False, checks
    return True, None


def valid_str(the_str):
    return re.fullmatch(r"[a-zA-Z0-9_\-\+\?\!\@\#\$\%\*\=]{3,32}", the_str) is not None


def exist_account(username: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE user_name = ? LIMIT 1", (username,))
    result = cur.fetchone()
    conn.close()
    return result is not None


bp = Blueprint("register", __name__, template_folder="../../templates")


@bp.route("/", methods=["GET", "POST"])
def register():
    message = None
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        server_key = request.form.get("server_key")

        if not SERVER_KEY or server_key != SERVER_KEY:
            message = "❌ Ungültiger Server-Key"
        elif not valid_str(username):
            message = "❌ Ungültiger Benutzername (3–32 Zeichen)."
        elif exist_account(username):
            message = "❌ Accountname bereits belegt"
        elif username in blacklist:
            message = "❌ Dieser Username ist verboten"
        else:
            valid, pw_message = valid_password(password)
            if not valid:
                message = pw_message
            else:
                try:
                    create_user(username, password)
                    message = "✅ Account erfolgreich erstellt"
                except Exception as e:
                    message = f"❌ Fehler: {e}"

    return render_template("register.html", message=message)

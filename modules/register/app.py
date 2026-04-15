from flask import Blueprint, render_template, request
import json
import os
import sqlite3
import re
from toolbox import create_user, DB_PATH

blacklist = ["tpm", "admin", "deinemutter"]
SERVER_KEY = "server"


def valid_password(password):
    checks = []

    # Länge
    if len(password) < 8:
        checks.append("❌ Mindestens 8 Zeichen – das ist keine PIN für dein Fahrradschloss")

    # Großbuchstabe
    if not re.search(r"[A-Z]", password):
        checks.append("❌ Mindestens ein Großbuchstabe – SCHREI wenigstens einmal")

    # Sonderzeichen
    if not re.search(r"[!@#$%*=+\-?_]", password):
        checks.append("❌ Mindestens ein Sonderzeichen (!@#$%*=+-?_) – sei kreativ, du schaffst das")

    # Aufeinanderfolgende Zahlen
    if re.search(r"(?:0(?=1)|1(?=2)|2(?=3)|3(?=4)|4(?=5)|5(?=6)|6(?=7)|7(?=8)|8(?=9)){3}", password):
        checks.append("❌ Keine 4 aufeinanderfolgenden Zahlen (1234 etc.) – das war 1998 schon schlecht")

    # Wiederholende Zeichen (z.B. aaaa, 1111)
    if re.search(r"(.)\1{2,}", password):
        checks.append("❌ Keine 3+ gleichen Zeichen hintereinander (aaa, 111...) – Strg+C Strg+V ist keine Strategie")

    # Keine Leerzeichen
    if re.search(r"\s", password):
        checks.append("❌ Keine Leerzeichen – ein Passwort ist kein Gedicht")

    # Nur Zahlen
    if password.isdigit():
        checks.append("❌ Nicht nur Zahlen – du bist Mensch, beweise es")

    # Häufige Passwörter
    banned = ["passwort", "password", "qwertz", "qwerty", "abc123", "123456", "hallo", "admin", "login", "welcome"]
    if password.lower() in banned:
        checks.append("❌ Dieses Passwort steht auf jeder Hacker-Liste – bitte mehr Fantasie")

    # Jahreszahlen (1900–2099)
    if re.search(r"(19|20)\d{2}", password):
        checks.append("❌ Kein Geburtsjahr oder Datum – dein Hacker weiß wann du geboren bist")

    # Keine Tastatur-Sequenzen
    keyboard_seqs = ["qwert", "qwertz", "asdf", "yxcv", "zxcv"]
    if any(seq in password.lower() for seq in keyboard_seqs):
        checks.append("❌ Keine Tastaturmuster – deine Finger sind keine Passwort-Strategie")

    # Mindestens 2 Zahlen
    if len(re.findall(r"\d", password)) < 2:
        checks.append("❌ Mindestens 2 Zahlen – eine reicht nicht, wir sind nicht bei 'Wer wird Millionär'")

    # Mindestens 2 Großbuchstaben
    if len(re.findall(r"[A-Z]", password)) < 2:
        checks.append("❌ Mindestens 2 Großbuchstaben – einmal SCHREIEN reicht nicht")

    if checks:
        return False, checks
    return True, None

#
def valid_str(the_str):
    return re.fullmatch(r"[a-zA-Z0-9_\-\+\?\!\@\#\$\%\*\=]{3,32}", the_str) is not None

def exist_account(username: str) -> bool:
    """
    Prüft, ob ein Benutzername bereits in der Datenbank existiert.
    Gibt True zurück, wenn der Benutzer existiert, sonst False.
    """
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
            message = "❌ Ungültiger Benutzername. Erlaubt sind 3–32 Zeichen: a-z A-Z 0-9 _ - + ? ! @ # $ % * ="
        elif exist_account(username):
            message = "❌ Accountname bereits belegt"
        elif not valid_str(password):
            message = "❌ Ungültiges Passwort. Erlaubt sind 3–32 Zeichen: a-z A-Z 0-9 _ - + ? ! @ # $ % * ="
        elif username in blacklist:
            message = "Dieser Username ist Verboten"
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

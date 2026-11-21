import os
import sqlite3
import sys
from datetime import datetime

from flask import Blueprint, abort, redirect, render_template, request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from toolbox.files import DB_PATH
from toolbox.news import create_notification
from toolbox.user import get_current_user, get_infos

bp = Blueprint("notification_manager", __name__, template_folder="templates")

# Grenzen fuer den optionalen Sendezeitstempel (Unix-Sekunden):
# 1970-01-01 bis 2100-01-01. Verhindert absurde Werte, die Anzeige-
# Formatierung oder Sortierung kaputt machen.
MIN_SENT_TS = 0
MAX_SENT_TS = 4102444800


def _parse_sent_at(raw):
    """Optionaler Zeitstempel aus ``<input type="datetime-local">``.

    Liefert ``(timestamp, error)``. Leer bedeutet "jetzt senden"
    (``(None, None)``), ein nicht parsebarer Wert liefert eine Meldung.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None, "Ungueltiger Zeitstempel – bitte Datum und Uhrzeit angeben."
    ts = int(dt.timestamp())
    if not (MIN_SENT_TS <= ts <= MAX_SENT_TS):
        return None, "Zeitstempel muss zwischen 1970 und 2100 liegen."
    return ts, None

@bp.before_request
def require_admin():
    """Sicherheits-Check: Nur Administratoren dürfen dieses Modul nutzen."""
    user = get_current_user()
    if user is None:
        abort(401)
    infos = get_infos(user["id"])
    if infos is None or not infos["admin"]:
        abort(403)

@bp.route("/")
def index():
    """Zeigt die Übersicht und das Formular zum Erstellen von Benachrichtigungen."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Liste aller User für das Dropdown-Menü laden
    cur.execute("SELECT user_id, user_name FROM users ORDER BY user_name ASC")
    users = cur.fetchall()
    conn.close()
    
    return render_template("notifmanager.html", users=users)

@bp.route("/send", methods=["POST"])
def send():
    """Verarbeitet den Versand der Benachrichtigung."""
    sender = get_current_user()
    target = request.form.get("target")  # Kann 'all' oder eine spezifische user_id sein
    message = request.form.get("message")
    notif_type = request.form.get("type", "system")

    # Optionaler Sendezeitstempel: bestimmt, wann die Nachricht laut
    # Anzeige gesendet wurde (z.B. rueckdatiert). Leer = jetzt.
    sent_at, ts_error = _parse_sent_at(request.form.get("sent_at"))
    if ts_error:
        return f"Fehler: {ts_error}", 400

    if not message:
        return "Fehler: Nachricht darf nicht leer sein", 400

    if target == "all":
        # Nachricht an alle Benutzer senden
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users")
        user_ids = [row[0] for row in cur.fetchall()]
        conn.close()

        for uid in user_ids:
            create_notification(
                uid, message, type=notif_type, sender_id=sender["id"],
                created_at=sent_at,
            )
    else:
        # Nachricht an einen spezifischen Benutzer senden
        try:
            create_notification(
                int(target), message, type=notif_type, sender_id=sender["id"],
                created_at=sent_at,
            )
        except (ValueError, TypeError):
            abort(400)

    return redirect("/admin/notifications?success=1")

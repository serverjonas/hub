# modules/chat/app.py
"""
SJNchat – Person-to-Person Chat.

Baut auf der `notifications`-Tabelle auf (`type='dm'`). Jede Nachricht wird
genau einmal gespeichert (Empfänger = user_id, sender_id = Sender). Damit
gibt es keine Duplikate beim Polling – `get_chat_messages` aus der Toolbox
joined die beiden Richtungen bereits korrekt.

Sicherheit / Gating:
  * Nur eingeloggte Benutzer.
  * Nur mit akzeptierten Freunden chatten (status='accepted').
  * Kein Self-Chat.
  * Gebannte Nutzer dürfen weiter lesen, aber nicht senden.
  * Nachrichtenlänge begrenzt (2000 Zeichen).
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

from datetime import datetime, date
from flask import (
    Blueprint,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from toolbox.toolbox import DB_PATH
from toolbox.user import (
    get_current_user,
    is_banned,
)
from toolbox.news import (
    get_chat_messages,
    send_dm,
)

bp = Blueprint("chat", __name__, template_folder="../../templates/chat")

# ─── Konstanten ─────────────────────────────────────────────────────────────
MSG_MAX_LEN = 2000
POLL_INTERVAL_DEFAULT = 4  # Sekunden


# ─── Jinja Template Helpers ─────────────────────────────────────────────────
def _day_label(ts: int) -> str:
    """Tages-Label für die Trenner im Chat (z.B. Heute / Gestern / Today).
    Sprache folgt dem lang-Cookie (deu|eng), Default deu.
    """
    try:
        d = datetime.fromtimestamp(int(ts))
    except (TypeError, ValueError, OSError):
        return ""
    today = date.today()
    if d.date() == today:
        return "Heute" if _current_lang() == "deu" else "Today"
    if d.date() == date.fromordinal(today.toordinal() - 1):
        return "Gestern" if _current_lang() == "deu" else "Yesterday"
    fmt = d.strftime("%A, %d.%m.")
    # Englischer Wochentag → englischen Namen nutzen, falls gewünscht.
    if _current_lang() == "eng":
        with _english_locale():
            fmt = d.strftime("%A, %d.%m.")
    return fmt


def _current_lang() -> str:
    """Sprache aus dem lang-Cookie. Default 'deu'."""
    try:
        from flask import request
        code = request.cookies.get("lang", "deu")
    except Exception:
        code = "deu"
    return "eng" if code == "eng" else "deu"


def _english_locale():
    """Kontextmanager: aktiviert englisches Locale für Datums-Formatting."""
    import contextlib
    import locale

    @contextlib.contextmanager
    def _cm():
        prev = locale.setlocale(locale.LC_TIME, None)
        try:
            # Try C.UTF-8 first, then C, then en_US, then leave as is.
            for loc in ("C.UTF-8", "C", "en_US.UTF-8", "en_US"):
                try:
                    locale.setlocale(locale.LC_TIME, loc)
                    break
                except locale.Error:
                    continue
            yield
        finally:
            try:
                locale.setlocale(locale.LC_TIME, prev)
            except locale.Error:
                pass

    return _cm()


def _time_short(ts: int) -> str:
    """Kurze Uhrzeit im Format HH:MM."""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return ""


# Globals für alle Templates dieses Blueprints.
bp.add_app_template_global(_day_label, "_day_label")
bp.add_app_template_global(_time_short, "_time_short")


# ─── Helper ─────────────────────────────────────────────────────────────────
def _require_login():
    """Liefert den eingeloggten User oder abort(401)."""
    user = get_current_user()
    if user is None:
        abort(401)
    return user


def _conn():
    return sqlite3.connect(DB_PATH)


def _are_friends(user_id: int, other_id: int) -> bool:
    """True wenn beide in der friendships-Tabelle mit status='accepted' stehen."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1 FROM friendships
        WHERE status = 'accepted'
          AND (
              (user_id = ? AND friend_id = ?) OR
              (user_id = ? AND friend_id = ?)
          )
        """,
        (user_id, other_id, other_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return row is not None


def _require_friend(user_id: int, other_id: int):
    """Beendet die Anfrage mit 403, wenn other_id kein akzeptierter Freund ist.

    Verhindert außerdem Self-Chat.
    """
    if other_id <= 0 or other_id == user_id:
        abort(403)
    if not _are_friends(user_id, other_id):
        abort(403)


def _user_summary(user_id: int):
    """Kompakte Anzeige für die Sidebar."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, user_name, admin, vip, mod FROM users WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "admin": bool(row[2]),
        "vip": bool(row[3]),
        "mod": bool(row[4]),
    }


def _conversations_for(user_id: int):
    """Baut die Sidebar-Liste.

    Pro akzeptiertem Freund: letzte DM, unread count.
    """
    conn = _conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        SELECT u.user_id, u.user_name, u.admin, u.vip, u.mod
        FROM users u
        JOIN friendships f ON (
            (f.user_id = ? AND f.friend_id = u.user_id) OR
            (f.friend_id = ? AND f.user_id = u.user_id)
        )
        WHERE f.status = 'accepted'
        ORDER BY u.user_name COLLATE NOCASE ASC
        """,
        (user_id, user_id),
    )
    friends = cur.fetchall()
    if not friends:
        conn.close()
        return []

    result = []
    for fr in friends:
        fid = fr["user_id"]
        # letzte Nachricht (in beide Richtungen)
        cur.execute(
            """
            SELECT message, created_at, sender_id, id
            FROM notifications
            WHERE type = 'dm'
              AND (
                  (user_id = ? AND sender_id = ?) OR
                  (user_id = ? AND sender_id = ?)
              )
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id, fid, fid, user_id),
        )
        last = cur.fetchone()
        # ungelesene Nachrichten, die MIR geschickt wurden
        cur.execute(
            """
            SELECT COUNT(*) FROM notifications
            WHERE type = 'dm'
              AND user_id = ?
              AND sender_id = ?
              AND read = 0
            """,
            (user_id, fid),
        )
        unread = cur.fetchone()[0]

        result.append(
            {
                "id": fid,
                "name": fr["user_name"],
                "admin": bool(fr["admin"]),
                "vip": bool(fr["vip"]),
                "mod": bool(fr["mod"]),
                "last_message": last["message"] if last else "",
                "last_time": int(last["created_at"]) if last else 0,
                "last_from_me": bool(last and last["sender_id"] == user_id),
                "last_id": int(last["id"]) if last else 0,
                "unread": int(unread or 0),
            }
        )

    conn.close()
    # neueste Aktivität zuerst, dann name
    result.sort(
        key=lambda c: (-c["last_time"] if c["last_time"] else 0, c["name"].lower())
    )
    return result


def _messages_for(user_id: int, other_id: int, after_id: int):
    """Liefert Chat-Nachrichten als serialisierbare Liste."""
    raw = get_chat_messages(user_id, other_id, after_id=after_id)
    out = []
    for m in raw:
        out.append(
            {
                "id": int(m["id"]),
                "from": int(m["from"]) if m["from"] is not None else None,
                "to": int(m["to"]) if m["to"] is not None else None,
                "message": m["message"],
                "time": int(m["created_at"]),
            }
        )
    return out


def _mark_conversation_read(user_id: int, other_id: int):
    """Markiert alle vom anderen an mich geschickten DMs als gelesen."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE notifications
        SET read = 1
        WHERE type = 'dm'
          AND user_id = ?
          AND sender_id = ?
          AND read = 0
        """,
        (user_id, other_id),
    )
    conn.commit()
    conn.close()


# ─── Index / Conversation View ─────────────────────────────────────────────
@bp.route("/", methods=["GET"])
def index():
    user = _require_login()

    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM friendships WHERE status = 'accepted' LIMIT 1")
    has_friends = cur.fetchone() is not None
    conn.close()

    banned, _reason = is_banned(user["id"])

    return render_template(
        "chat/index.html",
        user=user["name"],
        banned=banned,
        conversations=_conversations_for(user["id"]),
        active_id=None,
        active_user=None,
        messages=[],
        owned_max_id=0,
        has_friends=has_friends,
    )


@bp.route("/<int:friend_id>", methods=["GET"])
def conversation(friend_id: int):
    user = _require_login()
    _require_friend(user["id"], friend_id)

    # Beim Öffnen der Konversation direkt als gelesen markieren,
    # damit die Badge in der Sidebar / im Header korrekt verschwindet.
    _mark_conversation_read(user["id"], friend_id)

    messages = _messages_for(user["id"], friend_id, after_id=0)
    last_id = max((m["id"] for m in messages), default=0)

    banned, _reason = is_banned(user["id"])

    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM friendships WHERE status = 'accepted' LIMIT 1")
    has_friends = cur.fetchone() is not None
    conn.close()

    return render_template(
        "chat/index.html",
        user=user["name"],
        banned=banned,
        conversations=_conversations_for(user["id"]),
        active_id=friend_id,
        active_user=_user_summary(friend_id),
        messages=messages,
        owned_max_id=last_id,
        has_friends=has_friends,
    )


# ─── Senden ─────────────────────────────────────────────────────────────────
@bp.route("/<int:friend_id>/send", methods=["POST"])
def send(friend_id: int):
    user = _require_login()

    banned, _ = is_banned(user["id"])
    if banned:
        return jsonify(success=False, error="banned"), 403

    _require_friend(user["id"], friend_id)

    if request.is_json:
        raw = (request.json.get("message") or "")
    else:
        raw = (request.form.get("message") or "")
    text = raw.strip()
    if not text:
        return jsonify(success=False, error="empty"), 400
    if len(text) > MSG_MAX_LEN:
        return jsonify(success=False, error="too_long"), 400

    # send_dm legt genau eine notification (user_id=friend_id, sender_id=me)
    msg_id = send_dm(user["id"], friend_id, text)
    now = int(time.time())
    return jsonify(
        success=True,
        id=int(msg_id) if msg_id else None,
        message={
            "id": int(msg_id) if msg_id else 0,
            "from": user["id"],
            "to": friend_id,
            "message": text,
            "time": now,
        },
    )


# ─── Polling ────────────────────────────────────────────────────────────────
@bp.route("/<int:friend_id>/poll", methods=["GET"])
def poll(friend_id: int):
    user = _require_login()
    _require_friend(user["id"], friend_id)

    try:
        after_id = int(request.args.get("after_id") or 0)
    except (TypeError, ValueError):
        after_id = 0

    # Neue Nachrichten (egal in welche Richtung).
    messages = _messages_for(user["id"], friend_id, after_id=after_id)

    # Wenn neue Nachrichten von dem Anderen an mich dabei sind, autom. read.
    new_from_other = any(m["from"] == friend_id for m in messages)
    if new_from_other:
        _mark_conversation_read(user["id"], friend_id)

    # Sidebar gibt immer ALLE Konversationen zurück, damit die unread-Badges
    # auch für nicht-aktive Chats sichtbar aktualisiert werden.
    return jsonify(messages=messages, conversations=_conversations_for(user["id"]))




"""Groupchat module.

Erweitert das Notification-System um ``type='group_dm'`` Broadcasts:

  * Jede Nachricht wird einmal pro Mitglied in `notifications` eingefügt
    (inkl. Sender), so dass jede:r Empfänger:in ihre eigene read-Flagge hat.
  * `chat_groups` + `chat_group_members` verwalten Membership; CASCADE delete
    bei ``DELETE FROM chat_groups``.
  * Suche innerhalb einer Gruppe nutzt das gleiche LIKE-Escape-Pattern wie die
    DM-Suche.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import sys

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from markupsafe import escape, Markup

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from toolbox.files import DB_PATH
from toolbox.user import get_friends
from toolbox.news import (
    GROUP_DESC_MAX,
    GROUP_DM,
    GROUP_MAX_MEMBERS,
    GROUP_MSG_MAX,
    GROUP_NAME_MAX,
    GroupError,
    add_group_member,
    create_group,
    delete_group,
    get_chat_messages,
    get_group,
    get_group_messages,
    is_group_member,
    leave_group,
    list_group_members,
    list_my_groups,
    mark_group_read,
    remove_group_member,
    search_chat_messages,
    send_group_message,
)
from toolbox.user import (
    are_friends,
    get_current_user,
    get_infos,
    get_user_by_name,
    is_banned,
)


bp = Blueprint("groupchat", __name__,
               template_folder="templates")


# ─── Jinja Filters & Globals (groupchat-scope) ─────────────────────────────
#
# Diese Utilitys sind hier als lokale Kopien definiert, damit das Modul
# kein Cross-Blueprint-Import-Risiko trägt (Lade-Reihenfolge im
# ``app.py: load_modules()`` ist nicht deterministisch). Filter und
# Globals werden in genau der Form registriert, in der sie das Template
# verwendet — siehe ``modules/groupchat/templates/groupchat/index.html``.


def _dt_ymd(ts):
    """Filter: ``ts`` (UNIX ts) → ``'YYYY-MM-DD HH:MM'``."""
    try:
        return _dt.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _escape_search(text, q=None):
    """Filter: HTML-escape ``text``; umschließe Treffer von ``q`` mit ``<mark>``."""
    if text is None:
        return ""
    if not q:
        return escape(text)
    s = str(text)
    q_lower = str(q).lower()
    s_lower = s.lower()
    out, cur, qlen = [], 0, len(q_lower)
    while cur < len(s):
        idx = s_lower.find(q_lower, cur)
        if idx < 0:
            out.append(escape(s[cur:]))
            break
        out.append(escape(s[cur:idx]))
        out.append(
            Markup("<mark>") + escape(s[idx:idx + qlen]) + Markup("</mark>")
        )
        cur = idx + qlen
    return Markup("".join(out))


def _i18n_literal(key):
    """Filter: best-effort Server-Side i18n-Lookup für aktuelle Lang.

    Liefert ``''`` bei Fehlschlag, so dass ``{{ key | i18n_literal or 'fallback' }}``
    im Template greift. Liest nur ``groupchat_<lang>.json``.
    """
    try:
        lang = (request.cookies.get("lang", "deu") or "deu")
        if lang not in ("deu", "eng"):
            lang = "deu"
        i18n_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "static")
        )
        path = os.path.join(i18n_root, f"groupchat_{lang}.json")
        if not os.path.isfile(path):
            return ""
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return str(data.get(key, "") or "")
    except Exception:
        return ""


def _char_initials(name, fallback="?"):
    """Global: 1-2 Buchstaben aus Name (z. B. 'Maria K.' → 'MK')."""
    s = (name or "").strip()
    if not s:
        return fallback
    parts = s.split()
    if len(parts) >= 2 and parts[0] and parts[1]:
        return (parts[0][0] + parts[1][0]).upper()
    return s[0].upper()


def _avatar_img(user_id, avatar_path="", name="", size="md"):
    """Global: kompaktes <img>-Tag; Fallback ist /profile/avatar-fallback/uN."""
    parts = {
        "sm": ("28px", "9px"),
        "lg": ("64px", "18px"),
        "md": ("42px", "12px"),
    }.get(size, ("42px", "12px"))
    dim, radius = parts

    src = ""
    if user_id:
        safe_name = str(name or "")
        seeded = abs(hash(("uv", int(user_id), safe_name))) % 10000
        letters = _char_initials(safe_name)
        hue1 = (seeded * 47) % 360
        hue2 = (seeded * 71) % 360
        # Inline SVG-Fallback (funktioniert ohne /profile/avatar-fallback-Route,
        # und das <img>-onerror zeigt es bei 404).
        src = (
            f"data:image/svg+xml;utf8,"
            f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
            f"<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
            f"<stop offset='0' stop-color='hsl({hue1},70%,62%)'/>"
            f"<stop offset='1' stop-color='hsl({hue2},70%,52%)'/>"
            f"</linearGradient></defs>"
            f"<rect width='64' height='64' rx='{radius}' fill='url(%23g)'/>"
            f"<text x='32' y='40' text-anchor='middle' font-size='28' "
            f"font-family='system-ui,sans-serif' font-weight='700' "
            f"fill='white'>{escape(letters)}</text>"
            f"</svg>"
        )
    return (
        f"<img src=\"{escape(src)}\" alt=\"{escape(str(name or ''))}\" "
        f"style=\"width:{dim};height:{dim};border-radius:{radius};"
        f"object-fit:cover;display:block;\">"
    )


def _day_label(ts):
    """Global: Tages-Label ('Heute'/'Gestern'/'Wochentag, TT.MM.')."""
    try:
        d = _dt.datetime.fromtimestamp(int(ts))
    except (TypeError, ValueError, OSError):
        return ""
    today = d.date()
    now = _dt.date.today()
    lang = (request.cookies.get("lang", "deu") or "deu")
    if lang not in ("deu", "eng"):
        lang = "deu"
    if today == now:
        return "Heute" if lang == "deu" else "Today"
    if today == _dt.date.fromordinal(now.toordinal() - 1):
        return "Gestern" if lang == "deu" else "Yesterday"
    fmt = d.strftime("%A, %d.%m.")
    if lang == "eng":
        # einfache englische Wochentagsabbildung (ohne Systemlocale-Tricks)
        fmt = d.strftime("%A, %m/%d.")
    return fmt


def _time_short(ts):
    """Global: kurze Uhrzeit 'HH:MM'."""
    try:
        return _dt.datetime.fromtimestamp(int(ts)).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _lookup_user_name(uid):
    """Templates: ID → Username (für Sender-Labels in Broadcasts)."""
    try:
        from toolbox.user import get_name
        return get_name(int(uid))
    except (TypeError, ValueError, Exception):
        return ""


# Reihenfolge: Filter zuerst, dann Globals.
bp.add_app_template_filter(_dt_ymd, "datetime_ymd")
bp.add_app_template_filter(_escape_search, "escape_search")
bp.add_app_template_filter(_i18n_literal, "i18n_literal")
bp.add_app_template_global(_char_initials, "char_initials")
bp.add_app_template_global(_avatar_img, "avatar_img")
bp.add_app_template_global(_day_label, "_day_label")
bp.add_app_template_global(_time_short, "_time_short")
bp.add_app_template_global(_lookup_user_name, "get_user_by_name_from_id")


# ─── Helpers ────────────────────────────────────────────────────────────────

def _require_login():
    user = get_current_user()
    if user is None:
        abort(401)
    return user


def _conn():
    return sqlite3.connect(DB_PATH)


def _send_fallback(value, ok_redirect, flash_msg=None):
    """Helper: AJAX/fetch erwartet JSON, sonst HTML-Redirect.

    ``value`` ist die Antwort für JSON. ``ok_redirect`` ist die URL bei Erfolg
    via klassischem Form-POST.
    """
    if request.accept_mimetypes.best == "application/json":
        return jsonify(ok=True, **value)
    if flash_msg:
        try:
            flash(flash_msg)
        except Exception:
            pass
    return redirect(ok_redirect)


def _err_fallback(err_code, redirect_to, flash_key=None):
    if request.accept_mimetypes.best == "application/json":
        return jsonify(ok=False, error=err_code), 400
    try:
        if flash_key:
            flash(flash_key, "error")
    except Exception:
        pass
    return redirect(redirect_to)


def _ensure_member_or_403(group_id, user_id):
    if not is_group_member(group_id, user_id):
        abort(403)


# ─── Index ─────────────────────────────────────────────────────────────────

@bp.route("/", methods=["GET"])
def index():
    user = _require_login()
    banned, _ = is_banned(user["id"])
    return render_template(
        "groupchat/index.html",
        user=user["name"],
        groups=list_my_groups(user["id"]),
        active_id=None,
        active=None,
        members=[],
        messages=[],
        owned_max_id=0,
        banned=banned,
        search_results=None,
        search_query="",
    )


# ─── Create group ──────────────────────────────────────────────────────────

@bp.route("/create", methods=["POST"])
def create():
    user = _require_login()
    name = (request.form.get("name", "") or "").strip()
    description = (request.form.get("description", "") or "").strip()
    try:
        gid = create_group(user["id"], name, description=description)
    except GroupError as exc:
        return _err_fallback(
            "invalid", "/groups", getattr(exc, "args", [str(exc)])[0])
    except Exception as exc:  # noqa: BLE001
        return _err_fallback("unknown", "/groups", str(exc))
    return _send_fallback({"group_id": gid}, f"/groups/{gid}")


# ─── View group + poll ─────────────────────────────────────────────────────

@bp.route("/<int:group_id>", methods=["GET"])
def view(group_id):
    user = _require_login()
    _ensure_member_or_403(group_id, user["id"])
    grp = get_group(group_id)
    if not grp:
        abort(404)

    mark_group_read(group_id, user["id"])
    messages = get_group_messages(group_id, after_id=0, limit=500)
    last_id = max((m["id"] for m in messages), default=0)
    members = list_group_members(group_id)
    friends = [f for f in get_friends(user["id"])
               if f["id"] != user["id"]
               and not any(m["user_id"] == f["id"] for m in members)]

    banned, _ = is_banned(user["id"])

    return render_template(
        "groupchat/index.html",
        user=user["name"],
        groups=list_my_groups(user["id"]),
        active_id=group_id,
        active=grp,
        active_members=members,
        messages=messages,
        owned_max_id=last_id,
        addable_friends=friends,
        banned=banned,
        search_results=None,
        search_query="",
    )


@bp.route("/<int:group_id>/poll", methods=["GET"])
def poll(group_id):
    user = _require_login()
    _ensure_member_or_403(group_id, user["id"])

    try:
        after_id = int(request.args.get("after_id") or 0)
    except (TypeError, ValueError):
        after_id = 0

    messages = get_group_messages(group_id, after_id=after_id, limit=200)
    new_for_me = any(m["from"] != user["id"] for m in messages)
    if new_for_me:
        mark_group_read(group_id, user["id"])

    return jsonify(
        messages=messages,
        groups=list_my_groups(user["id"]),
    )


# ─── Send ─────────────────────────────────────────────────────────────────

@bp.route("/<int:group_id>/send", methods=["POST"])
def send(group_id):
    user = _require_login()
    banned, _ = is_banned(user["id"])
    if banned:
        return _err_fallback("banned", f"/groups/{group_id}",
                             "Du bist gebannt und kannst nicht senden.")
    _ensure_member_or_403(group_id, user["id"])

    text = (request.form.get("message", "") or "").strip()
    if not text:
        return _err_fallback("empty", f"/groups/{group_id}",
                             "Leere Nachricht.")
    if len(text) > GROUP_MSG_MAX:
        return _err_fallback("too_long", f"/groups/{group_id}",
                             f"Maximal {GROUP_MSG_MAX} Zeichen.")

    try:
        msg_id = send_group_message(group_id, user["id"], text)
    except GroupError as exc:
        return _err_fallback("group_error", f"/groups/{group_id}",
                             getattr(exc, "args", [str(exc)])[0])

    if request.accept_mimetypes.best == "application/json":
        return jsonify(
            ok=True,
            id=msg_id,
            message={
                "id": msg_id,
                "from": user["id"],
                "to": None,
                "message": text,
                "time": __import__("time").time(),
            },
        )
    return redirect(f"/groups/{group_id}")


# ─── Member management ────────────────────────────────────────────────────

@bp.route("/<int:group_id>/members/add", methods=["POST"])
def add_member_route(group_id):
    user = _require_login()
    _ensure_member_or_403(group_id, user["id"])

    target_name = (request.form.get("username", "") or "").strip()
    target = get_user_by_name(target_name)
    if not target:
        return _err_fallback("user_not_found", f"/groups/{group_id}",
                             f"Benutzer '{target_name}' nicht gefunden.")
    if target["id"] == user["id"]:
        return _err_fallback("self", f"/groups/{group_id}",
                             "Du bist bereits Mitglied.")

    # Nur akzeptierte Freunde einladen (verhindert Spam).
    if not are_friends(user["id"], target["id"]):
        return _err_fallback("not_friend", f"/groups/{group_id}",
                             "Du kannst nur Freunde hinzufügen.")

    try:
        added = add_group_member(group_id, target["id"])
    except GroupError as exc:
        return _err_fallback("group_full", f"/groups/{group_id}",
                             getattr(exc, "args", [str(exc)])[0])

    if added:
        return _send_fallback({"added": True}, f"/groups/{group_id}",
                              f"{target['name']} hinzugefügt.")
    return _send_fallback({"added": False}, f"/groups/{group_id}",
                          f"{target['name']} ist bereits Mitglied.")


@bp.route("/<int:group_id>/members/remove", methods=["POST"])
def remove_member_route(group_id):
    user = _require_login()
    grp = get_group(group_id)
    if not grp:
        abort(404)
    # Nur Owner darf Mitglieder entfernen.
    if grp["owner_id"] != user["id"]:
        abort(403)

    try:
        target_id = int(request.form.get("user_id", "0"))
    except (TypeError, ValueError):
        return _err_fallback("bad_input", f"/groups/{group_id}", "Ungültige Eingabe.")
    if target_id == user["id"]:
        return _err_fallback("self_remove", f"/groups/{group_id}",
                             "Owner können sich nicht selbst entfernen.")

    ok = remove_group_member(group_id, target_id)
    if not ok:
        return _err_fallback("not_found", f"/groups/{group_id}",
                             "Mitglied nicht gefunden.")
    return _send_fallback({"removed": True}, f"/groups/{group_id}",
                          "Mitglied entfernt.")


@bp.route("/<int:group_id>/leave", methods=["POST"])
def leave_route(group_id):
    user = _require_login()
    try:
        leave_group(group_id, user["id"])
    except GroupError as exc:
        return _err_fallback("cannot_leave", "/groups",
                             getattr(exc, "args", [str(exc)])[0])
    except Exception:
        return _err_fallback("unknown", "/groups")
    return _send_fallback({"left": True}, "/groups", "Gruppe verlassen.")


@bp.route("/<int:group_id>/delete", methods=["POST"])
def delete_route(group_id):
    user = _require_login()
    grp = get_group(group_id)
    if not grp:
        abort(404)
    if grp["owner_id"] != user["id"]:
        abort(403)

    if request.form.get("confirm") != "1":
        return _err_fallback("confirm_required", f"/groups/{group_id}",
                             "Bitte bestätige.")

    delete_group(group_id, user["id"])
    return _send_fallback({"deleted": True}, "/groups", "Gruppe gelöscht.")


# ─── Search within group (LIKE) ───────────────────────────────────────────


@bp.route("/<int:group_id>/search", methods=["GET"])
def search(group_id):
    user = _require_login()
    _ensure_member_or_403(group_id, user["id"])
    q = (request.args.get("q", "") or "").strip()[:200]
    if not q:
        return redirect(f"/groups/{group_id}")
    results = _search_group_messages(user["id"], group_id, q, limit=100)
    grp = get_group(group_id)
    members = list_group_members(group_id)
    return render_template(
        "groupchat/index.html",
        user=user["name"],
        groups=list_my_groups(user["id"]),
        active_id=group_id,
        active=grp,
        active_members=members,
        messages=[],
        owned_max_id=0,
        search_results=results,
        search_query=q,
        banned=False,
    )


def _search_group_messages(user_id, group_id, query, limit=100):
    """LIKE-basiert: filtert group_dm-Nachrichten in dieser Gruppe,
    die an diesen user_id gerichtet sind (oder wo der user_id der Sender ist).
    """
    raw = (query or "").strip()
    if not raw:
        return []
    safe = (
        raw.replace("\\", "\\\\")
           .replace("%", "\\%")
           .replace("_", "\\_")
    )
    pattern = f"%{safe}%"
    conn = _conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, user_id, sender_id, message, created_at
            FROM notifications
            WHERE type = ? AND group_id = ?
              AND (user_id = ? OR sender_id = ?)
              AND message LIKE ? ESCAPE '\\'
            ORDER BY id ASC
            LIMIT ?
            """,
            (GROUP_DM, group_id, user_id, user_id, pattern, int(limit)),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]

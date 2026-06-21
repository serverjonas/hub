import os
import sqlite3
import time
from flask import request, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from toolbox.files import *

def check_pw(username, password):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        "SELECT password_hash FROM users WHERE user_name = ?",
        (username,)
    )
    row = cur.fetchone()

    conn.close()

    # User existiert nicht
    if row is None:
        return False

    stored_hash = row[0]

    # Passwort prüfen
    return check_password_hash(stored_hash, password)

def create_user(username, password):
    password_hash = generate_password_hash(password)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO users (user_name, password_hash, admin, vip, mod)
        VALUES (?, ?, 0, 0, 0)
    """,
        (username, password_hash),
    )

    conn.commit()
    conn.close()

def is_user_active(user_id):
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT active FROM user_activation WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()

    # Kein Eintrag = alter Account → erlauben
    if row is None:
        return True

    return row[0] == 1



def get_infos(user_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.execute("""
        SELECT user_name, email, email_active,
               admin, vip, mod
        FROM users
        WHERE user_id = ?
    """, (user_id,))

    row = cur.fetchone()
    con.close()

    if row is None:
        return None

    return {
        "user_name": row[0],
        "email": row[1],
        "email_active": bool(row[2]),
        "admin": bool(row[3]),
        "vip": bool(row[4]),
        "mod": bool(row[5]),
    }

def is_banned(user_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT reason, expires_at FROM ban WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    con.close()

    if row is None:
        return False, None

    reason, expires_at = row

    if expires_at is not None and expires_at < int(time.time()):
        return False, None  # Ban abgelaufen

    return True, reason

def get_name(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT user_name
        FROM users
        WHERE user_id = ?
    """,
        (user_id,),
    )

    row = cur.fetchone()
    conn.close()

    if not row:
        return None  # User existiert nicht

    return row[0]  # username


def get_current_user():
    session_id = request.cookies.get("session_id")
    if not session_id:
        return None

    now = int(time.time())

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT users.user_id, users.user_name
        FROM sessions
        JOIN users ON sessions.user_id = users.user_id
        WHERE sessions.session_id = ?
          AND sessions.expires_at > ?
    """,
        (session_id, now),
    )

    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    # Rückgabe als Dictionary: sowohl ID als auch Name
    return {"id": row[0], "name": row[1]}


def get_friends(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT u.user_id FROM users u
        JOIN friendships f ON (
            (f.user_id = ? AND f.friend_id = u.user_id) OR
            (f.friend_id = ? AND f.user_id = u.user_id)
        )
        WHERE f.status = 'accepted'
    """,
        (user_id, user_id),
    )
    rows = cur.fetchall()
    conn.close()
    return [row[0] for row in rows]


# ─── E-Mail-Verifizierung ────────────────────────────────────────────────
import secrets

EMAIL_RESEND_COOLDOWN = 60            # Sekunden
EMAIL_TOKEN_TTL = 60 * 60 * 24        # 24 Stunden


def get_user_email_status(user_id):
    """Returns (email, active) for a user or (None, False) when missing."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT email, email_active FROM users WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        return None, False
    email, active = row[0], bool(row[1])
    return (email or None), active


def is_email_verified(user_id):
    """True wenn eine E-Mail hinterlegt UND aktiviert ist."""
    email, active = get_user_email_status(user_id)
    return bool(email) and active


def set_user_email(user_id, email):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET email = ?, email_active = 0 WHERE user_id = ?",
        (email, user_id),
    )
    conn.commit()
    conn.close()


def mark_user_email_active(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET email_active = 1 WHERE user_id = ?",
        (user_id,),
    )
    conn.commit()
    conn.close()


def record_email_sent(user_id):
    """Setzt den Cooldown-Zeitstempel."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET last_email_sent_at = ? WHERE user_id = ?",
        (int(time.time()), user_id),
    )
    conn.commit()
    conn.close()


def email_resend_cooldown_remaining(user_id):
    """Gibt die verbleibenden Sekunden bis zum nächsten Versand zurück (0 wenn ok)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT last_email_sent_at FROM users WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row or not row[0]:
        return 0
    elapsed = int(time.time()) - int(row[0])
    return max(0, EMAIL_RESEND_COOLDOWN - elapsed)


def create_email_verification(user_id, email):
    """Erzeugt ein neues Token; alle bisherigen offenen Tokens werden gelöscht."""
    now = int(time.time())
    expires = now + EMAIL_TOKEN_TTL
    token = secrets.token_urlsafe(32)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM email_verifications WHERE user_id = ?",
        (user_id,),
    )
    cur.execute(
        """
        INSERT INTO email_verifications (token, user_id, email, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?)
    """,
        (token, user_id, email, expires, now),
    )
    conn.commit()
    conn.close()
    return token, expires


def consume_email_verification(token):
    """Liefert (user_id, email) wenn gültig; sonst None. Tokens werden gelöscht."""
    if not token:
        return None
    now = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT user_id, email, expires_at
        FROM email_verifications
        WHERE token = ?
    """,
        (token,),
    )
    row = cur.fetchone()
    if row is None:
        conn.close()
        return None
    user_id, email, expires_at = row
    cur.execute("DELETE FROM email_verifications WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    if expires_at < now:
        return None
    return user_id, email


def mask_email(email):
    """Maskiert die lokale Hälfte einer E-Mail: 'j***@example.com'."""
    if not email or "@" not in email:
        return email or ""
    local, _, domain = email.partition("@")
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


# ─── Mod-Panel: Rollen, Cooldowns, Vorschläge ────────────────────────────────

from flask import abort as _abort


MOD_COOLDOWN_SECONDS = 7 * 24 * 60 * 60   # 7 Tage
VALID_SUGGESTION_ROLES = ("admin", "vip", "mod")


def is_mod(user_id) -> bool:
    """True wenn der Nutzer die Mod-Rolle hat."""
    infos = get_infos(user_id) if user_id else None
    return bool(infos and infos.get("mod"))


def is_admin_or_mod(user_id) -> bool:
    """True wenn der Nutzer Admin ODER Mod ist."""
    infos = get_infos(user_id) if user_id else None
    return bool(infos and (infos.get("admin") or infos.get("mod")))


def require_admin_or_mod():
    """before_request hook: prüft Login und Admin/Mod-Rolle."""
    user = get_current_user()
    if user is None:
        _abort(401)
    if not is_admin_or_mod(user["id"]):
        _abort(403)


def get_mod_cooldown(mod_id):
    """Gibt (expires_at, reason) des aktuellen Cooldowns zurück oder (None, None)."""
    if not mod_id:
        return None, None
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT expires_at, reason FROM mod_cooldowns WHERE mod_id = ?",
        (mod_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None, None
    return row[0], (row[1] or "")


def has_active_cooldown(mod_id) -> bool:
    """True wenn für `mod_id` aktuell ein Ban-Cooldown läuft."""
    expires_at, _ = get_mod_cooldown(mod_id)
    return bool(expires_at and expires_at > int(time.time()))


def get_cooldown_remaining(mod_id) -> int:
    """Sekunden bis der Cooldown abläuft. 0 wenn keiner aktiv."""
    expires_at, _ = get_mod_cooldown(mod_id)
    if not expires_at:
        return 0
    return max(0, expires_at - int(time.time()))


def record_mod_cooldown(mod_id, reason: str = "") -> int:
    """Setzt/reaktiviert den 7-Tage-Cooldown für einen Mod."""
    now = int(time.time())
    expires_at = now + MOD_COOLDOWN_SECONDS
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO mod_cooldowns (mod_id, starts_at, expires_at, reason)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(mod_id) DO UPDATE SET
            starts_at  = excluded.starts_at,
            expires_at = excluded.expires_at,
            reason     = excluded.reason
        """,
        (mod_id, now, expires_at, reason or ""),
    )
    conn.commit()
    conn.close()
    return expires_at


def create_permission_suggestion(mod_id, target_user_id, role, value, comment=""):
    """
    Erzeugt einen Rollen-Vorschlag. Vorhandene pending-Vorschläge für dieselbe
    target+role-Kombination werden ersetzt, damit Admins nicht mit konflikt-
    identischen Einträgen zugespammt werden.

    Verläss auf den teilindizierten UNIQUE-Index uniq_pending_suggestion für
    Race-Schutz bei gleichzeitigen POSTs.
    """
    if role not in VALID_SUGGESTION_ROLES:
        raise ValueError("invalid role")
    if value not in (0, 1):
        raise ValueError("invalid value")
    now = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            DELETE FROM permission_suggestions
            WHERE target_user_id = ? AND role = ? AND status = 'pending'
            """,
            (target_user_id, role),
        )
        cur.execute(
            """
            INSERT INTO permission_suggestions
                (mod_id, target_user_id, role, value, status, comment, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """,
            (mod_id, target_user_id, role, value, comment or "", now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Concurrent POST hat bereits einen pending-Eintrag erzeugt; nichts tun.
        conn.rollback()
    finally:
        conn.close()


def list_pending_suggestions():
    """Hängt alle offenen Vorschläge inklusive Mod- und Target-Namen ab."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.id, s.mod_id, s.target_user_id, s.role, s.value,
               s.comment, s.created_at,
               m.user_name   AS mod_name,
               t.user_name   AS target_name,
               t.admin, t.vip, t.mod
        FROM permission_suggestions s
        JOIN users m ON m.user_id = s.mod_id
        JOIN users t ON t.user_id = s.target_user_id
        WHERE s.status = 'pending'
        ORDER BY s.created_at ASC
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id":              r[0],
            "mod_id":          r[1],
            "target_user_id":  r[2],
            "role":            r[3],
            "value":           bool(r[4]),
            "comment":         r[5],
            "created_at":      r[6],
            "mod_name":        r[7],
            "target_name":     r[8],
            "target_admin":    bool(r[9]),
            "target_vip":      bool(r[10]),
            "target_mod":      bool(r[11]),
        }
        for r in rows
    ]


def review_permission_suggestion(suggestion_id, admin_id, decision):
    """
    `decision` ist 'approved' oder 'rejected'. Bei 'approved' wird die Rolle
    sofort auf den Ziel-User angewendet.
    """
    if decision not in ("approved", "rejected"):
        raise ValueError("invalid decision")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        "SELECT target_user_id, role, value, status FROM permission_suggestions WHERE id = ?",
        (suggestion_id,),
    )
    row = cur.fetchone()
    if row is None or row[3] != "pending":
        conn.close()
        return False

    target_user_id, role, value = row[0], row[1], row[2]
    now = int(time.time())

    if decision == "approved":
        cur.execute(
            f"UPDATE users SET {role} = ? WHERE user_id = ?",
            (value, target_user_id),
        )

    cur.execute(
        """
        UPDATE permission_suggestions
        SET status = ?, reviewed_by = ?, reviewed_at = ?
        WHERE id = ?
        """,
        (decision, admin_id, now, suggestion_id),
    )
    conn.commit()
    conn.close()
    return True

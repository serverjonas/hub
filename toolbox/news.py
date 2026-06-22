import sqlite3
import time
from toolbox.files import *


# ─── Single-target notifications / DMs ─────────────────────────────────────

def send_dm(from_user, to_user, message):
    return create_notification(
        user_id=to_user,
        message=message,
        type="dm",
        sender_id=from_user,
    )


def get_last_message_id(user_a, user_b):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT MAX(id)
        FROM notifications
        WHERE (
            user_id = ? AND sender_id = ?
        ) OR (
            user_id = ? AND sender_id = ?
        )
        """,
        (user_a, user_b, user_b, user_a),
    )

    row = cur.fetchone()
    conn.close()

    return row[0] if row and row[0] else 0


def get_chat_messages(user_a, user_b, after_id=0):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM notifications
        WHERE (
            user_id = ? AND sender_id = ?
        ) OR (
            user_id = ? AND sender_id = ?
        )
        AND id > ?
        ORDER BY id ASC
        """,
        (user_a, user_b, user_b, user_a, after_id),
    )

    rows = cur.fetchall()
    conn.close()

    return [
        {
            "id": r["id"],
            "from": r["sender_id"],
            "to": r["user_id"],
            "message": r["message"],
            "created_at": r["created_at"],
            "type": r["type"],
        }
        for r in rows
    ]


def search_chat_messages(user_a, user_b, query, limit=50):
    """Volltext-Suche (LIKE-basiert) in einer 1:1-Konversation.

    Liefert die letzten ``limit`` Treffer in id-ASC-Reihenfolge, damit die
    Ergebnisse stabil durchgeblättert werden können. ``%`` und ``_`` im Query
    werden mit Escape-Zeichen versehen, damit sie nicht als Wildcards
    interpretiert werden.
    """
    if not query or not query.strip():
        return []
    # Trim + Escape.
    raw = query.strip()
    if len(raw) > 200:
        raw = raw[:200]
    safe = (
        raw.replace("\\", "\\\\")
           .replace("%", "\\%")
           .replace("_", "\\_")
    )
    pattern = f"%{safe}%"

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, user_id, sender_id, message, created_at
            FROM notifications
            WHERE type = 'dm'
              AND (
                  (user_id = ? AND sender_id = ?) OR
                  (user_id = ? AND sender_id = ?)
              )
              AND message LIKE ? ESCAPE '\\'
            ORDER BY id ASC
            LIMIT ?
            """,
            (user_a, user_b, user_b, user_a, pattern, int(limit)),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "id": r["id"],
            "from": r["sender_id"],
            "to": r["user_id"],
            "message": r["message"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def get_notifications(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM notifications
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        (user_id,),
    )

    rows = cursor.fetchall()
    conn.close()

    notifications = []
    for row in rows:
        notifications.append({
            "id": row["id"],
            "sender": row["sender_id"],
            "type": row["type"],
            "message": row["message"],
            "read": bool(row["read"]),
            "created_at": row["created_at"],
        })
    return notifications


def create_notification(user_id, message, type="system", sender_id=None, group_id=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO notifications (
            user_id, sender_id, type, message, read, created_at, group_id
        )
        VALUES (?, ?, ?, ?, 0, ?, ?)
        """,
        (user_id, sender_id, type, message, int(time.time()), group_id),
    )
    notif_id = cur.lastrowid
    conn.commit()
    conn.close()
    return notif_id


# ─── Group chat ─────────────────────────────────────────────────────────────

GROUP_DM = "group_dm"
GROUP_NAME_MIN, GROUP_NAME_MAX = 1, 50
GROUP_DESC_MAX = 200
GROUP_MSG_MAX = 2000
GROUP_MAX_MEMBERS = 20


class GroupError(ValueError):
    """Validation / permission error raised by group helpers."""


def create_group(owner_id, name, description=""):
    """Legt eine neue Gruppe an; der Owner wird automatisch Mitglied.

    Liefert die neue ``group_id``. Wirft ``GroupError`` bei ungültigen Daten.
    """
    name = (name or "").strip()
    description = (description or "").strip()
    if not (GROUP_NAME_MIN <= len(name) <= GROUP_NAME_MAX):
        raise GroupError(
            f"Gruppenname muss {GROUP_NAME_MIN}–{GROUP_NAME_MAX} Zeichen lang sein."
        )
    if len(description) > GROUP_DESC_MAX:
        raise GroupError(
            f"Beschreibung ist zu lang ({len(description)} > {GROUP_DESC_MAX})."
        )
    if not owner_id:
        raise GroupError("Kein Benutzer angemeldet.")

    now = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO chat_groups (name, description, owner_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (name, description, owner_id, now),
    )
    gid = cur.lastrowid
    cur.execute(
        """
        INSERT INTO chat_group_members (group_id, user_id, joined_at, role)
        VALUES (?, ?, ?, 'owner')
        """,
        (gid, owner_id, now),
    )
    conn.commit()
    conn.close()
    return gid


def get_group(group_id):
    """Liefert das Gruppen-Dict oder ``None``."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT g.group_id, g.name, g.description, g.owner_id, g.created_at,
               u.user_name AS owner_name
        FROM chat_groups g
        JOIN users u ON u.user_id = g.owner_id
        WHERE g.group_id = ?
        """,
        (group_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row["group_id"],
        "name": row["name"],
        "description": row["description"],
        "owner_id": row["owner_id"],
        "owner_name": row["owner_name"],
        "created_at": int(row["created_at"]),
    }


def list_my_groups(user_id):
    """Alle Gruppen, in denen ``user_id`` Mitglied ist, mit Last-Message-Snippet."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT g.group_id, g.name, g.description, g.owner_id, g.created_at,
               m.joined_at
        FROM chat_groups g
        JOIN chat_group_members m ON m.group_id = g.group_id
        WHERE m.user_id = ?
        ORDER BY g.created_at DESC
        """,
        (user_id,),
    )
    rows = cur.fetchall()
    if not rows:
        conn.close()
        return []

    groups = []
    for row in rows:
        # letzte Nachricht über alle Mitglieder lesen
        cur.execute(
            """
            SELECT message, created_at, sender_id, id
            FROM notifications
            WHERE group_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (row["group_id"],),
        )
        last = cur.fetchone()
        # unread für MICH
        cur.execute(
            """
            SELECT COUNT(*)
            FROM notifications
            WHERE group_id = ? AND user_id = ? AND read = 0
            """,
            (row["group_id"], user_id),
        )
        unread = cur.fetchone()[0] or 0
        groups.append({
            "id": row["group_id"],
            "name": row["name"],
            "description": row["description"],
            "owner_id": row["owner_id"],
            "created_at": int(row["created_at"]),
            "joined_at": int(row["joined_at"]),
            "last_message": last["message"] if last else "",
            "last_time": int(last["created_at"]) if last else 0,
            "last_sender_id": last["sender_id"] if last else None,
            "last_id": int(last["id"]) if last else 0,
            "unread": int(unread or 0),
        })
    conn.close()
    # neueste Aktivität zuerst
    groups.sort(
        key=lambda g: (-g["last_time"] if g["last_time"] else -g["created_at"])
    )
    return groups


def list_group_members(group_id):
    """Liste der Mitglieder einer Gruppe."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT m.user_id, m.joined_at, m.role,
               u.user_name, u.admin, u.vip, u.mod
        FROM chat_group_members m
        JOIN users u ON u.user_id = m.user_id
        WHERE m.group_id = ?
        ORDER BY (m.role = 'owner') DESC, m.joined_at ASC
        """,
        (group_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "user_id": r["user_id"],
            "user_name": r["user_name"],
            "joined_at": int(r["joined_at"]),
            "role": r["role"],
            "admin": bool(r["admin"]),
            "vip": bool(r["vip"]),
            "mod": bool(r["mod"]),
        }
        for r in rows
    ]


def is_group_member(group_id, user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM chat_group_members WHERE group_id = ? AND user_id = ?",
        (group_id, user_id),
    )
    ok = cur.fetchone() is not None
    conn.close()
    return ok


def add_group_member(group_id, user_id):
    """Fügt ``user_id`` zur Gruppe hinzu. Idempotent."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Cap prüfen.
    cur.execute(
        "SELECT COUNT(*) FROM chat_group_members WHERE group_id = ?",
        (group_id,),
    )
    count = cur.fetchone()[0] or 0
    if count >= GROUP_MAX_MEMBERS:
        conn.close()
        raise GroupError(f"Gruppe ist voll ({GROUP_MAX_MEMBERS} Mitglieder max).")

    cur.execute(
        """
        INSERT OR IGNORE INTO chat_group_members (group_id, user_id, joined_at, role)
        VALUES (?, ?, ?, 'member')
        """,
        (group_id, user_id, int(time.time())),
    )
    conn.commit()
    changed = cur.rowcount or 0
    conn.close()
    return changed > 0


def remove_group_member(group_id, user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM chat_group_members
        WHERE group_id = ? AND user_id = ? AND role != 'owner'
        """,
        (group_id, user_id),
    )
    deleted = cur.rowcount or 0
    conn.commit()
    conn.close()
    return deleted > 0


def leave_group(group_id, user_id):
    """Mitglied verlässt die Gruppe. Owner darf nicht leave (muss vorher löschen)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM chat_group_members
        WHERE group_id = ? AND user_id = ? AND role != 'owner'
        """,
        (group_id, user_id),
    )
    deleted = cur.rowcount or 0
    conn.commit()
    conn.close()
    if not deleted:
        raise GroupError("Owner können die Gruppe nicht verlassen – bitte löschen.")
    return True


def delete_group(group_id, requester_id):
    """Löscht eine Gruppe + Mitgliedschaften. Nur der Owner darf das."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT owner_id FROM chat_groups WHERE group_id = ?",
        (group_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    if row[0] != requester_id:
        conn.close()
        raise GroupError("Nur der Owner darf die Gruppe löschen.")
    cur.execute("DELETE FROM chat_group_members WHERE group_id = ?", (group_id,))
    cur.execute("DELETE FROM chat_groups WHERE group_id = ?", (group_id,))
    conn.commit()
    conn.close()
    return True


def send_group_message(group_id, sender_id, message):
    """Broadcastet eine Nachricht in eine Gruppe.

    INSERTet eine notification pro Mitglied (inkl. Sender). Liefert die
    ID der zuletzt eingefügten Zeile (Sender's Kopie).
    """
    text = (message or "").strip()
    if not text:
        raise GroupError("Leere Nachricht.")
    if len(text) > GROUP_MSG_MAX:
        raise GroupError(f"Nachricht zu lang (max {GROUP_MSG_MAX}).")
    if not is_group_member(group_id, sender_id):
        raise GroupError("Du bist kein Mitglied dieser Gruppe.")

    members = [m["user_id"] for m in list_group_members(group_id)]
    if not members:
        raise GroupError("Gruppe hat keine Mitglieder.")

    last_id = None
    for uid in members:
        nid = create_notification(
            user_id=uid,
            message=text,
            type=GROUP_DM,
            sender_id=sender_id,
            group_id=group_id,
        )
        last_id = nid
    return last_id


def get_group_messages(group_id, after_id=0, limit=200):
    """Liefert alle Broadcast-Nachrichten der Gruppe, ASC geordnet."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, user_id, sender_id, message, created_at
        FROM notifications
        WHERE type = ? AND group_id = ? AND id > ?
        ORDER BY id ASC
        LIMIT ?
        """,
        (GROUP_DM, group_id, after_id, int(limit)),
    )
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "from": r["sender_id"],
            "to": r["user_id"],
            "message": r["message"],
            "created_at": int(r["created_at"]),
        }
        for r in rows
    ]


def mark_group_read(group_id, user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE notifications
        SET read = 1
        WHERE type = ? AND group_id = ? AND user_id = ? AND read = 0
        """,
        (GROUP_DM, group_id, user_id),
    )
    conn.commit()
    conn.close()

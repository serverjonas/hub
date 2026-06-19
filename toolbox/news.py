import sqlite3
def send_dm(from_user, to_user, message):
    return create_notification(
        user_id=to_user,
        message=message,
        type="dm",
        sender_id=from_user
    )


def get_last_message_id(user_a, user_b):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT MAX(id)
        FROM notifications
        WHERE (
            user_id = ? AND sender_id = ?
        ) OR (
            user_id = ? AND sender_id = ?
        )
    """, (user_a, user_b, user_b, user_a))

    row = cur.fetchone()
    conn.close()

    return row[0] if row and row[0] else 0


def get_chat_messages(user_a, user_b, after_id=0):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM notifications
        WHERE (
            user_id = ? AND sender_id = ?
        ) OR (
            user_id = ? AND sender_id = ?
        )
        AND id > ?
        ORDER BY id ASC
    """, (user_a, user_b, user_b, user_a, after_id))

    rows = cur.fetchall()
    conn.close()

    return [
        {
            "id": r["id"],
            "from": r["sender_id"],
            "to": r["user_id"],
            "message": r["message"],
            "created_at": r["created_at"],
            "type": r["type"]
        }
        for r in rows
    ]

def get_notifications(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # erlaubt dict-artigen Zugriff
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM notifications
        WHERE user_id = ?
        ORDER BY created_at DESC
    """, (user_id,))

    rows = cursor.fetchall()
    conn.close()

    notifications = []

    for row in rows:
        notifications.append({
            "id": row["id"], 
            "sender": row["sender_id"],   # kannst du später auf Username mappen
            "type": row["type"],
            "message": row["message"],
            "read": bool(row["read"]),
            "created_at": row["created_at"]
        })

    return notifications


def create_notification(user_id, message, type="system", sender_id=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO notifications (
            user_id,
            sender_id,
            type,
            message,
            read,
            created_at
        ) VALUES (?, ?, ?, ?, 0, ?)
    """, (
        user_id,
        sender_id,
        type,
        message,
        int(time.time())
    ))

    conn.commit()
    conn.close()

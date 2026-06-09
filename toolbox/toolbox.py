# toolbox.py
import os
import tomllib
import sqlite3
import time
from flask import request, make_response
from werkzeug.security import generate_password_hash, check_password_hash

BASE_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = BASE_PATH
DB_PATH = os.path.join(BASE_PATH, "users.db")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
DATA_DIR = os.path.join(BASE_DIR, "data")





def load_configs(config_dir: str) -> dict:
    config = {}

    for root, _, files in os.walk(config_dir):
        rel_path = os.path.relpath(root, config_dir)

        current = config
        if rel_path != ".":
            for part in rel_path.split(os.sep):
                current = current.setdefault(part, {})

        for file in files:
            if not file.endswith(".toml"):
                continue

            name = os.path.splitext(file)[0]

            with open(os.path.join(root, file), "rb") as f:
                current[name] = tomllib.load(f)

    return config


config = load_configs(CONFIG_DIR)


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

def send_dm(from_user, to_user, message):
    return create_notification(
        user_id=to_user,
        message=message,
        type="dm",
        sender_id=from_user
    )

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






if __name__ == "__main__":
    print(
        "dies ist die ToolBox man soll sie nur importiren nic	ht direckt asuführen"
    )

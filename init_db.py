import sqlite3
import os

DB_PATH = "/var/www/serverjonas-hub/users.db"

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.executescript("""

    -- Benutzer
    CREATE TABLE IF NOT EXISTS users (
        user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        user_name     TEXT    NOT NULL UNIQUE,
        password_hash TEXT    NOT NULL,
        admin         INTEGER NOT NULL DEFAULT 0,
        vip           INTEGER NOT NULL DEFAULT 0,
        mod           INTEGER NOT NULL DEFAULT 0
    );

    -- Login-Sessions
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT    PRIMARY KEY,
        user_id    INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    );

    -- Benachrichtigungen
    CREATE TABLE IF NOT EXISTS notifications (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        sender_id  INTEGER,
        type       TEXT    NOT NULL DEFAULT 'system',
        message    TEXT    NOT NULL,
        read       INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    );

    -- Account-Aktivierung
    CREATE TABLE IF NOT EXISTS user_activation (
        user_id INTEGER PRIMARY KEY,
        active  INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    );

    -- Bans
    CREATE TABLE IF NOT EXISTS ban (
        user_id    INTEGER PRIMARY KEY,
        reason     TEXT,
        expires_at INTEGER,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    );

    -- Freundschaften
    CREATE TABLE IF NOT EXISTS friendships (
        user_id   INTEGER NOT NULL,
        friend_id INTEGER NOT NULL,
        status    TEXT    NOT NULL DEFAULT 'pending',
        PRIMARY KEY (user_id, friend_id),
        FOREIGN KEY (user_id)   REFERENCES users(user_id),
        FOREIGN KEY (friend_id) REFERENCES users(user_id)
    );

""")

conn.commit()
conn.close()
print("✅ Datenbank erfolgreich initialisiert")

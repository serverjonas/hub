import os
import sqlite3
import time

DB_PATH = "./users.db"


def _existing_columns(cur, table):
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _ensure_column(cur, table, column, definition):
    """Adds a column to an existing table if it isn't already present."""
    if column in _existing_columns(cur, table):
        return
    cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    print(f"  ➕ {table}.{column} ({definition})")


conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.executescript(
    """

    -- Benutzer
    CREATE TABLE IF NOT EXISTS users (
        user_id            INTEGER PRIMARY KEY AUTOINCREMENT,
        user_name          TEXT    NOT NULL UNIQUE,
        password_hash      TEXT    NOT NULL,
        admin              INTEGER NOT NULL DEFAULT 0,
        vip                INTEGER NOT NULL DEFAULT 0,
        mod                INTEGER NOT NULL DEFAULT 0,
        email              TEXT,
        email_active       INTEGER NOT NULL DEFAULT 0,
        last_email_sent_at INTEGER
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

    -- E-Mail-Verifizierungs-Tokens
    CREATE TABLE IF NOT EXISTS email_verifications (
        token      TEXT    PRIMARY KEY,
        user_id    INTEGER NOT NULL,
        email      TEXT    NOT NULL,
        expires_at INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    );

    -- Mod-Ban-Cooldown: ein Mod darf 7 Tage lang niemanden bannen,
    -- nachdem ein Admin einen seiner Bans aufgehoben hat.
    CREATE TABLE IF NOT EXISTS mod_cooldowns (
        mod_id     INTEGER PRIMARY KEY,
        starts_at  INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        reason     TEXT    NOT NULL DEFAULT '',
        FOREIGN KEY (mod_id) REFERENCES users(user_id)
    );

    -- Rollen-Änderungs-Vorschläge von Mods an Admins.
    CREATE TABLE IF NOT EXISTS permission_suggestions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        mod_id          INTEGER NOT NULL,
        target_user_id  INTEGER NOT NULL,
        role            TEXT    NOT NULL,
        value           INTEGER NOT NULL,
        status          TEXT    NOT NULL DEFAULT 'pending',
        comment         TEXT    NOT NULL DEFAULT '',
        created_at      INTEGER NOT NULL,
        reviewed_by     INTEGER,
        reviewed_at     INTEGER,
        FOREIGN KEY (mod_id)         REFERENCES users(user_id),
        FOREIGN KEY (target_user_id) REFERENCES users(user_id),
        FOREIGN KEY (reviewed_by)    REFERENCES users(user_id)
    );

    -- Pro (target, role) darf es höchstens einen offenen Vorschlag geben,
    -- damit gleichzeitige POSTs keine Duplikate erzeugen.
    CREATE UNIQUE INDEX IF NOT EXISTS uniq_pending_suggestion
        ON permission_suggestions(target_user_id, role)
        WHERE status = 'pending';

"""
)

# Idempotente Migrationen für bereits bestehende Datenbanken
_ensure_column(cur, "users", "email", "TEXT")
_ensure_column(cur, "users", "email_active", "INTEGER NOT NULL DEFAULT 0")
_ensure_column(cur, "users", "last_email_sent_at", "INTEGER")

# Mod-Panel: tracked who placed a ban so admins can apply cooldowns later.
_ensure_column(cur, "ban", "banned_by", "INTEGER")

# ─── Profile / Avatar / Groupchat-Migrationen ──────────────────────────
# Diese Spalten wurden mit dem Roll-out von Profilseiten, Avatar-Upload
# und dem Groupchat-Modul eingeführt. Wir migrieren idempotent, damit ein
# Re-Run von init_db.py auf einer bestehenden DB sicher ist.
_ensure_column(cur, "users", "bio", "TEXT NOT NULL DEFAULT ''")
_ensure_column(cur, "users", "avatar_path", "TEXT")
_ensure_column(cur, "users", "profile_visibility", "TEXT NOT NULL DEFAULT 'public'")
_ensure_column(cur, "users", "created_at", "INTEGER")

# Bestandsschutz: ältere Installationen haben u.U. ``created_at IS NULL``
# auf ``users``. Wir backfillen mit now(), damit ``get_profile_v()`` einen
# echten UNIX-Timestamp zurückgeben kann (join-date-Anzeige).
_cur_now = int(time.time())
cur.execute(
    "UPDATE users SET created_at = ? "
    "WHERE created_at IS NULL OR created_at = 0",
    (_cur_now,),
)

# Groupchat-Bridge-Spalte: ``notifications`` trägt für ``type='group_dm'``
# die zugehörige ``chat_groups.group_id``.
_ensure_column(cur, "notifications", "group_id", "INTEGER")

# Groupchat-Tabellen werden frisch angelegt, falls sie noch nicht
# existieren. ON DELETE CASCADE räumt beim Löschen einer Gruppe deren
# Mitglieder mit auf.
cur.executescript(
    """
    CREATE TABLE IF NOT EXISTS chat_groups (
        group_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT    NOT NULL,
        description  TEXT    NOT NULL DEFAULT '',
        owner_id     INTEGER NOT NULL,
        created_at   INTEGER NOT NULL,
        FOREIGN KEY (owner_id) REFERENCES users(user_id)
    );

    CREATE TABLE IF NOT EXISTS chat_group_members (
        group_id  INTEGER NOT NULL,
        user_id   INTEGER NOT NULL,
        joined_at INTEGER NOT NULL,
        role      TEXT    NOT NULL DEFAULT 'member',
        PRIMARY KEY (group_id, user_id),
        FOREIGN KEY (group_id) REFERENCES chat_groups(group_id) ON DELETE CASCADE,
        FOREIGN KEY (user_id)  REFERENCES users(user_id)
    );

    CREATE INDEX IF NOT EXISTS idx_group_members_user
        ON chat_group_members(user_id);
    """
)

conn.commit()
conn.close()
print("✅ Datenbank erfolgreich initialisiert")

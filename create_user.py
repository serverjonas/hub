#!/usr/bin/env python3
import sys
import sqlite3
import subprocess
from werkzeug.security import generate_password_hash
import os
import datetime
import traceback
# ======================
# KONFIGURATION
# ======================
DB_PATH = "/var/www/serverjonas-hub/users.db"
NEXTCLOUD_OCC = "/var/www/nextcloud/occ"
NEXTCLOUD_QUOTA = "50 G" #Limit an speicherplaz
PHP_BIN = "php"  # meist richtig
ERROR_LOG = "/var/www/serverjonas-hub/errors.log"
DEFAULT_ROLE = {
    "admin": 0,
    "vip": 0,
    "mod": 0
}

# ======================
# HILFSFUNKTIONEN
# ======================
def usage():
    print("Usage:")
    print("  create_user.py <username> <password>")
    sys.exit(1)

def log_error(msg: str, exc: Exception = None):
    """Schreibt eine Nachricht + optional Exception in errors.log"""
    with open(ERROR_LOG, "a", encoding="utf-8") as f:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{now}] {msg}\n")
        if exc:
            f.write(f"{type(exc).__name__}: {exc}\n")
            f.write(traceback.format_exc())
        f.write("\n")

def user_exists(username: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE user_name = ?", (username,))
    exists = cur.fetchone() is not None
    conn.close()
    return exists


def create_db_user(username: str, password: str):
    password_hash = generate_password_hash(password)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO users (user_name, password_hash, admin, vip, mod)
        VALUES (?, ?, ?, ?, ?)
    """, (
        username,
        password_hash,
        DEFAULT_ROLE["admin"],
        DEFAULT_ROLE["vip"],
        DEFAULT_ROLE["mod"]
    ))

    user_id = cur.lastrowid  # 👈 DAS ist wichtig

    # Activation-Eintrag erstellen
#    cur.execute("""
#        INSERT INTO user_activation (user_id, active, created_at)
#        VALUES (?, 0, strftime('%s','now'))
#    """, (user_id,))

    conn.commit()
    conn.close()


def create_nextcloud_user(username: str, password: str):
    cmd = [
        PHP_BIN, NEXTCLOUD_OCC,
        "user:add",
        "--password-from-env",
        username
    ]

    env = dict(os.environ)
    env["OC_PASS"] = password

    result = subprocess.run(
        cmd,
        env=env,
        capture_output=True,  # <- stdout/stderr abfangen
        text=True,
        encoding="utf-8"
    )

    # Fehler-Check
    if result.returncode != 0:
        raise Exception(f"Nextcloud-Error:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")

    quota_cmd = [
        PHP_BIN, NEXTCLOUD_OCC,
        "user:setting",
        username,
        "files",
        "quota",
        NEXTCLOUD_QUOTA
    ]

    subprocess.run(quota_cmd, check=True)



def create_nextcloud_user_old(username: str, password: str):
    cmd = [
        PHP_BIN,
        NEXTCLOUD_OCC,
        "user:add",
        "--password-from-env",
        "--quota",
        NEXTCLOUD_QUOTA,
        username
    ]

    env = dict(**os.environ)
    env["OC_PASS"] = password

    subprocess.run(
        cmd,
        env=env,
        check=True
    )


# ======================
# MAIN
# ======================
if len(sys.argv) != 3:
    usage()

username = sys.argv[1]
password = sys.argv[2]

if user_exists(username):
    print(f"❌ User '{username}' existiert bereits")
    sys.exit(2)

try:
    print("☁️ Erstelle Nextcloud‑Account …")
    create_nextcloud_user(username, password)
    print("✅ Nextcloud‑User erstellt")
    account = True
except Exception as e:
    print("Fehler beim erstellen des nextcloud accounts", e)
    log_error("Fehler beim Erstellen des Nextcloud-Accounts", e)
    account = True

#account = True

if account:
    print("🔐 Hashing password …")
    create_db_user(username, password)
    print("✅ User in users.db angelegt")
    print("🎉 Fertig!")


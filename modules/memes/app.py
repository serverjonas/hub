import os
import random
import secrets
import sqlite3
import time
from flask import (
    Blueprint,
    abort,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
    send_from_directory
)
from PIL import Image
from toolbox.toolbox import DB_PATH
from toolbox.files import check_storage, get_storage_info
from toolbox.user import get_current_user, get_name

MEMES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "memes")
MEMES_DB_PATH = os.path.join(MEMES_DIR, "memes.db")

bp = Blueprint("memes", __name__)

def init_db():
    os.makedirs(MEMES_DIR, exist_ok=True)
    conn = sqlite3.connect(MEMES_DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS memes (
            meme_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            title TEXT,
            created_at INTEGER NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS likes (
            meme_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (meme_id, user_id)
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_random_meme():
    conn = sqlite3.connect(MEMES_DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT meme_id, user_id, filename, title, created_at FROM memes")
    memes = cur.fetchall()
    conn.close()
    if not memes:
        return None
    return random.choice(memes)

@bp.route("/", methods=["GET"])
def memepage():
    user = get_current_user()
    if user is None:
        return abort(401)

    meme_row = get_random_meme()
    if meme_row is None:
        return render_template("meme_view.html", meme=None, user=user)

    meme_id = meme_row[0]
    conn = sqlite3.connect(MEMES_DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM likes WHERE meme_id = ?", (meme_id,))
    like_count = cur.fetchone()[0]
    cur.execute("SELECT 1 FROM likes WHERE meme_id = ? AND user_id = ?", (meme_id, user["id"]))
    is_liked = cur.fetchone() is not None
    conn.close()

    meme = {
        "meme_id": meme_id,
        "user_id": meme_row[1],
        "filename": meme_row[2],
        "title": meme_row[3],
        "created_at": meme_row[4],
        "author": get_name(meme_row[1]),
        "like_count": like_count,
        "is_liked": is_liked
    }
    return render_template("meme_view.html", meme=meme, user=user)

@bp.route("/api/like", methods=["POST"])
def like_api():
    user = get_current_user()
    if user is None:
        return {"error": "Unauthorized"}, 401
    
    data = request.json
    meme_id = data.get("meme_id")
    if not meme_id:
        return {"error": "Missing meme_id"}, 400
    
    conn = sqlite3.connect(MEMES_DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM likes WHERE meme_id = ? AND user_id = ?", (meme_id, user["id"]))
    liked = cur.fetchone() is not None
    
    if liked:
        cur.execute("DELETE FROM likes WHERE meme_id = ? AND user_id = ?", (meme_id, user["id"]))
        new_liked = False
    else:
        cur.execute("INSERT OR IGNORE INTO likes (meme_id, user_id) VALUES (?, ?)", (meme_id, user["id"]))
        new_liked = True
        
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM likes WHERE meme_id = ?", (meme_id,))
    count = cur.fetchone()[0]
    conn.close()
    
    return {"liked": new_liked, "count": count}

@bp.route("/file/<filename>")
def serve_meme(filename):
    return send_from_directory(os.path.join(MEMES_DIR, "files"), filename)

@bp.route("/upload", methods=["GET", "POST"])
def upload():
    user = get_current_user()
    if not user:
        return redirect("/login")

    storage = get_storage_info(user["id"])
    message = None
    message_type = "info"

    if request.method == "POST":
        file = request.files.get("file")
        if not file:
            message = "❌ Keine Datei ausgewählt."
            message_type = "error"
        else:
            orig_ext = os.path.splitext(file.filename)[1].lower()
            if orig_ext not in [".png", ".jpg", ".jpeg", ".gif", ".mp4", ".webm"]:
                message = "❌ Nicht unterstütztes Format."
                message_type = "error"
            else:
                timestamp = int(time.time())
                filename = f"{user['id']}-{timestamp}{orig_ext}"
                files_dir = os.path.join(MEMES_DIR, "files")
                os.makedirs(files_dir, exist_ok=True)
                filepath = os.path.join(files_dir, filename)

                try:
                    if orig_ext in [".mp4", ".webm"]:
                        file.save(filepath)
                    else:
                        img = Image.open(file)
                        img.save(filepath)

                    # Tatsächliche Dateigröße nach dem Schreiben prüfen
                    actual_size = os.path.getsize(filepath)

                    # Storage-Check gegen das neue Limit (gerade geschriebene Datei ausschließen)
                    ok, info = check_storage(user["id"], actual_size, exclude_paths=[filepath])
                    if not ok:
                        # Datei wieder löschen, da Limit überschritten
                        try:
                            os.remove(filepath)
                        except FileNotFoundError:
                            pass
                        message = (
                            f"❌ Speicherlimit überschritten. "
                            f"Belegt: {info['used_human']} / Limit: {info['limit_human']}. "
                            f"Lösche ältere Memes, um Platz zu schaffen."
                        )
                        message_type = "error"
                        # DB-Eintrag wird nicht angelegt, weil Datei gelöscht ist
                        return render_template(
                            "memes_upload.html",
                            message=message,
                            user=user["name"],
                            storage=info,
                        )

                    title = request.form.get("title", "Ein lustiges Meme")
                    conn = sqlite3.connect(MEMES_DB_PATH)
                    cur = conn.cursor()
                    cur.execute(
                        "INSERT INTO memes (user_id, filename, title, created_at) VALUES (?, ?, ?, ?)",
                        (user["id"], filename, title, int(time.time())),
                    )
                    conn.commit()
                    conn.close()
                    message = "✅ Meme erfolgreich hochgeladen!"
                    message_type = "success"
                    # Nach erfolgreichem Upload Storage neu berechnen
                    storage = get_storage_info(user["id"])
                except Exception as e:
                    message = f"❌ Fehler: {e}"
                    message_type = "error"

    return render_template("memes_upload.html", message=message, user=user["name"], storage=storage)

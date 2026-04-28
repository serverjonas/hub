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
)
from PIL import Image

from toolbox import DB_PATH, get_current_user, get_name

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

    now = int(time.time())
    # Einfache Gewichtung: Neuere Memes haben leicht höhere Chance, aber alle sind möglich
    # Gewicht = 1 + (Stunden seit Upload / 24) -> Ältere Memes werden leicht bevorzugt oder umgekehrt?
    # Eigentlich wollen wir meistens Abwechslung. Random ohne Gewichtung ist oft cleaner.
    return random.choice(memes)


@bp.route("/", methods=["GET"])
def memepage():
    user = get_current_user()
    if user is None:
        return redirect("/login")

    meme_row = get_random_meme()

    if meme_row is None:
        return render_template("meme_view.html", meme=None, user=user)

    meme = {
        "meme_id": meme_row[0],
        "user_id": meme_row[1],
        "filename": meme_row[2],
        "title": meme_row[3],
        "created_at": meme_row[4],
        "author": get_name(meme_row[1]),
    }

    return render_template("meme_view.html", meme=meme, user=user)


@bp.route("/file/<filename>")
def serve_meme(filename):
    return send_from_directory(os.path.join(MEMES_DIR, "files"), filename)


@bp.route("/upload", methods=["GET", "POST"])
def upload():
    user = get_current_user()
    if not user:
        return redirect("/login")

    message = None
    if request.method == "POST":
        file = request.files.get("file")
        if not file:
            message = "❌ Keine Datei ausgewählt."
        else:
            timestamp = int(time.time())
            filename = f"{user['id']}-{timestamp}.png"
            files_dir = os.path.join(MEMES_DIR, "files")
            os.makedirs(files_dir, exist_ok=True)
            filepath = os.path.join(files_dir, filename)

            try:
                img = Image.open(file)
                img.convert("RGB")  # Sicherstellen dass es ein valides Bild ist
                img.save(filepath, format="PNG")

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
            except Exception as e:
                message = f"❌ Fehler: {e}"

    return render_template("memes_upload.html", message=message, user=user["name"])


# Import am Ende um Zirkelbezüge zu vermeiden falls nötig
from flask import send_from_directory

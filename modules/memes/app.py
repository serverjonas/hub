from flask import Blueprint, render_template, request, redirect, make_response, abort
from PIL import Image
import sqlite3, secrets, time, os, random
from toolbox import get_current_user, get_name


MEMES_DIR = "/var/www/serverjonas-hub/static/memes"
USER_DB_PATH = "/var/www/serverjonas-hub/users.db"
MEMES_DB_PATH = os.path.join(MEMES_DIR, "memes.db")

bp = Blueprint("memes", __name__)

def get_random_meme():
    conn = sqlite3.connect(MEMES_DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT meme_id, user_id, filename, title, created_at FROM memes")
    memes = cur.fetchall()
    conn.close()

    now = int(time.time())
    weights = [1 + (now - meme[4])/3600 for meme in memes]  # z.B. Gewicht = Stunden seit Upload
    selected = random.choices(memes, weights=weights, k=1)[0]

    return {
        "meme_id": selected[0],
        "user_id": selected[1],
        "filename": selected[2],
        "title": selected[3],
        "created_at": selected[4]
    }

def insert_meme(user_id, filename, title=None):
    """Fügt einen neuen Meme-Eintrag in die Datenbank ein."""
    created_at = int(time.time())

    conn = sqlite3.connect(MEMES_DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO memes (user_id, filename, title, created_at)
        VALUES (?, ?, ?, ?)
    """, (user_id, filename, title, created_at))

    conn.commit()
    conn.close()

    return True  # optional, nur zur Bestätigung

@bp.route("/", methods=["GET"])
def memepage():
    """Zeigt ein zufälliges Meme im Viewer."""
    user = get_current_user()

    if user is None:
        abort(401)

    meme = get_random_meme()  # Funktion aus vorherigem Schritt

    if meme is None:
        # Kein Meme vorhanden → Rückgabe einer Nachricht oder Template
        return "<p>Keine Memes verfügbar. Lade zuerst welche hoch!</p>"

    # Meme gefunden → Template rendern
    return render_template("meme_view.html", meme=meme, user=user)


@bp.route("/upload", methods=["GET", "POST"])
def upload():
    user = get_current_user()
    if not user:
        abort(401)

    message = None

    if request.method == "POST":
        file = request.files.get("file")
        if not file:
            message = "Keine Datei hochgeladen"
        else:
            timestamp = int(time.time())
            filename = f"{user['id']}-{timestamp}.png"
            files_dir = os.path.join(MEMES_DIR, "files")
            os.makedirs(files_dir, exist_ok=True)
            filepath = os.path.join(files_dir, filename)

            try:
                img = Image.open(file)
                img.save(filepath, format="PNG")
                title = request.form.get("title")
                insert_meme(user['id'], filename, title=title)
                message = "Meme erfolgreich gespeichert."
            except Exception as e:
                message = "Fehler beim Speichern des Bildes: " + str(e)

    return render_template("memes_upload.html", message=message, user = user["name"])

from flask import Blueprint, request, redirect, url_for, session
import json, os, time

bp = Blueprint("musikauswahl", __name__)

# ---------------- Passwörter ----------------
ADD_PASSWORD = "party_pw"       # Passwort für Add-Seite
SETTINGS_PASSWORD = "settings_pw"  # Passwort für Settings

# ---------------- Dateien ----------------
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "settings.json")
SONGS_FILE = os.path.join(os.path.dirname(__file__), "songs.json")

default_config = {"max_entries": 50, "delay_seconds": 0, "allow_duplicates": True}

def load_json(path, default):
    if not os.path.exists(path):
        save_json(path, default)
        return default
    with open(path, "r") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=4)

config = load_json(CONFIG_FILE, default_config)
songs = load_json(SONGS_FILE, [])
last_entry_time = 0

# ---------------- Layout ----------------
def layout(content, message=""):
    return f"""
    <html>
    <head>
        <meta charset='utf-8'>
        <title>Musikliste</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 40px;
                background: var(--bg);
                color: var(--fg);
                transition: 0.3s;
            }}
            :root {{
                --bg: #111;
                --fg: #eee;
                --card: #1c1c1c;
                --input: #222;
                --accent: #4da3ff;
            }}
            .card {{
                background: var(--card);
                padding: 20px;
                border-radius: 12px;
                box-shadow: 0 0 8px #0008;
                max-width: 450px;
                margin-bottom: 20px;
            }}
            input {{
                width: 100%;
                padding: 8px;
                border-radius: 8px;
                border: none;
                margin-top: 4px;
                background: var(--input);
                color: var(--fg);
                transition: 0.3s;
            }}
            button {{
                padding: 10px 16px;
                background: var(--accent);
                border: none;
                color: white;
                border-radius: 8px;
                cursor: pointer;
                margin-top: 10px;
            }}
            a {{
                color: var(--accent);
            }}
            .song {{
                margin-bottom: 10px;
                padding: 10px;
                background: #1a1a1a;
                border-radius: 6px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .del {{
                background: #d33;
                padding: 6px 10px;
                border-radius: 6px;
                color: white;
                text-decoration: none;
            }}
            .msg {{
                margin-bottom: 15px;
                padding: 10px;
                background: #b33;
                border-radius: 6px;
            }}
            #toggleMode {{
                position: fixed;
                top: 20px;
                right: 20px;
                background: #333;
                padding: 8px 14px;
                border-radius: 8px;
                cursor: pointer;
                color: #ddd;
            }}
        </style>
    </head>
    <body>
        <div id="toggleMode" onclick="toggleDark()">Dark/Light</div>
        {f"<div class='msg'>{message}</div>" if message else ""}
        {content}
        <script>
            function toggleDark() {{
                const r = document.querySelector(':root');
                if (r.style.getPropertyValue('--bg') === '#111') {{
                    r.style.setProperty('--bg', '#fafafa');
                    r.style.setProperty('--fg', '#111');
                    r.style.setProperty('--card', '#ffffff');
                    r.style.setProperty('--input', '#eee');
                }} else {{
                    r.style.setProperty('--bg', '#111');
                    r.style.setProperty('--fg', '#eee');
                    r.style.setProperty('--card', '#1c1c1c');
                    r.style.setProperty('--input', '#222');
                }}
            }}
        </script>
    </body>
    </html>
    """

# ---------------- Globaler Zugriffsschutz Add ----------------
@bp.before_request
def require_add_pw():
    allowed_routes = ["musikauswahl.login_add", "musikauswahl.login_settings", "static"]
    if request.endpoint not in allowed_routes:
        if not session.get("logged_in_add"):
            return redirect(url_for("musikauswahl.login_add"))

# ---------------- LOGIN Add ----------------
@bp.route("/login", methods=["GET","POST"])
def login_add():
    if request.method == "POST":
        pw = request.form.get("pw","")
        if pw == ADD_PASSWORD:
            session["logged_in_add"] = True
            return redirect(url_for("musikauswahl.add_song"))
        return layout("", "Falsches Passwort!")
    return layout("""
        <div class='card'>
            <h2>Party Passwort</h2>
            <form method='post'>
                Passwort: <input type='password' name='pw'>
                <button>OK</button>
            </form>
        </div>
    """)

# ---------------- SONG HINZUFÜGEN ----------------
@bp.route("/add", methods=["GET","POST"])
def add_song():
    global last_entry_time
    message = ""
    if request.method == "POST":
        now = time.time()
        if now - last_entry_time < config["delay_seconds"]:
            message = f"Bitte {int(config['delay_seconds'] - (now - last_entry_time))} Sekunden warten."
        else:
            artist = request.form.get("artist","").strip()
            title = request.form.get("title","").strip()
            if artist and title:
                if len(songs) >= config["max_entries"]:
                    message = "Limit erreicht!"
                elif not config["allow_duplicates"] and any(s["artist"]==artist and s["title"]==title for s in songs):
                    message = "Schon vorhanden!"
                else:
                    songs.append({"artist": artist, "title": title})
                    save_json(SONGS_FILE, songs)
                    last_entry_time = now
                    message = f"Gespeichert: {artist} – {title}"

    content = f"""
    <div class='card'>
        <h2>Song hinzufügen</h2>
        <div style="display:flex; align-items:flex-start; gap:20px;">
            <form method='post' style="flex:1;">
                Künstler:<br>
                <input name="artist" autocomplete="off"><br><br>
                
                Titel:<br>
                <input name="title" autocomplete="off"><br><br>
                <button type='submit'>Speichern</button>
            </form>
        </div>
        <br>
        <a href='{url_for('musikauswahl.list_songs')}'>Zur Liste</a><br>
        <a href='{url_for('musikauswahl.login_settings')}'>Entwickleroptionen</a>
    </div>
    """
    return layout(content, message)

# ---------------- SONG LISTE ----------------
@bp.route("/list")
def list_songs():
    items = ""
    for i,s in enumerate(songs):
        items += f"<div class='song'>{s['artist']} – {s['title']} <a class='del' href='{url_for('musikauswahl.delete_song', id=i)}'>✖</a></div>"
    if not items:
        items = "<p>Noch keine Songs gespeichert.</p>"
    content = f"<div class='card'><h2>Songliste</h2>{items}<br><a href='{url_for('musikauswahl.add_song')}'>Zurück</a></div>"
    return layout(content)

# ---------------- DELETE ----------------
@bp.route("/delete/<int:id>")
def delete_song(id):
    if 0 <= id < len(songs):
        songs.pop(id)
        save_json(SONGS_FILE, songs)
    return redirect(url_for("musikauswahl.list_songs"))

# ---------------- LOGIN SETTINGS ----------------
@bp.route("/settings_login", methods=["GET","POST"])
def login_settings():
    if request.method == "POST":
        pw = request.form.get("pw","")
        if pw == SETTINGS_PASSWORD:
            session["logged_in_settings"] = True
            return redirect(url_for("musikauswahl.settings"))
        return layout("", "Falsches Settings-Passwort!")
    return layout("""
        <div class='card'>
            <h2>Settings Passwort</h2>
            <form method='post'>
                Passwort: <input type='password' name='pw'>
                <button>OK</button>
            </form>
        </div>
    """)

# ---------------- SETTINGS ----------------
@bp.route("/settings", methods=["GET","POST"])
def settings():
    if not session.get("logged_in_settings"):
        return redirect(url_for("musikauswahl.login_settings"))
    message = ""
    if request.method == "POST":
        config["max_entries"] = int(request.form.get("max_entries"))
        config["delay_seconds"] = int(request.form.get("delay_seconds"))
        config["allow_duplicates"] = "allow_duplicates" in request.form
        save_json(CONFIG_FILE, config)
        message = "Einstellungen gespeichert!"
    checked = "checked" if config.get("allow_duplicates") else ""
    content = f"""
        <div class='card'>
            <h2>Entwickleroptionen</h2>
            <form method='post'>
                Maximale Einträge:<br>
                <input name='max_entries' value='{config.get("max_entries",50)}'><br><br>
                Delay zwischen Einträgen:<br>
                <input name='delay_seconds' value='{config.get("delay_seconds",0)}'><br><br>
                <label><input type='checkbox' name='allow_duplicates' {checked}> Duplikate erlauben</label><br><br>
                <button>Speichern</button>
            </form>
            <br><a href='{url_for('musikauswahl.add_song')}'>Zurück</a>
        </div>
    """
    return layout(content, message)

# ---------------- ROOT ----------------
@bp.route("/")
def index():
    return redirect(url_for("musikauswahl.add_song"))

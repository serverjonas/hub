import json
import os
import shutil
import subprocess
import uuid
import zipfile

import requests
from flask import Blueprint, abort, jsonify, render_template, request, send_file
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3

from toolbox.toolbox import get_current_user

bp = Blueprint("music", __name__, template_folder="templates")

import os

BASE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "music")

# -------------------------
# PATHS
# -------------------------


def user_paths(user_id):
    incoming = os.path.join(BASE, "incoming", str(user_id))
    library = os.path.join(BASE, "library", str(user_id))

    os.makedirs(incoming, exist_ok=True)
    os.makedirs(library, exist_ok=True)

    return incoming, library


# -------------------------
# HELPERS
# -------------------------


def safe(x):
    return x.replace("/", "_").replace("\\", "_").strip() if x else "Unknown"


def filename_fallback(name):
    name = os.path.splitext(name)[0]
    if " - " in name:
        a, t = name.split(" - ", 1)
        return a, t
    return "Unknown Artist", name


# -------------------------
# FINGERPRINT (fpcalc)
# -------------------------


def fingerprint(path):
    try:
        out = subprocess.check_output(["fpcalc", path]).decode()
        for line in out.splitlines():
            if line.startswith("FINGERPRINT="):
                return line.split("=", 1)[1]
    except:
        pass
    return None


# -------------------------
# MUSICBRAINZ LOOKUP
# -------------------------


def lookup_musicbrainz(fp, duration):
    try:
        r = requests.get(
            "https://api.acoustid.org/v2/lookup",
            params={
                "client": "iInmsHiFXY",
                "meta": "recordings",
                "duration": duration,
                "fingerprint": fp,
            },
            timeout=10,
        )
        data = r.json()

        if data.get("results"):
            rec = data["results"][0]["recordings"][0]

            artist = rec["artists"][0]["name"]
            title = rec["title"]

            return artist, title
    except:
        pass

    return None


# -------------------------
# METADATA EXTRACTION
# -------------------------


def read_id3(path):
    try:
        audio = EasyID3(path)
    except:
        audio = MP3(path, ID3=EasyID3)
        audio.add_tags()

    artist = audio.get("artist", [""])[0]
    title = audio.get("title", [""])[0]
    album = audio.get("album", ["Unknown Album"])[0]
    track = audio.get("tracknumber", ["1"])[0].split("/")[0]

    return audio, artist, title, album, track


# -------------------------
# PROCESS FILE (CORE BRAIN)
# -------------------------


def process_file(path, library):
    filename = os.path.basename(path)

    audio, artist, title, album, track = read_id3(path)

    # 1. Fingerprint (BEST)
    fp = fingerprint(path)

    if fp:
        try:
            duration = int(
                float(
                    subprocess.check_output(
                        [
                            "ffprobe",
                            "-v",
                            "error",
                            "-show_entries",
                            "format=duration",
                            "-of",
                            "default=noprint_wrappers=1:nokey=1",
                            path,
                        ]
                    )
                )
            )
        except:
            duration = 0

        mb = lookup_musicbrainz(fp, duration)

        if mb:
            artist, title = mb

    # 2. ID3 fallback
    if not artist or not title:
        fa, ft = filename_fallback(filename)
        artist = artist or fa
        title = title or ft

    # cleanup
    artist = safe(artist)
    title = safe(title)
    album = safe(album)

    try:
        track = str(int(track)).zfill(2)
    except:
        track = "01"

    # write corrected tags
    audio["artist"] = artist
    audio["title"] = title
    audio["album"] = album
    audio["tracknumber"] = track
    audio.save()

    # folder structure
    artist_dir = os.path.join(library, artist)
    album_dir = os.path.join(artist_dir, album)
    os.makedirs(album_dir, exist_ok=True)

    new_path = os.path.join(album_dir, f"{track} - {title}.mp3")

    if os.path.exists(new_path):
        os.remove(new_path)

    os.rename(path, new_path)


# -------------------------
# SCAN
# -------------------------


def scan(root):
    out = []
    for r, _, f in os.walk(root):
        for i in f:
            full = os.path.join(r, i)
            print("DEBUG FILE:", full)

            if i.lower().endswith((".mp3", ".m4a", ".flac", ".wav", ".ogg")):
                out.append(full)

    return out


# -------------------------
# UPLOAD HANDLER
# -------------------------


def handle_upload(user_id, files, zip_file):
    incoming, library = user_paths(user_id)

    tmp = os.path.join(incoming, str(uuid.uuid4()))
    os.makedirs(tmp, exist_ok=True)

    # ZIP
    if zip_file:
        zpath = os.path.join(tmp, zip_file.filename)
        zip_file.save(zpath)

        with zipfile.ZipFile(zpath, "r") as z:
            z.extractall(tmp)

    # FILES
    for f in files:
        p = os.path.join(tmp, f.filename)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        f.save(p)

    # PROCESS
    for mp3 in scan(tmp):
        process_file(mp3, library)

    shutil.rmtree(tmp, ignore_errors=True)


# -------------------------
# ROUTES
# -------------------------


@bp.route("/")
def home():
    user = get_current_user()
    if not user:
        abort(401)

    _, lib = user_paths(user["id"])
    artists = sorted(os.listdir(lib)) if os.path.exists(lib) else []

    return render_template("artists.html", artists=artists)


@bp.route("/artist/<artist>")
def albums(artist):
    user = get_current_user()
    _, lib = user_paths(user["id"])

    path = os.path.join(lib, artist)
    albums = sorted(os.listdir(path)) if os.path.exists(path) else []

    return render_template("albums.html", artist=artist, albums=albums)


@bp.route("/artist/<artist>/<album>")
def songs(artist, album):
    user = get_current_user()
    _, lib = user_paths(user["id"])

    path = os.path.join(lib, artist, album)
    songs = sorted(os.listdir(path)) if os.path.exists(path) else []

    return render_template("songs.html", artist=artist, album=album, songs=songs)


@bp.route("/play/<artist>/<album>/<song>")
def play(artist, album, song):
    user = get_current_user()
    _, lib = user_paths(user["id"])

    path = os.path.join(lib, artist, album, song)
    return send_file(path)


@bp.route("/upload", methods=["POST"])
def upload():
    user = get_current_user()
    if not user:
        abort(401)

    files = request.files.getlist("files")
    zipf = request.files.get("zip")

    handle_upload(user["id"], files, zipf)

    return jsonify({"status": "ok"})

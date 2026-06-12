import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime

from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    stream_with_context,
    url_for,
)

from toolbox.user import get_current_user

FILMS_DATA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "films", "data"
)
# Ensure the tmp directory exists so tempfile.tempdir works
tmp_dir = os.path.join(FILMS_DATA_DIR, "tmp")
os.makedirs(tmp_dir, exist_ok=True)
tempfile.tempdir = tmp_dir

UPLOAD_TMP_DIR = os.path.join(FILMS_DATA_DIR, "chunk_uploads")
os.makedirs(UPLOAD_TMP_DIR, exist_ok=True)

bp = Blueprint("films", __name__, template_folder="templates")


@bp.app_template_filter("strftime")
def strftime_filter(value):
    return datetime.fromtimestamp(int(value)).strftime("%d.%m.%Y %H:%M")


# ── Hilfsfunktionen ────────────────────────────────────────────────────────────


def user_dir(username: str) -> str:
    return os.path.join(FILMS_DATA_DIR, str(username), "films")


def film_dir(username: str, film_id: str) -> str:
    return os.path.join(user_dir(username), film_id)


def meta_path(username: str, film_id: str) -> str:
    return os.path.join(film_dir(username, film_id), "meta.json")


def read_meta(username: str, film_id: str) -> dict | None:
    p = meta_path(username, film_id)
    if not os.path.isfile(p):
        return None
    with open(p) as f:
        return json.load(f)


def write_meta(username: str, film_id: str, data: dict):
    with open(meta_path(username, film_id), "w") as f:
        json.dump(data, f, indent=2)


def list_films(username: str) -> list:
    base = user_dir(username)
    if not os.path.isdir(base):
        return []
    films = []
    for fid in os.listdir(base):
        meta = read_meta(username, fid)
        if meta:
            films.append(meta)
    films.sort(key=lambda m: m.get("uploaded_at", 0), reverse=True)
    return films


def list_only_films(username: str) -> list:
    """Nur Einträge ohne Serien-Zugehörigkeit."""
    return [m for m in list_films(username) if not m.get("series")]


def list_series(username: str) -> dict:
    """
    Gibt ein dict zurück: { "Serienname": { "seasons": { 1: [ meta, ... ] } } }
    """
    all_items = list_films(username)
    series_dict = {}
    for m in all_items:
        s = m.get("series")
        if not s:
            continue
        if s not in series_dict:
            series_dict[s] = {"seasons": {}}
        season = int(m.get("season", 1))
        if season not in series_dict[s]["seasons"]:
            series_dict[s]["seasons"][season] = []
        series_dict[s]["seasons"][season].append(m)
    # Episoden innerhalb jeder Staffel sortieren
    for s in series_dict:
        for season in series_dict[s]["seasons"]:
            series_dict[s]["seasons"][season].sort(
                key=lambda m: int(m.get("episode", 0))
            )
    return series_dict


# ── FFmpeg-Konvertierung ───────────────────────────────────────────────────────


def convert_film(username: str, film_id: str, original_path: str):
    fdir = film_dir(username, film_id)
    output_path = os.path.join(fdir, "film.mp4")

    meta = read_meta(username, film_id)
    meta["status"] = "converting"
    write_meta(username, film_id, meta)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        original_path,
        "-vf",
        "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease",
        "-c:v",
        "libx264",
        "-crf",
        "23",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        output_path,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
        meta["status"] = "ready"
        meta["filename"] = "film.mp4"
        if os.path.isfile(original_path):
            os.remove(original_path)
    except subprocess.CalledProcessError as e:
        meta["status"] = "error"
        meta["error"] = e.stderr.decode(errors="replace")[-500:]

    write_meta(username, film_id, meta)


# ── Routen ─────────────────────────────────────────────────────────────────────


@bp.route("/", methods=["GET"])
def index():
    user = get_current_user()
    if not user:
        return render_template("not_logged_in.html")
    return render_template("films_index.html", user=user["name"])


@bp.route("/films", methods=["GET"])
def films():
    user = get_current_user()
    if not user:
        return render_template("not_logged_in.html")
    films = list_only_films(user["id"])
    return render_template("films_films.html", user=user["name"], films=films)


@bp.route("/series", methods=["GET"])
def series():
    user = get_current_user()
    if not user:
        return render_template("not_logged_in.html")
    series_data = list_series(user["id"])
    return render_template("films_series.html", user=user["name"], series=series_data)


@bp.route("/series/<series_name>", methods=["GET"])
def series_detail(series_name):
    user = get_current_user()
    if not user:
        return render_template("not_logged_in.html")
    series_data = list_series(user["id"])
    if series_name not in series_data:
        abort(404)
    return render_template(
        "films_series_detail.html",
        user=user["name"],
        series_name=series_name,
        seasons=series_data[series_name]["seasons"],
    )


@bp.route("/upload", methods=["GET", "POST"])
def upload():
    user = get_current_user()
    if not user:
        return render_template("not_logged_in.html")

    message = None
    message_type = "info"

    if request.method == "POST":
        file = request.files.get("file")
        title = request.form.get("title", "").strip() or "Unbekannter Film"
        upload_type = request.form.get("upload_type", "film")  # "film" oder "series"
        series_name = request.form.get("series_name", "").strip()
        season = request.form.get("season", "1").strip()
        episode = request.form.get("episode", "1").strip()

        if not file or file.filename == "":
            message = "Keine Datei ausgewählt."
            message_type = "error"
        else:
            film_id = str(uuid.uuid4())[:8]
            fdir = film_dir(user["id"], film_id)
            os.makedirs(fdir, exist_ok=True)

            ext = os.path.splitext(file.filename)[1].lower() or ".mp4"
            original_filename = f"original{ext}"
            original_path = os.path.join(fdir, original_filename)
            file.save(original_path)

            meta = {
                "film_id": film_id,
                "title": title,
                "username": user["name"],
                "uploaded_at": int(time.time()),
                "status": "queued",
                "filename": None,
                "original": original_filename,
            }

            if upload_type == "series" and series_name:
                meta["series"] = series_name
                meta["season"] = int(season) if season.isdigit() else 1
                meta["episode"] = int(episode) if episode.isdigit() else 1

            write_meta(user["id"], film_id, meta)

            t = threading.Thread(
                target=convert_film,
                args=(user["id"], film_id, original_path),
                daemon=True,
            )
            t.start()

            return redirect(url_for("films.film_detail", film_id=film_id))

    return render_template(
        "films_upload.html",
        user=user["name"],
        message=message,
        message_type=message_type,
    )

@bp.route("/upload/init", methods=["POST"])
def upload_init():
    user = get_current_user()
    if not user:
        abort(403)

    data = request.json

    upload_id = str(uuid.uuid4())

    upload_dir = os.path.join(UPLOAD_TMP_DIR, upload_id)
    os.makedirs(upload_dir, exist_ok=True)

    with open(os.path.join(upload_dir, "meta.json"), "w") as f:
        json.dump(data, f)

    return jsonify({
        "upload_id": upload_id
    })

@bp.route("/upload/chunk", methods=["POST"])
def upload_chunk():
    user = get_current_user()
    if not user:
        abort(403)

    upload_id = request.form["upload_id"]
    chunk_index = int(request.form["chunk_index"])

    chunk = request.files["chunk"]

    upload_dir = os.path.join(
        UPLOAD_TMP_DIR,
        upload_id
    )

    chunk_path = os.path.join(
        upload_dir,
        f"{chunk_index:08d}.part"
    )

    chunk.save(chunk_path)

    return jsonify({
        "ok": True
    })

@bp.route("/upload/finish", methods=["POST"])
def upload_finish():
    user = get_current_user()
    if not user:
        abort(403)

    data = request.json

    upload_id = data["upload_id"]

    upload_dir = os.path.join(
        UPLOAD_TMP_DIR,
        upload_id
    )

    with open(
        os.path.join(upload_dir, "meta.json")
    ) as f:
        upload_meta = json.load(f)

    film_id = str(uuid.uuid4())[:8]

    fdir = film_dir(
        user["id"],
        film_id
    )

    os.makedirs(fdir, exist_ok=True)

    ext = upload_meta.get(
        "extension",
        ".mp4"
    )

    original_filename = f"original{ext}"

    original_path = os.path.join(
        fdir,
        original_filename
    )

    with open(original_path, "wb") as out:

        chunks = sorted(
            x for x in os.listdir(upload_dir)
            if x.endswith(".part")
        )

        for chunk_name in chunks:

            chunk_path = os.path.join(
                upload_dir,
                chunk_name
            )

            with open(chunk_path, "rb") as inp:

                while True:
                    buf = inp.read(1024 * 1024)

                    if not buf:
                        break

                    out.write(buf)

    meta = {
        "film_id": film_id,
        "title": upload_meta["title"],
        "username": user["name"],
        "uploaded_at": int(time.time()),
        "status": "queued",
        "filename": None,
        "original": original_filename,
    }

    if upload_meta["upload_type"] == "series":
        meta["series"] = upload_meta["series_name"]
        meta["season"] = int(upload_meta["season"])
        meta["episode"] = int(upload_meta["episode"])

    write_meta(
        user["name"],
        film_id,
        meta
    )

    threading.Thread(
        target=convert_film,
        args=(
            user["id"],
            film_id,
            original_path
        ),
        daemon=True
    ).start()

    shutil.rmtree(
        upload_dir,
        ignore_errors=True
    )

    return jsonify({
        "film_id": film_id,
        "redirect":
            url_for(
                "films.film_detail",
                film_id=film_id
            )
    })


@bp.route("/film/<film_id>")
def film_detail(film_id):
    user = get_current_user()
    if not user:
        return render_template("not_logged_in.html")
    meta = read_meta(user["id"], film_id)
    if not meta:
        abort(404)

    # Nächste Episode ermitteln (falls Serie)
    next_episode = None
    if meta.get("series"):
        series_data = list_series(user["id"])
        s = meta["series"]
        season = int(meta.get("season", 1))
        episode = int(meta.get("episode", 1))
        if s in series_data:
            seasons = series_data[s]["seasons"]
            eps_in_season = seasons.get(season, [])
            for ep in eps_in_season:
                if int(ep.get("episode", 0)) == episode + 1:
                    next_episode = ep
                    break
            if not next_episode:
                next_season_eps = seasons.get(season + 1, [])
                if next_season_eps:
                    next_episode = next_season_eps[0]

    return render_template(
        "films_detail.html", user=user["name"], film=meta, next_episode=next_episode
    )


@bp.route("/film/<film_id>/watch")
def watch(film_id):
    user = get_current_user()
    if not user:
        abort(403)

    meta = read_meta(user["id"], film_id)
    if not meta or meta.get("status") != "ready":
        abort(404)

    fpath = os.path.join(film_dir(user["id"], film_id), meta["filename"])
    if not os.path.isfile(fpath):
        abort(404)

    file_size = os.path.getsize(fpath)
    range_header = request.headers.get("Range")

    if range_header:
        byte_range = range_header.replace("bytes=", "").split("-")
        start = int(byte_range[0])
        end = int(byte_range[1]) if byte_range[1] else file_size - 1
        length = end - start + 1

        def generate_range():
            with open(fpath, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        resp = Response(
            stream_with_context(generate_range()), 206, mimetype="video/mp4"
        )
        resp.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Content-Length"] = str(length)
        return resp

    def generate_full():
        with open(fpath, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                yield chunk

    resp = Response(stream_with_context(generate_full()), 200, mimetype="video/mp4")
    resp.headers["Content-Length"] = str(file_size)
    resp.headers["Accept-Ranges"] = "bytes"
    return resp


@bp.route("/film/<film_id>/status")
def film_status(film_id):
    user = get_current_user()
    if not user:
        abort(403)
    meta = read_meta(user["id"], film_id)
    if not meta:
        abort(404)
    return jsonify({"status": meta.get("status"), "film_id": film_id})


@bp.route("/film/<film_id>/delete", methods=["POST"])
def delete_film(film_id):
    user = get_current_user()
    if not user:
        abort(403)
    fdir = film_dir(user["id"], film_id)
    if os.path.isdir(fdir):
        shutil.rmtree(fdir)
    return redirect(url_for("films.index"))

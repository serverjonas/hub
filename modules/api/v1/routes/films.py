"""API v1 · films – chunked film / series upload.

Mirrors the browser-facing chunked upload in ``modules/films/app.py`` but
authenticates via the API key (``@require_api_key``) instead of a cookie
session. The heavy lifting (storage checks, meta persistence, ffmpeg
transcoding) is delegated to the existing films module to avoid
duplication.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid

from flask import Blueprint, abort, g, jsonify, request, url_for

from modules.api.v1.auth import require_api_key
from modules.films.app import (
    UPLOAD_TMP_DIR,
    check_storage,
    convert_film,
    film_dir,
    write_meta,
)


bp = Blueprint("api_v1_films", __name__)


# Per-chunk hard cap. Keeps the chunk-upload blind spot closed even if
# Content-Length is missing: a chunk larger than this is rejected before
# disk write.
MAX_CHUNK_BYTES = 16 * 1024 * 1024


# ─── Upload pipeline ─────────────────────────────────────────────────────


@bp.post("/upload/init")
@require_api_key(scopes={"films:upload"})
def upload_init():
    payload = request.get_json(silent=True) or {}

    required_fields = ("title", "upload_type", "extension")
    if not all(payload.get(k) for k in required_fields):
        return jsonify({
            "error": "missing_fields",
            "message": "title, upload_type und extension sind erforderlich.",
        }), 400

    if payload["upload_type"] not in ("film", "series"):
        return jsonify({
            "error": "invalid_upload_type",
            "message": "upload_type muss 'film' oder 'series' sein.",
        }), 400

    if payload["upload_type"] == "series":
        if not payload.get("series_name"):
            return jsonify({
                "error": "missing_series_name",
                "message": "series_name ist für Serien erforderlich.",
            }), 400
        try:
            payload["season"] = int(payload.get("season", 1))
            payload["episode"] = int(payload.get("episode", 1))
        except (TypeError, ValueError):
            return jsonify({
                "error": "invalid_season_episode",
                "message": "season und episode müssen ganze Zahlen sein.",
            }), 400

    user_id = g.api_user["id"]
    upload_id = str(uuid.uuid4())
    upload_dir = os.path.join(UPLOAD_TMP_DIR, upload_id)
    os.makedirs(upload_dir, exist_ok=True)

    # Persist meta early so /chunk and /finish can read it.
    safe_meta = {
        "title": str(payload["title"])[:200],
        "upload_type": payload["upload_type"],
        "extension": "." + str(payload["extension"]).lstrip(".").lower(),
        "owner_id": user_id,
        "owner_name": g.api_user["name"],
        "key_id": g.api_key["id"],
        "created_at": int(time.time()),
    }
    if payload["upload_type"] == "series":
        safe_meta["series_name"] = str(payload["series_name"])[:200]
        safe_meta["season"] = payload["season"]
        safe_meta["episode"] = payload["episode"]

    with open(os.path.join(upload_dir, "meta.json"), "w") as f:
        json.dump(safe_meta, f)

    # Pre-flight storage check.
    expected_size = payload.get("expected_size")
    if expected_size:
        try:
            expected_size = int(expected_size)
        except (TypeError, ValueError):
            expected_size = None
        if expected_size:
            ok, info = check_storage(user_id, expected_size, exclude_paths=[upload_dir])
            if not ok:
                shutil.rmtree(upload_dir, ignore_errors=True)
                return jsonify({
                    "error": "storage_limit_exceeded",
                    "used_human": info["used_human"],
                    "limit_human": info["limit_human"],
                    "remaining_human": info["remaining_human"],
                    "message": (
                        f"Speicherlimit überschritten. Belegt: {info['used_human']} / "
                        f"Limit: {info['limit_human']}."
                    ),
                }), 413

    return jsonify({"upload_id": upload_id})


@bp.post("/upload/chunk")
@require_api_key(scopes={"films:upload"})
def upload_chunk():
    upload_id = request.form.get("upload_id")
    chunk_index = request.form.get("chunk_index")
    chunk = request.files.get("chunk")

    if not upload_id or chunk_index is None or chunk is None:
        return jsonify({
            "error": "missing_chunk_fields",
            "message": "upload_id, chunk_index und chunk sind erforderlich.",
        }), 400

    try:
        chunk_index = int(chunk_index)
    except (TypeError, ValueError):
        return jsonify({
            "error": "invalid_chunk_index",
            "message": "chunk_index muss eine ganze Zahl sein.",
        }), 400

    if chunk_index < 0:
        return jsonify({
            "error": "invalid_chunk_index",
            "message": "chunk_index darf nicht negativ sein.",
        }), 400

    user_id = g.api_user["id"]
    upload_dir = os.path.join(UPLOAD_TMP_DIR, upload_id)

    if not os.path.isdir(upload_dir):
        abort(404)

    # Enforce ownership: meta.json was written by /upload/init with owner_id
    meta_path = os.path.join(upload_dir, "meta.json")
    if not os.path.isfile(meta_path):
        abort(404)
    with open(meta_path) as f:
        meta = json.load(f)
    if meta.get("owner_id") != user_id:
        return jsonify({
            "error": "forbidden",
            "message": "Dieser Upload gehört einem anderen Nutzer.",
        }), 403

    chunk_filename = f"{chunk_index:08d}.part"
    chunk_path = os.path.join(upload_dir, chunk_filename)

    # Idempotent re-upload: if the same chunk_index arrives twice, the
    # retried chunk must byte-for-byte match the original (network retry).
    # Anything else is treated as a protocol violation.
    chunk_bytes = chunk.read()
    if len(chunk_bytes) > MAX_CHUNK_BYTES:
        return jsonify({
            "error": "chunk_too_large",
            "message": f"Chunk ist größer als {MAX_CHUNK_BYTES} Bytes.",
            "max_chunk": MAX_CHUNK_BYTES,
        }), 413
    if os.path.isfile(chunk_path):
        try:
            with open(chunk_path, "rb") as existing:
                existing_bytes = existing.read()
        except OSError:
            existing_bytes = None
        if existing_bytes != chunk_bytes:
            return jsonify({
                "error": "chunk_mismatch",
                "message": (
                    "Dieser Chunk-Index wurde bereits mit anderen Daten "
                    "belegt. Netzwerk-Retry muss byte-identisch sein."
                ),
            }), 409
        # Identical retry — return OK without re-writing.
        return jsonify({"ok": True, "received_bytes": len(chunk_bytes), "duplicate": True})

    # Track accumulated size and re-check against storage limit on the fly.
    running_size = len(chunk_bytes)
    for name in os.listdir(upload_dir):
        if name.endswith(".part"):
            try:
                running_size += os.path.getsize(os.path.join(upload_dir, name))
            except OSError:
                pass

    ok, info = check_storage(user_id, running_size, exclude_paths=[upload_dir])
    if not ok:
        return jsonify({
            "error": "storage_limit_exceeded",
            "used_human": info["used_human"],
            "limit_human": info["limit_human"],
            "remaining_human": info["remaining_human"],
            "message": (
                f"Speicherlimit überschritten. Belegt: {info['used_human']} / "
                f"Limit: {info['limit_human']}."
            ),
        }), 413

    with open(chunk_path, "wb") as out:
        out.write(chunk_bytes)

    return jsonify({"ok": True, "received_bytes": len(chunk_bytes)})


@bp.post("/upload/finish")
@require_api_key(scopes={"films:upload"})
def upload_finish():
    data = request.get_json(silent=True) or {}
    upload_id = data.get("upload_id")
    if not upload_id:
        return jsonify({
            "error": "missing_upload_id",
            "message": "upload_id ist erforderlich.",
        }), 400

    user_id = g.api_user["id"]
    upload_dir = os.path.join(UPLOAD_TMP_DIR, upload_id)
    if not os.path.isdir(upload_dir):
        abort(404)

    meta_path = os.path.join(upload_dir, "meta.json")
    with open(meta_path) as f:
        meta = json.load(f)
    if meta.get("owner_id") != user_id:
        return jsonify({
            "error": "forbidden",
            "message": "Dieser Upload gehört einem anderen Nutzer.",
        }), 403

    film_id = str(uuid.uuid4())[:8]
    fdir = film_dir(user_id, film_id)
    os.makedirs(fdir, exist_ok=True)

    ext = meta["extension"]
    original_filename = f"original{ext}"
    original_path = os.path.join(fdir, original_filename)

    with open(original_path, "wb") as out:
        chunks = sorted(x for x in os.listdir(upload_dir) if x.endswith(".part"))
        if not chunks:
            shutil.rmtree(fdir, ignore_errors=True)
            shutil.rmtree(upload_dir, ignore_errors=True)
            return jsonify({
                "error": "no_chunks",
                "message": "Es wurden keine Chunks hochgeladen.",
            }), 400
        for chunk_name in chunks:
            with open(os.path.join(upload_dir, chunk_name), "rb") as inp:
                while True:
                    buf = inp.read(1024 * 1024)
                    if not buf:
                        break
                    out.write(buf)

    actual_size = os.path.getsize(original_path)
    ok, info = check_storage(user_id, actual_size, exclude_paths=[fdir])
    if not ok:
        shutil.rmtree(fdir, ignore_errors=True)
        shutil.rmtree(upload_dir, ignore_errors=True)
        return jsonify({
            "error": "storage_limit_exceeded",
            "used_human": info["used_human"],
            "limit_human": info["limit_human"],
            "remaining_human": info["remaining_human"],
            "message": (
                f"Speicherlimit überschritten. Belegt: {info['used_human']} / "
                f"Limit: {info['limit_human']}."
            ),
        }), 413

    film_meta = {
        "film_id": film_id,
        "title": meta["title"],
        "username": g.api_user["name"],
        "uploaded_at": int(time.time()),
        "uploaded_via": "api_v1",
        "status": "queued",
        "filename": None,
        "original": original_filename,
    }
    if meta["upload_type"] == "series":
        film_meta["series"] = meta["series_name"]
        film_meta["season"] = int(meta["season"])
        film_meta["episode"] = int(meta["episode"])

    write_meta(user_id, film_id, film_meta)

    threading.Thread(
        target=convert_film,
        args=(user_id, film_id, original_path),
        daemon=True,
    ).start()

    shutil.rmtree(upload_dir, ignore_errors=True)

    return jsonify({
        "film_id": film_id,
        "status": "queued",
        "watch_url": url_for(
            "films.film_detail",
            film_id=film_id,
            _external=False,
        ),
        "status_url": url_for(
            "films.film_status",
            film_id=film_id,
            _external=False,
        ),
    })

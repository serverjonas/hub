#cloud app.py
import os
import uuid
import zipfile
import shutil
from flask import Blueprint, render_template, request, abort, send_file, redirect, jsonify

from toolbox.user import get_current_user
from toolbox.files import DATA_PATH, _folder_size, check_storage, get_storage_info, _format_bytes

bp = Blueprint("cloud", __name__, template_folder="templates")

CLOUD_BASE = os.path.join(DATA_PATH, "cloud")
# Temp-Verzeichnis für Uploads, das NICHT unter data/cloud/{user_id} liegt,
# damit es nicht fälschlich beim Storage-Counting mitgezählt wird.
CLOUD_TMP_BASE = os.path.join(DATA_PATH, "cloud_tmp")

# -----------------------------
# Helpers
# -----------------------------

def user_root(user_id):
    path = os.path.join(CLOUD_BASE, str(user_id))
    os.makedirs(path, exist_ok=True)
    return path


def safe_path(base, target):
    # prevents path traversal
    full = os.path.abspath(os.path.join(base, target))
    if not full.startswith(os.path.abspath(base)):
        raise Exception("invalid path")
    return full


# -----------------------------
# Views
# -----------------------------

@bp.route("/")
def home():
    user = get_current_user()
    if not user:
        abort(401)

    root = user_root(user["id"])

    items = []
    for f in os.listdir(root):
        full = os.path.join(root, f)
        items.append({
            "name": f,
            "is_dir": os.path.isdir(full)
        })

    storage = get_storage_info(user["id"])
    return render_template("cloud.html", items=items, storage=storage)


@bp.route("/upload", methods=["POST"])
def upload():
    user = get_current_user()
    if not user:
        abort(401)

    files = request.files.getlist("files")
    zipf = request.files.get("zip")

    os.makedirs(CLOUD_TMP_BASE, exist_ok=True)
    tmp = os.path.join(CLOUD_TMP_BASE, f"{user['id']}_{uuid.uuid4()}")
    os.makedirs(tmp, exist_ok=True)

    try:
        # ZIP upload
        if zipf:
            zpath = os.path.join(tmp, zipf.filename)
            zipf.save(zpath)
            with zipfile.ZipFile(zpath, "r") as z:
                z.extractall(tmp)
            # das Zip selbst zählt nicht zum Inhalt
            try:
                os.remove(zpath)
            except FileNotFoundError:
                pass

        # normal files
        for f in files:
            p = os.path.join(tmp, f.filename)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            f.save(p)

        # Größe des Uploads ermitteln
        incoming_size = _folder_size(tmp)

        # Storage-Check (tmp-Pfad ausschließen, damit nicht doppelt gezählt)
        ok, info = check_storage(user["id"], incoming_size, exclude_paths=[tmp])
        if not ok:
            return jsonify({
                "error": "storage_limit_exceeded",
                "used_human": info["used_human"],
                "limit_human": info["limit_human"],
                "remaining_human": info["remaining_human"],
                "incoming_human": _format_bytes(incoming_size),
                "would_use_human": _format_bytes(info["would_use"]),
                "message": (
                    f"Speicherlimit überschritten. Belegt: {info['used_human']} / "
                    f"Limit: {info['limit_human']}."
                ),
            }), 413

        # move into cloud
        root = user_root(user["id"])
        for r, _, fs in os.walk(tmp):
            for file in fs:
                src = os.path.join(r, file)
                rel = os.path.relpath(src, tmp)
                dst = safe_path(root, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)

        return jsonify({"status": "ok"})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@bp.route("/download/<path:path>")
def download(path):
    user = get_current_user()
    if not user:
        abort(401)

    root = user_root(user["id"])
    full = safe_path(root, path)

    if not os.path.exists(full):
        abort(404)

    return send_file(full, as_attachment=True)


@bp.route("/delete/<path:path>")
def delete(path):
    user = get_current_user()
    if not user:
        abort(401)

    root = user_root(user["id"])
    full = safe_path(root, path)

    if os.path.isdir(full):
        shutil.rmtree(full)
    else:
        os.remove(full)

    return redirect("/cloud/")

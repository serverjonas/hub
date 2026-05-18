#cloud app.py
import os
import uuid
import zipfile
import shutil
from flask import Blueprint, render_template, request, abort, send_file, redirect, jsonify

from toolbox.user import get_current_user
from toolbox.files import DATA_PATH

bp = Blueprint("cloud", __name__, template_folder="templates")

CLOUD_BASE = os.path.join(DATA_PATH, "cloud")

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

    return render_template("cloud.html", items=items)


@bp.route("/upload", methods=["POST"])
def upload():
    user = get_current_user()
    if not user:
        abort(401)

    root = user_root(user["id"])

    files = request.files.getlist("files")
    zipf = request.files.get("zip")

    tmp = os.path.join(root, ".tmp_" + str(uuid.uuid4()))
    os.makedirs(tmp, exist_ok=True)

    # ZIP upload
    if zipf:
        zpath = os.path.join(tmp, zipf.filename)
        zipf.save(zpath)
        with zipfile.ZipFile(zpath, "r") as z:
            z.extractall(tmp)

    # normal files
    for f in files:
        p = os.path.join(tmp, f.filename)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        f.save(p)

    # move into cloud
    for r, _, fs in os.walk(tmp):
        for file in fs:
            src = os.path.join(r, file)
            rel = os.path.relpath(src, tmp)
            dst = safe_path(root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)

    shutil.rmtree(tmp, ignore_errors=True)

    return jsonify({"status": "ok"})


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



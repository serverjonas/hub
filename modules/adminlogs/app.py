import os
import re
import sys
from datetime import datetime

from flask import Blueprint, abort, jsonify, render_template

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
from toolbox.files import LOGS_DIR
from toolbox.user import get_current_user, get_infos

bp = Blueprint("adminlogs", __name__, template_folder="templates")

# Hard caps to avoid a giant log file locking up the server.
MAX_FILE_BYTES = 5 * 1024 * 1024   # 5 MB per request
MAX_LINES = 10000                  # last N lines per request

# Strict whitelist for filenames — no slashes, dots-as-traversal, etc.
_VALID_NAME = re.compile(r"^[A-Za-z0-9._-]+\.log$")


# ─── Admin-Check für alle Routen unter diesem Blueprint ──────────────────────
@bp.before_request
def require_admin():
    user = get_current_user()
    if user is None:
        abort(401)
    infos = get_infos(user["id"])
    if infos is None or not infos["admin"]:
        abort(403)


# ─── Helpers ────────────────────────────────────────────────────────────────
def _human_size(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _human_time(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "—"


def _safe_path(name: str):
    """Returns the absolute path to a log file inside LOGS_DIR, or None.

    Defense in depth: even if the filename passes the regex + separator
    check, we resolve symlinks and confirm the resolved path is really
    inside LOGS_DIR.
    """
    if not name or not isinstance(name, str):
        return None
    if os.sep in name or (os.altsep and os.altsep in name):
        return None
    if ".." in name:
        return None
    if not _VALID_NAME.match(name):
        return None
    candidate = os.path.join(LOGS_DIR, name)
    real_logs = os.path.realpath(LOGS_DIR)
    try:
        real_path = os.path.realpath(candidate)
    except OSError:
        return None
    if not (real_path == real_logs or real_path.startswith(real_logs + os.sep)):
        return None
    return real_path


def _read_text(path: str):
    """Reads up to MAX_FILE_BYTES + 1 of the file as text."""
    with open(path, "rb") as f:
        raw = f.read(MAX_FILE_BYTES + 1)
    truncated = False
    if len(raw) > MAX_FILE_BYTES:
        raw = raw[:MAX_FILE_BYTES]
        truncated = True
    try:
        return raw.decode("utf-8"), truncated
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace"), truncated


# ─── Page ──────────────────────────────────────────────────────────────────
@bp.route("/")
def index():
    user = get_current_user()
    return render_template("admin_logs.html", user=user["name"])


# ─── API: list log files ───────────────────────────────────────────────────
@bp.route("/api/list")
def api_list():
    if not os.path.isdir(LOGS_DIR):
        return jsonify({"files": []})

    out = []
    for fname in os.listdir(LOGS_DIR):
        if not fname.endswith(".log"):
            continue
        path = os.path.join(LOGS_DIR, fname)
        if not os.path.isfile(path):
            continue
        try:
            size = os.path.getsize(path)
            mtime = os.path.getmtime(path)
        except OSError:
            continue

        line_count = 0
        truncated = False
        try:
            with open(path, "rb") as f:
                head = f.read(MAX_FILE_BYTES)
            line_count = head.count(b"\n")
            truncated = size > MAX_FILE_BYTES
        except OSError:
            pass

        out.append({
            "name": fname,
            "size": size,
            "size_human": _human_size(size),
            "mtime": mtime,
            "mtime_human": _human_time(mtime),
            "lines": line_count,
            "truncated": truncated,
        })

    out.sort(key=lambda f: f["mtime"], reverse=True)
    return jsonify({"files": out})


# ─── API: serve a single log file's contents ───────────────────────────────
@bp.route("/api/file/<name>")
def api_file(name):
    path = _safe_path(name)
    if path is None or not os.path.isfile(path):
        abort(404)

    # First pass: full line count so we can return stable line numbers.
    try:
        size = os.path.getsize(path)
        mtime = os.path.getmtime(path)
        with open(path, "rb") as f:
            raw_full = f.read(MAX_FILE_BYTES + 1)
    except OSError:
        abort(500)

    file_truncated = len(raw_full) > MAX_FILE_BYTES
    if file_truncated:
        raw_full = raw_full[:MAX_FILE_BYTES]

    try:
        text = raw_full.decode("utf-8") if not file_truncated else raw_full.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        text = raw_full.decode("latin-1", errors="replace")

    all_lines = text.splitlines()
    total_lines = len(all_lines)
    first_line_index = 0  # 0-based offset into the original file
    if total_lines > MAX_LINES:
        first_line_index = total_lines - MAX_LINES
        all_lines = all_lines[-MAX_LINES:]
    line_truncated = total_lines > MAX_LINES

    try:
        base = os.path.basename(path)
        size_h = _human_size(size)
        mtime_h = _human_time(mtime)
    except OSError:
        abort(500)

    return jsonify({
        "name":             base,
        "size":             size,
        "size_human":       size_h,
        "mtime":            mtime,
        "mtime_human":      mtime_h,
        "content":          "\n".join(all_lines),
        "lines":            len(all_lines),
        "first_line_index": first_line_index,
        "truncated":        file_truncated or line_truncated,
    })

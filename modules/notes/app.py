# modules/notes/app.py
# ─── SJN Notes — Markdown editor backed by the cloud folder ────────────────
#
# What this module does
# ---------------------
# Provides a markdown notes editor. Files are stored as plain ``.md``
# in the user's existing cloud folder (``data/cloud/<user_id>/…``) so:
#
# * every ``.md`` file the user drops into the cloud shows up here
#   automatically — no duplication, no separate DB;
# * every note created here lives in the cloud, subject to the same
#   storage quota and security checks as any other cloud file.
#
# Notes are scoped to one **user** — ``user_root(user_id) =
# data/cloud/<user_id>`` — and listed **recursively** so users can keep
# notes grouped into subfolders (``projects/recipes.md`` etc.).
#
# Security model
# --------------
# 1. Every storage path is resolved through ``safe_join_under`` so a
#    user-supplied ``../`` is rejected before it ever touches the
#    filesystem.
# 2. Filenames are normalised via ``normalize_storage_filename``.
# 3. Per-note size cap is much lower than the cloud default because the
#    live preview would otherwise lock up the browser on a 200 MB note.
#    See ``MAX_NOTE_BYTES``.
# 4. Storage quota (the cross-module role-based limit in
#    ``toolbox.files.check_storage``) is enforced BEFORE the write.
#
# Routes
# ------
# * ``GET  /notes/``                     -- render notes UI
# * ``GET  /notes/list``                 -- recursive JSON list of .md files
# * ``GET  /notes/raw/<path:p>``         -- raw markdown content
# * ``POST /notes/save``                 -- save (create or overwrite)
# * ``POST /notes/rename``               -- rename / move into another folder
# * ``POST /notes/delete``               -- delete (POST, matches cloud)

from __future__ import annotations

import logging
import os
import uuid
from typing import List, Tuple

from flask import (
    Blueprint,
    abort,
    jsonify,
    render_template,
    request,
)

from toolbox.files import (
    DATA_PATH,
    _format_bytes,
    check_storage,
)
from toolbox.security import (
    get_route_limit,
    normalize_storage_filename,
    safe_join_under,
)
from toolbox.user import get_current_user

bp = Blueprint("notes", __name__)
log = logging.getLogger("notes")


CLOUD_BASE = os.path.join(DATA_PATH, "cloud")
# Per-note size cap. Configurable via ``[notes] max_note`` in
# ``config/security_limits.toml``; the in-code default matches the
# security_limits convention for other routes (200 MB for media,
# 25 MB for memes, etc.) but is much lower for notes because the live
# preview would otherwise lock up the browser on a giant markdown
# file.
DEFAULT_MAX_NOTE = 5 * 1024 * 1024  # 5 MB
MAX_NOTE_BYTES = lambda: get_route_limit("notes", "max_note", DEFAULT_MAX_NOTE)


# ─── Path helpers ───────────────────────────────────────────────────────────


def user_root(user_id: int) -> str:
    """Return the user's cloud root (created on demand)."""
    root = os.path.join(CLOUD_BASE, str(user_id))
    os.makedirs(root, exist_ok=True)
    return root


def resolve_subpath(user_id: int, rel: str) -> Tuple[str, str]:
    """Resolve ``rel`` against user cloud root.

    Returns ``(abs_path, relpath)``. Raises ``ValueError`` on traversal.
    Treats ``None`` and bare ``/`` as the root (`""`).
    """
    if rel is None:
        rel = ""
    if not isinstance(rel, str):
        rel = str(rel)
    rel = rel.lstrip("/")
    root = user_root(user_id)
    try:
        full = safe_join_under(root, rel)
    except ValueError:
        raise
    rel_out = os.path.relpath(full, root)
    return full, rel_out


def split_path(rel: str) -> List[str]:
    """``"a/b/c.md"`` → ``["a", "b", "c.md"]`` (skips empty parts)."""
    return [p for p in (rel or "").split(os.sep) if p]


def _norm_ext(filename: str) -> str:
    _, ext = os.path.splitext(filename or "")
    return ext.lower()


def _is_hidden(name: str) -> bool:
    return bool(name) and name.startswith(".")


# ─── Filename validation ────────────────────────────────────────────────────


def _validate_note_filename(name: str) -> str:
    """Sanitize and validate a note filename.

    The given ``name`` is normalised via ``normalize_storage_filename``
    (strips dangerous chars, collapses whitespaces) and gets ``.md``
    appended if the user didn't include it. Rejects empty or
    pure-dot names. Returns the canonical ``"foo.md"``.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("empty_name")
    # Strip any extension the user typed so we always end up with .md
    # (unless they typed .markdown, in which case we keep it but
    # standardise on `.md`).
    stem, ext = os.path.splitext(cleaned)
    if ext.lower() not in (".md", ".markdown"):
        if ext:
            # Some other extension like .txt or .doc; we ignore it.
            stem = cleaned
        else:
            stem = cleaned
    safe = normalize_storage_filename(stem)
    if not safe or safe in {".", ".."}:
        raise ValueError("invalid_name")
    return safe + ".md"


def _validate_folder_path(path: str) -> str:
    """Allow nested folder paths. Each path segment must normalise to
    a non-empty safe name. Empty means the user chose root.
    """
    if not path:
        return ""
    parts = []
    for seg in (path or "").replace("\\", "/").split("/"):
        if not seg or seg == ".":
            continue
        safe = normalize_storage_filename(seg)
        if not safe or safe in {".."}:
            raise ValueError("invalid_path")
        parts.append(safe)
    return os.sep.join(parts)


def _unique_path(folder: str, name: str) -> str:
    """Return a non-colliding path inside ``folder`` named ``name``.

    Collisions suffix ``_1``, ``_2`` … on the stem. ``.md`` is
    preserved. ``FileExistsError`` if > 10 000 collisions.
    """
    if not name:
        raise ValueError("empty name")
    candidate = os.path.join(folder, name)
    if not os.path.exists(candidate):
        return candidate
    stem, _, ext = name.rpartition(".")
    base_for_split = stem if ext else name
    if not base_for_split:
        base_for_split = name
    for n in range(1, 10_000):
        trial = f"{base_for_split}_{n}{('.' + ext) if ext else ''}"
        candidate = os.path.join(folder, trial)
        if not os.path.exists(candidate):
            return candidate
    raise FileExistsError(f"too many collisions for {name!r}")


# ─── Listing ────────────────────────────────────────────────────────────────


def _list_notes_recursive(root: str) -> dict:
    """Walk ``root`` and return a serialisable list of every ``.md`` file.

    Skip hidden dotfiles. Sort alphabetically by relative path so the
    UI is stable. Each item has ``name``, ``rel`` (slash-joined), and
    ``size`` (bytes). Deleted-on-disk failures are skipped.
    """
    items: List[dict] = []
    abs_root = os.path.abspath(root)
    if not os.path.isdir(abs_root):
        return {"folder": "", "items": items}
    for dirpath, dirnames, filenames in os.walk(abs_root):
        # Skip hidden subtrees so users don't accidentally litter the
        # sidebar with .git/, etc.
        dirnames[:] = [d for d in dirnames if not _is_hidden(d)]
        for fn in filenames:
            if _is_hidden(fn):
                continue
            if _norm_ext(fn) not in (".md", ".markdown"):
                continue
            full = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(full)
                mtime = int(os.path.getmtime(full))
            except OSError:
                continue
            try:
                rel = os.path.relpath(full, abs_root)
            except ValueError:
                continue
            parts = split_path(rel)
            items.append({
                "name": parts[-1] if parts else fn,
                "rel": rel.replace(os.sep, "/"),
                "folder": "/".join(parts[:-1]),
                "size": size,
                "size_human": _format_bytes(size),
                "mtime": mtime,
            })
    items.sort(key=lambda x: x["rel"].lower())
    return {"items": items}


# ─── Routes ─────────────────────────────────────────────────────────────────


@bp.route("/")
def home():
    """Render the notes UI shell — the sidebar + editor pane are
    populated via JS calls to ``bp.list_notes`` and ``bp.raw_note``.
    """
    user = get_current_user()
    if user is None:
        abort(401)
    return render_template("notes.html")


@bp.route("/list")
def list_notes():
    """Return ``{items: [{rel, name, size, …}, …]}`` for every ``.md``
    in the user's cloud tree.
    """
    user = get_current_user()
    if user is None:
        return jsonify({"error": "not_logged_in"}), 401
    return jsonify(_list_notes_recursive(user_root(user["id"])))


@bp.route("/raw/")
@bp.route("/raw/<path:p>")
def raw_note(p=""):
    """Return raw markdown text + metadata for a single note.

    The empty trailing-slash form ``/raw/`` is stacked for the same
    Werkzeug :class:`PathConverter` reason as the cloud blueprint's
    sibling routes (``PathConverter`` requires ``[^/].*?`` and
    therefore refuses to match an empty trailing segment). The
    ``p=""`` default resolves to the user cloud root, which fails
    the ``os.path.isfile`` check below and returns the same 404
    JSON the catch-all would have produced as HTML, but with the
    correct JSON status the in-page JS can interpret.
    """
    user = get_current_user()
    if user is None:
        abort(401)
    try:
        full, rel = resolve_subpath(user["id"], p)
    except ValueError:
        return jsonify({"error": "path_traversal"}), 400
    if not os.path.isfile(full):
        return jsonify({"error": "not_found"}), 404
    if _norm_ext(full) not in (".md", ".markdown"):
        return jsonify({"error": "not_a_note"}), 400
    try:
        size = os.path.getsize(full)
        cap = MAX_NOTE_BYTES()
        if size > cap:
            return jsonify({
                "error": "note_too_large",
                "message": (
                    f"Note is { _format_bytes(size) } > limit { _format_bytes(cap) }."
                ),
                "limit": cap,
                "size": size,
            }), 413
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        return jsonify({"error": "read_failed", "message": str(e)}), 500
    return jsonify({
        "rel": rel.replace(os.sep, "/"),
        "name": os.path.basename(full),
        "content": text,
        "size": size,
        "size_human": _format_bytes(size),
    })


@bp.route("/save", methods=["POST"])
def save_note():
    """Create or overwrite a ``.md`` note.

    Form fields:

    * ``name``    -- filename (``.md`` appended if missing)
    * ``content`` -- raw markdown text; ``<binary>`` is rejected
    * ``folder``  -- optional nested folder ("projects/ideas"); empty
                     for root
    * ``rel``     -- optional. If present and points to an existing
                     note, **updates** that note in place (keeps the
                     path). Otherwise a new file is created at
                     ``<folder><name>.md``.
    """
    user = get_current_user()
    if user is None:
        return jsonify({"error": "not_logged_in"}), 401

    raw_name = (request.form.get("name") or "").strip()
    folder = (request.form.get("folder") or "").strip()
    rel_override = (request.form.get("rel") or "").strip()
    content = request.form.get("content") or ""

    if not isinstance(content, str):
        return jsonify({
            "error": "content_invalid",
            "message": "Content must be plain text.",
        }), 400

    cap = MAX_NOTE_BYTES()
    encoded = content.encode("utf-8")
    if len(encoded) > cap:
        return jsonify({
            "error": "note_too_large",
            "message": (
                f"Note is { _format_bytes(len(encoded)) } > limit "
                f"{ _format_bytes(cap) }."
            ),
            "limit": cap,
        }), 413

    # An explicit `rel` means "update this existing note in place".
    # `folder` is meaningful only for **new** notes (controls where
    # they land). Mixing the two would mean silently moving AND
    # overwriting — ambiguous. Reject the call so the client picks
    # one path and sends it consistently.
    if rel_override and folder:
        return jsonify({
            "error": "invalid_name",
            "message": (
                "Provide either 'rel' (update in place) or "
                "'folder' (create at new location), not both."
            ),
        }), 400

    target_abs = None
    is_update = False

    if rel_override:
        # Verify the existing location, then overwrite in place.
        try:
            existing_abs, existing_rel = resolve_subpath(user["id"], rel_override)
        except ValueError:
            return jsonify({"error": "path_traversal"}), 400
        if not os.path.isfile(existing_abs):
            return jsonify({"error": "not_found"}), 404
        if _norm_ext(existing_abs) not in (".md", ".markdown"):
            return jsonify({"error": "not_a_note"}), 400
        # Renames go through ``/rename`` which is more atomic.
        target_abs = existing_abs
        is_update = True
    else:
        try:
            safe_folder = _validate_folder_path(folder)
        except ValueError:
            return jsonify({
                "error": "invalid_folder",
                "message": "Invalid folder path.",
            }), 400
        try:
            safe_name = _validate_note_filename(raw_name)
        except ValueError as e:
            if str(e) == "empty_name":
                return jsonify({
                    "error": "empty_name",
                    "message": "Please give your note a name.",
                }), 400
            return jsonify({
                "error": "invalid_name",
                "message": "The filename contains illegal characters.",
            }), 400
        try:
            rel_parent, _ = resolve_subpath(user["id"], safe_folder)
        except ValueError:
            return jsonify({"error": "path_traversal"}), 400
        os.makedirs(rel_parent, exist_ok=True)
        try:
            target_abs = _unique_path(rel_parent, safe_name)
        except FileExistsError:
            return jsonify({
                "error": "too_many_collisions",
                "message": "Too many notes share the same name.",
            }), 409

    # Storage check.
    incoming = len(encoded)
    # If updating, exclude the existing file so its bytes aren't
    # counted twice against the quota on overwrite.
    exclude = [target_abs] if is_update and os.path.exists(target_abs) else []
    ok, info = check_storage(user["id"], incoming, exclude_paths=exclude)
    if not ok:
        return jsonify({
            "error": "storage_limit_exceeded",
            "used_human": info["used_human"],
            "limit_human": info["limit_human"],
            "remaining_human": info["remaining_human"],
            "incoming_human": _format_bytes(incoming),
            "would_use_human": _format_bytes(info["would_use"]),
            "message": (
                f"Speicherlimit überschritten. Belegt: {info['used_human']} "
                f"/ Limit: {info['limit_human']}."
            ),
        }), 413

    # Write atomically (write-temp-then-rename) so a crash mid-write
    # can't leave a half-written .md file. The temp filename uses a
    # uuid so two concurrent saves from the same worker (or the
    # same user opening two tabs) never collide. fsync flushes the
    # data to disk before the atomic rename.
    tmp_name = f".{ os.path.basename(target_abs) }.{ uuid.uuid4().hex }.tmp"
    tmp_path = os.path.join(os.path.dirname(target_abs), tmp_name)
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
            if content and not content.endswith("\n"):
                f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target_abs)
    except OSError as e:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return jsonify({
            "error": "write_failed",
            "message": str(e),
        }), 500

    final_rel = os.path.relpath(target_abs, user_root(user["id"]))
    return jsonify({
        "status": "ok",
        "rel": final_rel.replace(os.sep, "/"),
        "name": os.path.basename(target_abs),
        "size": os.path.getsize(target_abs),
        "size_human": _format_bytes(os.path.getsize(target_abs)),
        "created": not is_update,
    })


@bp.route("/rename", methods=["POST"])
def rename_note():
    """Rename / move a note. ``path`` → ``new_name`` (same folder rename)
    or ``path`` → ``new_folder`` (move into another folder)."""
    user = get_current_user()
    if user is None:
        return jsonify({"error": "not_logged_in"}), 401

    rel = (request.form.get("path") or "").strip()
    new_name = (request.form.get("new_name") or "").strip()
    new_folder = (request.form.get("new_folder") or "").strip()
    if not rel or not (new_name or new_folder):
        return jsonify({
            "error": "missing_fields",
            "message": "Old path and either new_name or new_folder are required.",
        }), 400

    try:
        src_abs, _ = resolve_subpath(user["id"], rel)
    except ValueError:
        return jsonify({"error": "path_traversal"}), 400
    abs_root = os.path.abspath(user_root(user["id"]))
    if src_abs == abs_root:
        return jsonify({"error": "cannot_rename_root"}), 400
    if not os.path.isfile(src_abs):
        return jsonify({"error": "not_found"}), 404
    if _norm_ext(src_abs) not in (".md", ".markdown"):
        return jsonify({"error": "not_a_note"}), 400

    if new_folder:
        try:
            safe_folder = _validate_folder_path(new_folder)
        except ValueError:
            return jsonify({"error": "invalid_folder"}), 400
        try:
            target_dir_abs, _ = resolve_subpath(user["id"], safe_folder)
        except ValueError:
            return jsonify({"error": "path_traversal"}), 400
        # Compute the new full path: keep the same filename, just move
        # the directory.
        target_name = os.path.basename(src_abs)
        target_abs = os.path.join(target_dir_abs, target_name)
        # Disallow dropping the file onto itself.
        if os.path.abspath(target_abs) == os.path.abspath(src_abs):
            return jsonify({
                "error": "noop",
                "message": "Source and destination are identical.",
            }), 400
    else:
        try:
            safe_name = _validate_note_filename(new_name)
        except ValueError:
            return jsonify({
                "error": "invalid_name",
                "message": "Invalid filename.",
            }), 400
        parent_dir = os.path.dirname(src_abs)
        try:
            target_abs = _unique_path(parent_dir, safe_name)
        except FileExistsError:
            return jsonify({"error": "too_many_collisions"}), 409

    if os.path.exists(target_abs):
        return jsonify({"error": "already_exists"}), 409
    try:
        os.makedirs(os.path.dirname(target_abs), exist_ok=True)
        os.rename(src_abs, target_abs)
    except OSError as e:
        return jsonify({
            "error": "rename_failed",
            "message": str(e),
        }), 500

    final_rel = os.path.relpath(target_abs, abs_root).replace(os.sep, "/")
    return jsonify({
        "status": "ok",
        "rel": final_rel,
        "name": os.path.basename(target_abs),
    })


@bp.route("/delete", methods=["POST"])
def delete_note():
    """Delete a single ``.md`` file. Folders are NOT recursed — only
    file delete is exposed, keeping the operation explicit."""
    user = get_current_user()
    if user is None:
        return jsonify({"error": "not_logged_in"}), 401

    rel = (request.form.get("path") or "").strip()
    if not rel:
        return jsonify({"error": "missing_path"}), 400
    try:
        target_abs, _ = resolve_subpath(user["id"], rel)
    except ValueError:
        return jsonify({"error": "path_traversal"}), 400
    abs_root = os.path.abspath(user_root(user["id"]))
    if target_abs == abs_root:
        return jsonify({"error": "cannot_delete_root"}), 400
    if not os.path.exists(target_abs):
        return jsonify({"error": "not_found"}), 404
    if os.path.isdir(target_abs):
        return jsonify({
            "error": "is_a_folder",
            "message": "Use the cloud UI to delete folders.",
        }), 400
    if _norm_ext(target_abs) not in (".md", ".markdown"):
        return jsonify({"error": "not_a_note"}), 400
    try:
        os.remove(target_abs)
    except OSError as e:
        return jsonify({
            "error": "delete_failed",
            "message": str(e),
        }), 500
    return jsonify({"status": "ok"})

# modules/cloud/app.py
# Cloud storage with folder support, file previews and a hardened
# upload pipeline.
#
# Security model
# --------------
#
# 1. Every storage path is resolved through :func:`safe_join_under` so a
#    user-supplied ``../`` is rejected before it ever touches the
#    filesystem.
# 2. Every uploaded file is funneled through :func:`validate_upload` with
#    an *allow-list* (union of image / video / audio / doc plus common
#    code / text / archive extensions). Files with missing or
#    disallowed extensions are rejected with the stable code from
#    ``UploadValidationError.code``.
# 3. Per-file byte cap is read from ``config/security_limits.toml``
#    via ``get_route_limit('cloud', 'max_file')``.
# 4. ZIPs go through :func:`safe_extract_zip` which enforces
#    ZIP Slip defence, member-count cap, aggregate-uncompressed cap
#    and rejects encrypted entries.
# 5. After a successful write we run the (optional) AV scan hook; if
#    a scanner reports a hit, we delete the offending file and surface
#    the threat id to the client.
# 6. Storage quota uses ``toolbox.files.check_storage`` with
#    ``exclude_paths=[tmp]`` so the in-progress upload is not
#    double-counted. The cap is enforced BEFORE the move to make quota
#    bypass meaningless (no file is ever written in the final
#    destination before the check passes).
#
# Routes
# ------
#
# * ``GET  /cloud/``                      -- list root
# * ``GET  /cloud/list/<path:p>``         -- list subfolder ``p``
# * ``POST /cloud/upload``                -- upload selected files to ``parent``
# * ``POST /cloud/mkdir``                 -- create folder under ``parent``
# * ``POST /cloud/delete``                -- delete file / folder
# * ``POST /cloud/rename``                -- rename / move into another folder
# * ``GET  /cloud/raw/<path:p>``          -- stream raw bytes (preview media)
# * ``GET  /cloud/preview/<path:p>``      -- JSON preview payload
# * ``GET  /cloud/download/<path:p>``     -- attachment download

from __future__ import annotations

import logging
import mimetypes
import os
import shutil
import uuid
from typing import List, Optional, Tuple

from flask import (
    Blueprint,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from toolbox.files import (
    DATA_PATH,
    _format_bytes,
    _folder_size,
    check_storage,
    get_storage_info,
)
from toolbox.security import (
    ALLOWED_AUDIO_EXTS,
    ALLOWED_AUDIO_MIMES,
    ALLOWED_DOC_EXTS,
    ALLOWED_DOC_MIMES,
    ALLOWED_IMAGE_EXTS,
    ALLOWED_IMAGE_MIMES,
    ALLOWED_VIDEO_EXTS,
    ALLOWED_VIDEO_MIMES,
    AntivirusUnavailableError,
    ErrorCode,
    UploadValidationError,
    ZipSecurityError,
    get_route_limit,
    normalize_storage_filename,
    safe_extract_zip,
    safe_join_under,
    scan_for_malware,
    validate_upload,
)
from toolbox.user import get_current_user

bp = Blueprint("cloud", __name__, template_folder="templates")

log = logging.getLogger("cloud")

CLOUD_BASE = os.path.join(DATA_PATH, "cloud")
# Quarantine bucket for files the AV hook flagged but whose
# destination ``os.remove`` failed (e.g. read-only mount). Keeps a
# flagged file out of the user's quota and alive for ops review.
CLOUD_QUARANTINE = os.path.join(DATA_PATH, "cloud_quarantine")
# Temporary directory used during upload processing. Lives OUTSIDE
# ``data/cloud/<user_id>`` so the storage accounting does NOT mistake
# in-progress bytes for committed storage.
CLOUD_TMP_BASE = os.path.join(DATA_PATH, "cloud_tmp")
os.makedirs(CLOUD_TMP_BASE, exist_ok=True)

# ─── Cloud allow-list ──────────────────────────────────────────────────────
# Union of the toolbox "safe" sets plus carefully-vetted common
# archives / code / text / data formats the cloud target accepts.
#
# The list is the SOURCE OF TRUTH for what may land in the cloud;
# routes must NOT separate this test from the storage write -- it is
# always enforced inside ``_save_one``.
_CLOUD_EXTENSIONS: frozenset = frozenset({
    # Images, video, audio, docs -- as curated in toolbox.security.uploads
    *ALLOWED_IMAGE_EXTS,
    *ALLOWED_VIDEO_EXTS,
    *ALLOWED_AUDIO_EXTS,
    *ALLOWED_DOC_EXTS,
    # Extra documents / data formats
    ".csv", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini",
    ".ini", ".cfg", ".conf", ".log",
    # Code / markup / styles
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx",
    ".py", ".rb", ".go", ".rs", ".java", ".kt", ".swift",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".m", ".sh", ".bash",
    ".php", ".pl", ".lua", ".r", ".sql",
    ".svg",                                  # served with sandbox CSP only
    # Archives the user can stage for the cloud itself
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z", ".rar",
})

_CLOUD_MIMES: frozenset = frozenset({
    *ALLOWED_IMAGE_MIMES,
    *ALLOWED_VIDEO_MIMES,
    *ALLOWED_AUDIO_MIMES,
    *ALLOWED_DOC_MIMES,
    # Best-effort additions; missing means we still rely on extension
    # validation (the allow-list is the authoritative check).
    "text/csv",
    "application/json",
    "application/xml",
    "text/xml",
    "application/yaml",
    "text/yaml",
    "application/x-yaml",
    "application/toml",
    "text/toml",
    "text/x-shellscript",
})

CLOUD_MAX_FILE = lambda: get_route_limit("cloud", "max_file", 200 * 1024 * 1024)
CLOUD_MAX_ZIP_TOTAL = lambda: get_route_limit("cloud", "max_zip_total", 1024 * 1024 * 1024)
CLOUD_MAX_ZIP_MEMBERS = lambda: get_route_limit("cloud", "max_members", 1000)

# Files we will preview as plain ``<pre>`` text up to N bytes. Markdown
# is renderered client-side via marked.js + DOMPurify.
_TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".log",
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx",
    ".py", ".rb", ".go", ".rs", ".java", ".kt", ".swift",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".m", ".sh", ".bash",
    ".php", ".pl", ".lua", ".r", ".sql", ".svg",
})
_IMAGE_EXTENSIONS = frozenset(ALLOWED_IMAGE_EXTS)
_VIDEO_EXTENSIONS = frozenset(ALLOWED_VIDEO_EXTS)
_AUDIO_EXTENSIONS = frozenset(ALLOWED_AUDIO_EXTS)
_MD_EXTENSIONS = frozenset({".md", ".markdown"})

# Hard cap for any text we hand out via the preview endpoint so we
# never accidentally dump a 200 MB CSV into the browser.
_PREVIEW_TEXT_LIMIT_BYTES = 512 * 1024  # 512 KB


# ─── Helpers ───────────────────────────────────────────────────────────────


def _norm_ext(filename: str) -> str:
    _, ext = os.path.splitext(filename or "")
    return ext.lower()


def _is_hidden(name: str) -> bool:
    return bool(name) and name.startswith(".")


def user_root(user_id: int) -> str:
    """Return the ensured-on-disk user root directory."""
    root = os.path.join(CLOUD_BASE, str(user_id))
    os.makedirs(root, exist_ok=True)
    return root


def _resolve_subpath(user_id: int, sub: str) -> Tuple[str, str]:
    """Resolve ``sub`` against user root.

    Returns ``(absolute_path, relative_to_root)``. Raises
    :class:`ValueError` (caught below and surfaced as 400 / JSON
    ``error``) if traversal was attempted.
    """
    if sub is None:
        sub = ""
    if not isinstance(sub, str):
        sub = str(sub)
    # Strip a possible leading '/' so ``/foo`` and ``foo`` resolve to
    # the same target -- this is purely UX, the real traversal check
    # is ``safe_join_under`` below.
    sub = sub.lstrip("/")
    root = user_root(user_id)
    # ``safe_join_under(root, sub)`` walks the user-supplied path under
    # ``root`` and raises if the resolved abs path escapes it.
    try:
        full = safe_join_under(root, sub)
    except ValueError:
        raise
    rel = os.path.relpath(full, root)
    return full, rel


def _unique_path(folder: str, name: str) -> str:
    """Return a non-colliding path inside ``folder`` for ``name``.

    Collisions are resolved by appending ``_1``, ``_2`` … to the stem
    until the path doesn't exist. The extension is preserved.
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
    # 10k collisions in one folder: we refuse rather than spin.
    raise FileExistsError(f"too many collisions for {name!r}")


def _validate_folder_name(name: str) -> str:
    """Sanitize and reject reserved names.

    Returns the sanitized name. Raises :class:`ValueError` with a
    localized-friendly key in :attr:`code` if input is unusable.
    """
    sanitized = normalize_storage_filename(name)
    if not sanitized:
        raise ValueError("invalid_name")
    # Reserved bucket for non-upload bookkeeping if ever introduced.
    if sanitized in {".cloud_meta", "__meta__"}:
        raise ValueError("invalid_name")
    if "." in sanitized and sanitized.split(".", 1)[0] in {"", "."}:
        raise ValueError("invalid_name")
    return sanitized


def _allowed_in_cloud(filename: str, sniffed_mime: str) -> Optional[str]:
    """Check ``filename`` extension against the cloud allow-list.

    Returns the normalised extension on success, ``None`` if rejected.
    MIME is used as belt-and-braces only -- the extension check is the
    authoritative one.
    """
    ext = _norm_ext(filename)
    if not ext:
        return None
    if ext not in _CLOUD_EXTENSIONS:
        return None
    return ext


def _validation_error_response(err: UploadValidationError):
    """Map a single ``UploadValidationError`` to a JSON error response.

    Returns ``(response, status)``. Stable error codes are translated
    to the i18n keys our JS already looks up, falling back to a
    generic ``cloud.error.upload_failed`` on unknown codes.
    """
    code = err.code or ErrorCode.FILE_EMPTY
    body = {
        "error": code,
        "message": str(err),
    }
    return jsonify(body), 400


# ─── Folder listing ────────────────────────────────────────────────────────


def _list_dir(root: str, rel: str) -> dict:
    """Return a serialisable ``{folder, breadcrumbs, items}`` for a
    folder relative to user root.

    ``items`` is a list of ``{name, is_dir, rel, size, ext, mtime}``.
    Order: folders first (alphabetical), then files (alphabetical).
    Hidden dotfiles are included but flagged so the UI can de-emphasise
    them if desired.

    Path-traversal defence is delegated to :func:`_resolve_subpath_from`
    which raises :class:`ValueError` if ``rel`` resolves outside
    ``root``.
    """
    abs_target, rel = _resolve_subpath_from(root, rel)

    items: List[dict] = []
    if os.path.isdir(abs_target):
        with os.scandir(abs_target) as it:
            entries = list(it)
    else:
        entries = []

    folders = [e for e in entries if e.is_dir(follow_symlinks=False)]
    files = [e for e in entries if e.is_file(follow_symlinks=False)]

    folders.sort(key=lambda e: e.name.lower())
    files.sort(key=lambda e: e.name.lower())

    for e in folders:
        items.append({
            "name": e.name,
            "is_dir": True,
            "rel": os.path.join(rel, e.name) if rel else e.name,
            "size": None,
            "ext": None,
            "mtime": int(e.stat().st_mtime),
            "hidden": _is_hidden(e.name),
        })

    for e in files:
        try:
            st = e.stat()
            size = st.st_size
            mtime = int(st.st_mtime)
        except OSError:
            size = None
            mtime = 0
        items.append({
            "name": e.name,
            "is_dir": False,
            "rel": os.path.join(rel, e.name) if rel else e.name,
            "size": size,
            "size_human": _format_bytes(size) if size is not None else "?",
            "ext": _norm_ext(e.name),
            "mtime": mtime,
            "hidden": _is_hidden(e.name),
        })

    return {
        "folder": rel,
        "breadcrumbs": _breadcrumbs(rel),
        "items": items,
    }


def _resolve_subpath_from(root: str, rel: str) -> Tuple[str, str]:
    """Like :func:`_resolve_subpath` but for arbitrary root (used by
    :func:`_list_dir`). Always runs the containment check.
    """
    rel = (rel or "").lstrip("/")
    abs_target = os.path.abspath(os.path.join(root, rel))
    abs_root = os.path.abspath(root)
    if abs_target != abs_root and not abs_target.startswith(abs_root + os.sep):
        raise ValueError("traversal")
    return abs_target, rel


def _breadcrumbs(rel: str) -> List[dict]:
    """Return ``[{label, rel}, ...]`` for breadcrumb trail."""
    rel = (rel or "").strip("/")
    out = [{"label": "🏠", "rel": ""}]
    if not rel:
        return out
    parts = rel.split(os.sep)
    acc: List[str] = []
    for p in parts:
        acc.append(p)
        out.append({"label": p, "rel": "/".join(acc)})
    return out


# ─── Routes ────────────────────────────────────────────────────────────────


@bp.route("/")
def home():
    """Render the cloud UI for the current user.

    The actual folder contents are loaded via JS calls to
    :func:`bp.list_folder` so the same HTML can navigate any
    subfolder without a full page reload.
    """
    user = get_current_user()
    if user is None:
        abort(401)
    storage = get_storage_info(user["id"])
    return render_template(
        "cloud.html",
        items=[],            # populated via fetch on the client
        storage=storage,
        current_path="",
        breadcrumbs=_breadcrumbs(""),
    )


@bp.route("/list/<path:p>")
def list_folder(p):
    """Return a JSON listing for subfolder ``p`` (root when ``p`` empty)."""
    user = get_current_user()
    if user is None:
        return jsonify({"error": "not_logged_in"}), 401
    try:
        listing = _list_dir(user_root(user["id"]), p)
    except ValueError:
        return jsonify({"error": "path_traversal"}), 400
    return jsonify(listing)


@bp.route("/upload", methods=["POST"])
def upload():
    """Hardened upload route.

    Accepted form fields:

    * ``parent`` -- server-relative folder to upload into (root when
      empty)
    * ``files``  -- one or more file inputs; all entries are validated
      against the cloud allow-list, size cap and ZIP rules.
    * ``paths``  -- OPTIONAL JSON array of original directory names so
      relative uploads preserve folder structure (e.g. dropping a
      ``docs/`` subtree). Order matches ``files`` 1:1.
    """
    user = get_current_user()
    if user is None:
        return jsonify({"error": "not_logged_in"}), 401

    parent_rel = (request.form.get("parent") or "").strip()
    try:
        parent_abs, _ = _resolve_subpath(user["id"], parent_rel)
    except ValueError:
        return jsonify({"error": "path_traversal",
                        "message": "Uploads root blocked"}), 400

    if not os.path.isdir(parent_abs):
        return jsonify({"error": "parent_missing",
                        "message": "Zielordner fehlt"}), 400

    files = request.files.getlist("files")
    if not files:
        return jsonify({
            "error": ErrorCode.FILE_EMPTY,
            "message": "Keine Dateien ausgewählt.",
        }), 400

    # Per-file originalName preservation: each browser-supplied
    # ``File.filename`` MAY include a subfolder (``docs/notes.md``).
    # Werkzeug strips slashes for ``name`` but keeps them in
    # ``filename``; we rely on the latter and sanitise.
    os.makedirs(CLOUD_TMP_BASE, exist_ok=True)
    tmp = os.path.join(CLOUD_TMP_BASE, f"{user['id']}_{uuid.uuid4()}")
    os.makedirs(tmp, exist_ok=True)
    written_files: List[Tuple[str, str, int]] = []  # (tmp_path, original_rel, size)
    max_file_bytes = CLOUD_MAX_FILE()

    try:
        for fs in files:
            original_name = fs.filename or "upload.bin"
            # Prevent wildly long filenames from breaking disk limits.
            if len(original_name) > 1024:
                original_name = original_name[-1024:]
            try:
                ext = _allowed_in_cloud(original_name, fs.mimetype or "")
            except Exception:
                ext = None
            if not ext:
                # Fall back to the toolbox validator which gives us a
                # stable error code so the client can localize.
                try:
                    validate_upload(
                        fs,
                        allowed_exts=_CLOUD_EXTENSIONS,
                        allowed_mimes=_CLOUD_MIMES,
                        max_bytes=max_file_bytes,
                    )
                except UploadValidationError as e:
                    shutil.rmtree(tmp, ignore_errors=True)
                    return _validation_error_response(e)
                # validate_upload passed but we ignored it earlier --
                # should not happen, but bail just in case.
                continue

            # Per-file byte cap.
            try:
                validate_upload(
                    fs,
                    allowed_exts=_CLOUD_EXTENSIONS,
                    allowed_mimes=_CLOUD_MIMES,
                    max_bytes=max_file_bytes,
                )
            except UploadValidationError as e:
                shutil.rmtree(tmp, ignore_errors=True)
                return _validation_error_response(e)

            # Persist to the per-upload tmp dir. ZIPs are extracted
            # ``safe_extract_zip``-style after this so members land
            # below ``tmp/zip/`` for quota accounting.
            clean_name = normalize_storage_filename(original_name) or "upload"
            tmp_path = os.path.join(tmp, clean_name)
            os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
            fs.save(tmp_path)
            written_files.append((tmp_path, original_name, fs.content_length or 0))

        # Handle ZIPs (if any of the saved files is a .zip, route it
        # through the safe extractor).
        zip_targets: List[Tuple[str, str]] = []
        non_zip_written: List[Tuple[str, str]] = []
        for tmp_path, original_name, _ in written_files:
            if _norm_ext(original_name) == ".zip":
                zip_targets.append((tmp_path, original_name))
            else:
                non_zip_written.append((tmp_path, original_name))

        tmp_root = tmp
        zip_inside = os.path.join(tmp_root, "_zip_inside_")
        extracted_total = 0
        if zip_targets:
            os.makedirs(zip_inside, exist_ok=True)
        for zp, oname in zip_targets:
            try:
                written = safe_extract_zip(
                    zp,
                    zip_inside,
                    allowed_exts=_CLOUD_EXTENSIONS,
                    max_members=CLOUD_MAX_ZIP_MEMBERS(),
                    max_total_uncompressed=CLOUD_MAX_ZIP_TOTAL(),
                )
            except ZipSecurityError as e:
                shutil.rmtree(tmp, ignore_errors=True)
                return jsonify({
                    "error": "zip_rejected",
                    "message": str(e),
                    # i18n key the client looks up for stable UX.
                    "key": (
                        "cloud.error.zip_encrypted"
                        if "verschlüsselt" in str(e) or "encrypted" in str(e).lower()
                        else "cloud.error.zip_rejected"
                    ),
                }), 400
            extracted_total += written
            # Remove the ZIP itself from the tmp; it's been replaced.
            try:
                os.remove(zp)
            except OSError:
                pass

        # Compute the total incoming bytes for quota.
        # We delete the original ZIP archives BEFORE this walk (above,
        # ``os.remove(zp)``), so ``tmp`` only holds the freshly-
        # extracted members plus any non-zip uploads. Walking it
        # gives the exact number of bytes that will live in the
        # user's tree after the move.
        incoming = 0
        for root_d, _, fs_in_tmp in os.walk(tmp):
            for fn in fs_in_tmp:
                fp = os.path.join(root_d, fn)
                try:
                    incoming += os.path.getsize(fp)
                except OSError:
                    pass

        # Storage quota check.
        ok, info = check_storage(
            user["id"], incoming, exclude_paths=[tmp]
        )
        if not ok:
            shutil.rmtree(tmp, ignore_errors=True)
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

        # Move everything from ``tmp`` (and ``_zip_inside_``) into the
        # user's final tree. Two key safety rules:
        #   • source paths are walked via ``os.walk`` and every
        #     destination is recomputed through ``safe_join_under``
        #     which rejects any path that would escape ``parent_abs``
        #     -- defends against a crafted zip member reusing ``..``.
        #   • collisions on the destination get a ``_N`` suffix.
        user_root_abs = os.path.abspath(parent_abs)
        for src_root, _, fnames in os.walk(tmp):
            for fn in fnames:
                src = os.path.join(src_root, fn)
                rel = os.path.relpath(src, tmp)
                # The intermediate extractor dir is mirrored 1:1 in the
                # destination. We replace it with the root itself so
                # ``_zip_inside_/foo`` becomes ``foo``.
                if rel.startswith("_zip_inside_"):
                    rel = rel[len("_zip_inside_"):].lstrip(os.sep)
                if not rel:
                    continue
                rel_dir, rel_name = os.path.split(rel)
                safe_rel_dir = normalize_storage_filename(rel_dir) or ""
                if rel_dir and not safe_rel_dir:
                    # Unusable folder name from a zip member -- drop
                    # into the parent instead so we don't lose the
                    # whole archive over a single ugly entry.
                    safe_rel_dir = ""
                safe_rel_name = (
                    normalize_storage_filename(rel_name) or "file"
                )
                target_dir_rel = (
                    os.path.join(parent_rel, safe_rel_dir) if safe_rel_dir
                    else parent_rel
                )
                try:
                    target_dir_abs, _ = _resolve_subpath(
                        user["id"], target_dir_rel
                    )
                except ValueError:
                    # Skip -- malicious entry slipped through.
                    continue
                os.makedirs(target_dir_abs, exist_ok=True)
                try:
                    target_path = _unique_path(target_dir_abs, safe_rel_name)
                except (ValueError, FileExistsError):
                    continue
                try:
                    shutil.move(src, target_path)
                except OSError:
                    continue
                # AV hook -- configured scanner runs on the file in
                # its final destination. A refusal or unrecoverable
                # scanner error refuses the upload. Bypassing the
                # scanner on a generic exception would silently
                # weaken the user's defence in depth, so log + refuse
                # instead.
                try:
                    clean, threat = scan_for_malware(target_path)
                except AntivirusUnavailableError:
                    # Scanner itself said "I cannot scan right now".
                    # Accept the file, but log loudly for ops review
                    # so the gap is visible without blocking uploads.
                    log.warning(
                        "antivirus unavailable for %s; accepted unscanned",
                        target_path,
                    )
                    clean, threat = True, None
                except Exception as exc:
                    log.error(
                        "antivirus scanner raised for %s: %s",
                        target_path, exc,
                    )
                    # Treat the file as suspicious and refuse. This
                    # closes the "scanner error = silent pass" hole.
                    clean, threat = False, f"scanner_error: {type(exc).__name__}"
                if not clean:
                    removed = False
                    try:
                        os.remove(target_path)
                        removed = True
                    except OSError as exc:
                        log.error(
                            "malware cleanup failed for %s (%s); "
                            "moving to quarantine",
                            target_path, exc,
                        )
                        try:
                            os.makedirs(CLOUD_QUARANTINE, exist_ok=True)
                            qdir = os.path.join(
                                CLOUD_QUARANTINE, str(user["id"])
                            )
                            os.makedirs(qdir, exist_ok=True)
                            qpath = os.path.join(
                                qdir,
                                f"{int.from_bytes(os.urandom(6), 'big')}_"
                                f"{os.path.basename(target_path)}",
                            )
                            shutil.move(target_path, qpath)
                            removed = True
                            log.error("quarantined to %s", qpath)
                        except Exception as qexc:
                            log.error(
                                "quarantine move failed for %s: %s",
                                target_path, qexc,
                            )
                    shutil.rmtree(tmp, ignore_errors=True)
                    return jsonify({
                        "error": "malware_detected",
                        "message": f"Datei abgelehnt: {threat or 'threat detected'}",
                        "quarantined": removed,
                    }), 400

        # Done.
        return jsonify({
            "status": "ok",
            "parent": parent_rel,
            "uploaded": [
                os.path.relpath(p, tmp) for p, _, _ in written_files
            ],
        })
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@bp.route("/mkdir", methods=["POST"])
def mkdir():
    """Create a folder under ``parent`` named ``name``."""
    user = get_current_user()
    if user is None:
        return jsonify({"error": "not_logged_in"}), 401

    parent_rel = (request.form.get("parent") or "").strip()
    raw_name = (request.form.get("name") or "").strip()
    if not raw_name:
        return jsonify({
            "error": "empty_name",
            "message": "Der Ordnername darf nicht leer sein.",
        }), 400
    try:
        clean_name = _validate_folder_name(raw_name)
    except ValueError as e:
        msg_key = "cloud.error.invalid_name"
        if str(e) == "invalid_name":
            return jsonify({
                "error": "invalid_name",
                "message": "Ungültiger Ordnername.",
                "key": msg_key,
            }), 400
        return jsonify({"error": "invalid_name"}), 400

    try:
        parent_abs, _ = _resolve_subpath(user["id"], parent_rel)
    except ValueError:
        return jsonify({"error": "path_traversal"}), 400

    if not os.path.isdir(parent_abs):
        return jsonify({"error": "parent_missing"}), 400

    target_abs = os.path.join(parent_abs, clean_name)
    if os.path.exists(target_abs):
        return jsonify({
            "error": "already_exists",
            "message": f"'{clean_name}' existiert bereits.",
        }), 409

    os.makedirs(target_abs, exist_ok=False)
    rel = os.path.relpath(target_abs, user_root(user["id"]))
    return jsonify({
        "status": "ok",
        "folder": rel.replace(os.sep, "/"),
        "name": clean_name,
    })


@bp.route("/delete", methods=["POST"])
def delete():
    """Delete a file or folder at ``path`` (recursive for folders)."""
    user = get_current_user()
    if user is None:
        return jsonify({"error": "not_logged_in"}), 401

    rel = (request.form.get("path") or "").strip()
    if not rel:
        return jsonify({"error": "missing_path"}), 400

    try:
        target_abs, _ = _resolve_subpath(user["id"], rel)
    except ValueError:
        return jsonify({"error": "path_traversal"}), 400

    abs_root = os.path.abspath(user_root(user["id"]))
    if target_abs == abs_root:
        return jsonify({"error": "cannot_delete_root"}), 400

    if not os.path.exists(target_abs):
        return jsonify({"error": "not_found"}), 404

    try:
        if os.path.isdir(target_abs):
            shutil.rmtree(target_abs)
        else:
            try:
                os.remove(target_abs)
            except IsADirectoryError:
                shutil.rmtree(target_abs)
    except OSError as e:
        # Permission / read-only mount surfaced as clean JSON 500
        # instead of an HTML error page.
        return jsonify({
            "error": "delete_failed",
            "message": str(e),
        }), 500

    return jsonify({"status": "ok"})


@bp.route("/rename", methods=["POST"])
def rename():
    """Rename ``path`` to ``new_name``. Same folder = simple rename,
    different folder = move.
    """
    user = get_current_user()
    if user is None:
        return jsonify({"error": "not_logged_in"}), 401

    rel = (request.form.get("path") or "").strip()
    new_name = (request.form.get("new_name") or "").strip()
    if not rel or not new_name:
        return jsonify({"error": "missing_fields"}), 400

    try:
        src_abs, _ = _resolve_subpath(user["id"], rel)
    except ValueError:
        return jsonify({"error": "path_traversal"}), 400

    abs_root = os.path.abspath(user_root(user["id"]))
    if src_abs == abs_root:
        return jsonify({"error": "cannot_rename_root"}), 400

    if not os.path.exists(src_abs):
        return jsonify({"error": "not_found"}), 404

    is_dir = os.path.isdir(src_abs)
    try:
        clean_name = (
            _validate_folder_name(new_name) if is_dir
            else (normalize_storage_filename(new_name) or "")
        )
    except ValueError:
        return jsonify({"error": "invalid_name"}), 400
    if not clean_name:
        return jsonify({"error": "invalid_name"}), 400

    parent_dir = os.path.dirname(src_abs)
    target_abs = os.path.join(parent_dir, clean_name)
    if os.path.exists(target_abs):
        return jsonify({"error": "already_exists"}), 409
    try:
        target_final = _unique_path(parent_dir, clean_name)
    except FileExistsError:
        return jsonify({"error": "already_exists"}), 409

    try:
        os.rename(src_abs, target_final)
    except OSError as e:
        return jsonify({"error": "rename_failed",
                        "message": str(e)}), 500

    new_rel = os.path.relpath(target_final, abs_root)
    return jsonify({
        "status": "ok",
        "path": new_rel.replace(os.sep, "/"),
    })


# ─── Stream / preview ──────────────────────────────────────────────────────


@bp.route("/raw/<path:p>")
def raw(p):
    """Stream raw bytes for ``<path:p>``.

    Used by the in-page ``<img>``, ``<video>``, ``<audio>`` and
    ``<iframe sandbox>`` previews. Always emits a CSP header that
    neuters JavaScript so an ``.html`` / ``.svg`` upload opened
    directly cannot execute stored XSS.
    """
    user = get_current_user()
    if user is None:
        abort(401)

    try:
        full, rel = _resolve_subpath(user["id"], p)
    except ValueError:
        abort(400)
    if not os.path.isfile(full):
        abort(404)

    # Best-effort Content-Type from the filename. Browsers ignore
    # these MIMEs when CSP refuses script, which is the point.
    mime, _ = mimetypes.guess_type(full)
    if not mime:
        mime = "application/octet-stream"

    resp = send_file(
        full,
        mimetype=mime,
        conditional=True,        # honour Range so <video> scrubbing works
        max_age=0,
    )
    # Sandbox the bytes so the browser can render them as media
    # (<img>, <video>, <audio>) without trusting the bytes themselves
    # if the user navigates straight to the URL.
    # Tighten CSP: ``style-src 'none'`` so SVGs / HTML can't pull in
    # external stylesheets via @import. The ``sandbox`` token and
    # ``default-src 'none'`` neuter scripts, forms and popups; image
    # and media-src allow ``self`` so the in-page <img>/<video>/<audio>
    # tags can still pull bytes without a same-origin GET to the
    # literal raw URL (each is a same-origin GET anyway, so this is
    # belt-and-braces).
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; img-src 'self' data: blob:; "
        "media-src 'self' data: blob:; style-src 'none'; "
        "sandbox; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'self';"
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.route("/preview/<path:p>")
def preview(p):
    """Return a JSON preview payload for ``p``.

    * ``text`` (txt, code, csv, log) -> ``{type: 'text', content, truncated, bytes}``
    * ``markdown`` (md)              -> ``{type: 'markdown', content}`` (raw md; client renders)
    * ``image``                      -> ``{type: 'image', url: '/cloud/raw/<p>'}``
    * ``video``                      -> ``{type: 'video', url: '/cloud/raw/<p>'}``
    * ``audio``                      -> ``{type: 'audio', url: '/cloud/raw/<p>'}``
    * else                           -> ``{type: 'binary', url: '/cloud/download/<p>'}``
    """
    user = get_current_user()
    if user is None:
        abort(401)
    try:
        full, rel = _resolve_subpath(user["id"], p)
    except ValueError:
        return jsonify({"error": "path_traversal"}), 400
    if not os.path.isfile(full):
        abort(404)

    ext = _norm_ext(full)
    size = os.path.getsize(full)

    raw_url = url_for("cloud.raw", p=_safe_url_path(rel))
    download_url = url_for("cloud.download", p=_safe_url_path(rel))

    if ext in _MD_EXTENSIONS:
        try:
            data = _read_capped(full, _PREVIEW_TEXT_LIMIT_BYTES)
            return jsonify({
                "type": "markdown",
                "content": data["text"],
                "truncated": data["truncated"],
                "bytes": size,
                "raw_url": raw_url,
                "download_url": download_url,
            })
        except OSError:
            return jsonify({"error": "read_failed"}), 500

    if ext in _IMAGE_EXTENSIONS:
        return jsonify({
            "type": "image",
            "url": raw_url,
            "bytes": size,
            "download_url": download_url,
        })
    if ext in _VIDEO_EXTENSIONS:
        return jsonify({
            "type": "video",
            "url": raw_url,
            "bytes": size,
            "download_url": download_url,
        })
    if ext in _AUDIO_EXTENSIONS:
        return jsonify({
            "type": "audio",
            "url": raw_url,
            "bytes": size,
            "download_url": download_url,
        })
    if ext in _TEXT_EXTENSIONS:
        try:
            data = _read_capped(full, _PREVIEW_TEXT_LIMIT_BYTES)
            return jsonify({
                "type": "text",
                "content": data["text"],
                "truncated": data["truncated"],
                "bytes": size,
                "raw_url": raw_url,
                "download_url": download_url,
            })
        except OSError:
            return jsonify({"error": "read_failed"}), 500

    return jsonify({
        "type": "binary",
        "url": download_url,
        "bytes": size,
        "download_url": download_url,
        "raw_url": raw_url,
    })


def _safe_url_path(rel: str) -> str:
    """Re-quote a relative path so Werkzeug's ``<path:p>`` matcher
    doesn't get confused by slashes inside segments.  Flask already
    routes ``<path:p>`` so this is purely cosmetic -- the bytes the
    client sees match the bytes on disk once Werkzeug has decoded
    them again.
    """
    # Use forward slashes consistently so cross-platform paths work.
    rel = rel.replace(os.sep, "/")
    # Werkzeug decodes percent-encoding itself; we re-quote segments
    # so things like ``My Doc.pdf`` keep their space.
    from urllib.parse import quote
    return "/".join(quote(seg, safe="") for seg in rel.split("/"))


def _read_capped(path: str, limit: int) -> dict:
    """Read up to ``limit`` bytes from ``path`` as text.

    Returns ``{"text", "truncated"}``. ``truncated`` is True when the
    file was longer than ``limit``. We intentionally do NOT replace
    undecodable bytes by exception -- that aborts large files; a
    fallback ``replace`` is the right behaviour for text previews.
    """
    truncated = False
    with open(path, "rb") as f:
        raw = f.read(limit + 1)
    if len(raw) > limit:
        truncated = True
        raw = raw[:limit]
    text = raw.decode("utf-8", errors="replace")
    return {"text": text, "truncated": truncated}


@bp.route("/download/<path:p>")
def download(p):
    """Send ``p`` as an attachment download."""
    user = get_current_user()
    if user is None:
        abort(401)
    try:
        full, rel = _resolve_subpath(user["id"], p)
    except ValueError:
        abort(400)
    if not os.path.isfile(full):
        abort(404)
    return send_file(full, as_attachment=True)


# Keep the old GET-style delete route redirecting to the home page so
# any pre-existing bookmark doesn't 404. The new endpoint is the
# POST ``/cloud/delete`` route above.
@bp.route("/delete/<path:p>", methods=["GET"])
def delete_redirect(p):
    return redirect(url_for("cloud.home"))

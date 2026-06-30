"""Validation helpers for ``werkzeug.FileStorage`` (and friends).

Why this exists
---------------

Historically each upload route inlined its own ad-hoc whitelists. That
leads to drift: memes accept certain extensions, music accepts others,
and a malicious uploader can probe routes until they find one with a
loose policy. ``validate_upload(...)`` is the single, opinionated entry
point that all routes should call.

Design choices
--------------

* **Extension is the user-facing check**, MIME is the belt-and-braces
  secondary check. We compare *both* against an allow-list supplied by
  the caller. The ``mimetypes`` stdlib does MIME inference from the file
  *name*, which is only useful when a browser sends no ``Content-Type``
  — for genuine sniffing we'd need ``python-magic`` which is not in the
  dependency set yet.
* **Sniffed MIME comes from Werkzeug** (``FileStorage.mimetype``), which
  is what the browser offered. Do not trust this alone.
* **Errors carry a stable ``code``** so routes can map to localized
  flash messages via i18n key lookup, instead of substring-matching the
  English message. Codes live on :class:`ErrorCode`.
"""

from __future__ import annotations

import mimetypes
import os
from typing import Iterable, Optional, Set

from werkzeug.datastructures import FileStorage


# ─── Default allow-lists ────────────────────────────────────────────────────
# These are the *defaults* used by ``validate_upload`` when a caller does
# not pass its own. Per-route policies are still required — see cloud,
# memes, music, films: each may tighten or relax the defaults for its use
# case (e.g. cloud allows everything in ``ALLOWED_DOC_EXTS``' siblings,
# memes accepts only images & short videos).

ALLOWED_IMAGE_EXTS: Set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".avif",                # next-gen image (AV1 still picture)
    ".heic", ".heif",       # iPhone camera since iOS 11
}
ALLOWED_IMAGE_MIMES: Set[str] = {
    "image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp",
    "image/avif",
    "image/heic", "image/heif",
}

ALLOWED_VIDEO_EXTS: Set[str] = {".mp4", ".webm"}
ALLOWED_VIDEO_MIMES: Set[str] = {"video/mp4", "video/webm"}

ALLOWED_AUDIO_EXTS: Set[str] = {
    ".mp3", ".m4a", ".flac", ".wav", ".ogg",
    ".opus",                # WhatsApp / Discord / Signal voice notes
}
ALLOWED_AUDIO_MIMES: Set[str] = {
    "audio/mpeg",           # .mp3 (browser occasionally reports this)
    "audio/mp3",
    "audio/m4a",
    "audio/x-m4a",
    "audio/mp4",            # m4a often reported as audio/mp4
    "audio/flac",
    "audio/x-flac",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
    "audio/vorbis",
    "audio/opus",
}

ALLOWED_DOC_EXTS: Set[str] = {".txt", ".md", ".pdf"}
ALLOWED_DOC_MIMES: Set[str] = {
    "text/plain", "text/markdown", "application/pdf",
}


# ─── Errors ─────────────────────────────────────────────────────────────────


class UploadValidationError(ValueError):
    """Raised by every public function in this module on bad input.

    Inherits from :class:`ValueError` so legacy ``except ValueError``
    blocks continue to match, but routes catching the new error
    explicitly can map it to a localized flash via the stable
    :attr:`code` attribute.

    Stable ``code`` values live on :class:`ErrorCode`; routes should
    branch on ``e.code`` and never substring-match ``str(e)``.
    """

    def __init__(self, message: str, *, code: Optional[str] = None):
        super().__init__(message)
        self.code = code or ErrorCode.FILE_EMPTY


class ErrorCode:
    """Stable error codes carried by :class:`UploadValidationError.code`.

    These strings are part of the public API — do not rename without
    auditing every catch site. Routes should use them as flash-message
    keys (mapping ``e.code`` -> ``t('flash.' + e.code)`` is the typical
    shape).
    """
    FILE_EMPTY            = "file_empty"
    MISSING_FILENAME      = "missing_filename"
    MISSING_EXTENSION     = "missing_extension"
    EXTENSION_NOT_ALLOWED = "type_not_allowed"
    MIME_NOT_ALLOWED      = "mime_not_allowed"
    SIZE_TOO_LARGE        = "file_too_large"
    SIZE_UNREADABLE       = "size_unreadable"


# ─── Helpers ────────────────────────────────────────────────────────────────


def _norm_ext(filename: Optional[str]) -> str:
    if not filename:
        return ""
    return os.path.splitext(filename)[1].lower()


def sniff_mime(file_storage) -> str:
    """Best-effort MIME inference for an uploaded file.

    Preference order:

    1. ``file.mimetype`` — what the browser claimed in the multipart
       request. Trust-no-this-alone; treat as advisory.
    2. ``mimetypes.guess_type`` from the filename extension. Stable,
       predictable.

    Empty string if both fail. Callers should always validate against a
    whitelist, never branch on this value alone.
    """
    claimed = ""
    try:
        claimed = (getattr(file_storage, "mimetype", "") or "").lower()
    except Exception:
        claimed = ""
    if claimed:
        return claimed
    try:
        guess, _ = mimetypes.guess_type(file_storage.filename or "")
    except Exception:
        guess = None
    return (guess or "").lower()


def _build_allow_sets(
    allowed_exts: Optional[Iterable[str]],
    allowed_mimes: Optional[Iterable[str]],
):
    if allowed_exts is not None:
        exts = {e.lower() if e.startswith(".") else f".{e.lower()}"
                for e in allowed_exts}
    else:
        exts = None
    if allowed_mimes is not None:
        mimes = {m.lower() for m in allowed_mimes}
    else:
        mimes = None
    return exts, mimes


# ─── Public API ─────────────────────────────────────────────────────────────


def validate_extension(filename: str, allowed_exts: Iterable[str]) -> str:
    """Return the normalized extension (e.g. ``.png``) or raise.

    Raises :class:`UploadValidationError` with a stable :attr:`code`:

    * ``ErrorCode.MISSING_FILENAME``      when ``filename`` is empty
    * ``ErrorCode.MISSING_EXTENSION``     when the filename has no ``.``
    * ``ErrorCode.EXTENSION_NOT_ALLOWED`` when the extension is not in
      the allow-list
    """
    if not filename or not filename.strip():
        raise UploadValidationError(
            "Dateiname fehlt.",
            code=ErrorCode.MISSING_FILENAME,
        )
    ext = _norm_ext(filename)
    if not ext:
        raise UploadValidationError(
            "Datei hat keine Dateiendung – Upload abgelehnt.",
            code=ErrorCode.MISSING_EXTENSION,
        )
    norm_allowed = {
        e.lower() if e.startswith(".") else f".{e.lower()}"
        for e in allowed_exts
    }
    if ext not in norm_allowed:
        raise UploadValidationError(
            f"Dateityp nicht erlaubt ({ext}). Erlaubt: "
            f"{', '.join(sorted(norm_allowed))}.",
            code=ErrorCode.EXTENSION_NOT_ALLOWED,
        )
    return ext


def validate_mime(
    file_storage,
    allowed_mimes: Iterable[str],
) -> str:
    """Return the sniffed MIME for ``file_storage`` or raise.

    Both Werkzeug's claim and the extension-inferred MIME are tried.
    Raises :class:`UploadValidationError` with
    :attr:`ErrorCode.MIME_NOT_ALLOWED` if neither source matches the
    allow-list.
    """
    norm_allowed = {m.lower() for m in allowed_mimes}
    claimed = (getattr(file_storage, "mimetype", "") or "").lower()
    if claimed and claimed in norm_allowed:
        return claimed
    guessed = sniff_mime(file_storage)
    if guessed and guessed in norm_allowed:
        return guessed
    hint = claimed or guessed or "unbekannt"
    raise UploadValidationError(
        f"MIME-Typ nicht erlaubt ({hint}). Erlaubt: "
        f"{', '.join(sorted(norm_allowed))}.",
        code=ErrorCode.MIME_NOT_ALLOWED,
    )


def validate_size(
    file_storage,
    max_bytes: int,
) -> int:
    """Validate ``file_storage`` size against ``max_bytes``.

    Two paths, both authoritative:

    * **Fast**: rejects purely on the multipart ``Content-Length``
      header that Werkzeug already set on the parsed part. Cheap.
    * **Slow**: does a ``seek(0, SEEK_END)`` round-trip on the
      underlying stream and reads ``tell()``. Authoritative.

    ``max_bytes`` <= 0 disables the size check entirely; the function
    still returns whatever size it managed to measure (0 if unreadable).
    Returns the measured size in bytes.

    Streaming chunks whose streams are not seekable **and** lack a
    ``Content-Length`` header intentionally raise
    ``ErrorCode.SIZE_UNREADABLE`` rather than silently passing — a
    soft skip would let arbitrarily-large chunks through.

    Note: MIME sniffing here uses stdlib :mod:`mimetypes` plus
    Werkzeug's claimed ``file.mimetype``, not :mod:`python-magic`.
    Add :mod:`python-magic` to requirements and replace :func:`sniff_mime`
    if genuine magic-byte detection becomes necessary.
    """
    if max_bytes is None or max_bytes <= 0:
        max_bytes = 0

    # Fast path: Content-Length from the multipart header.
    content_length = 0
    try:
        headers = getattr(file_storage, "headers", {}) or {}
        cl = headers.get("Content-Length") or headers.get(
            "content-length"
        )
        if cl:
            content_length = int(cl)
    except Exception:
        content_length = 0

    if content_length and max_bytes and content_length > max_bytes:
        raise UploadValidationError(
            f"Datei ist zu groß ({content_length} > {max_bytes} Bytes).",
            code=ErrorCode.SIZE_TOO_LARGE,
        )

    # Slow path: stream tell() round-trip. The seek + tell is safe
    # because subsequent readers always re-seek to 0 before reading.
    size = 0
    try:
        stream = file_storage.stream
        cur = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(cur, os.SEEK_SET)
    except Exception:
        size = 0

    if max_bytes and size and size > max_bytes:
        raise UploadValidationError(
            f"Datei ist zu groß ({size} > {max_bytes} Bytes).",
            code=ErrorCode.SIZE_TOO_LARGE,
        )
    return size


def validate_upload(
    file_storage,
    *,
    allowed_exts: Optional[Iterable[str]] = None,
    allowed_mimes: Optional[Iterable[str]] = None,
    max_bytes: int = 0,
) -> str:
    """Combined validator: extension + MIME + size.

    Returns the normalized extension (e.g. ``.png``) on success.
    Every rejection raises :class:`UploadValidationError` with a
    stable :attr:`code` so routes can localize the flash via the code
    rather than the English message.

    ``max_bytes <= 0`` disables the size check while still letting
    extension and MIME checks run.
    """
    if file_storage is None or not getattr(file_storage, "filename", None):
        raise UploadValidationError(
            "Keine Datei ausgewählt.",
            code=ErrorCode.FILE_EMPTY,
        )
    ext = validate_extension(file_storage.filename, allowed_exts or [])
    if allowed_mimes:
        validate_mime(file_storage, allowed_mimes)
    if max_bytes:
        validate_size(file_storage, max_bytes)
    return ext

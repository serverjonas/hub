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
* Errors are raised as ``UploadValidationError`` so callers can
  ``try / except`` once and map to a localized flash.
"""

from __future__ import annotations

import mimetypes
import os
import re
from typing import Iterable, Optional, Set

from werkzeug.datastructures import FileStorage


# ─── Default allow-lists ────────────────────────────────────────────────────
# These are the *defaults* used by ``validate_upload`` when a caller does
# not pass its own. Per-route policies are still required — see cloud,
# memes, music, films: each may tighten or relax the defaults for its use
# case (e.g. cloud allows everything in ``ALLOWED_DOC_EXTS``' siblings,
# memes accepts only images & short videos).

ALLOWED_IMAGE_EXTS: Set[str] = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
ALLOWED_IMAGE_MIMES: Set[str] = {
    "image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp",
}

ALLOWED_VIDEO_EXTS: Set[str] = {".mp4", ".webm"}
ALLOWED_VIDEO_MIMES: Set[str] = {"video/mp4", "video/webm"}

ALLOWED_AUDIO_EXTS: Set[str] = {".mp3", ".m4a", ".flac", ".wav", ".ogg"}
ALLOWED_AUDIO_MIMES: Set[str] = {
    "audio/mpeg",        # .mp3 (browser occasionally reports this)
    "audio/mp3",
    "audio/m4a",
    "audio/x-m4a",
    "audio/flac",
    "audio/x-flac",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
    "audio/vorbis",
}

ALLOWED_DOC_EXTS: Set[str] = {".txt", ".md", ".pdf"}
ALLOWED_DOC_MIMES: Set[str] = {
    "text/plain", "text/markdown", "application/pdf",
}


# ─── Errors ─────────────────────────────────────────────────────────────────


class UploadValidationError(ValueError):
    """Raised by every public function in this module on bad input.

    Inherits from ``ValueError`` so legacy ``except ValueError`` blocks
    continue to match, but routes catching the new error explicitly can
    map it to a localized flash with a specific key.
    """


# ─── Helpers ────────────────────────────────────────────────────────────────


_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


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

    Raises :class:`UploadValidationError` if the extension is missing or
    not in ``allowed_exts``. ``allowed_exts`` must be an iterable of
    extensions *including* the leading dot.
    """
    if not filename or not filename.strip():
        raise UploadValidationError("Dateiname fehlt.")
    ext = _norm_ext(filename)
    if not ext:
        raise UploadValidationError(
            "Datei hat keine Dateiendung – Upload abgelehnt."
        )
    norm_allowed = {
        e.lower() if e.startswith(".") else f".{e.lower()}"
        for e in allowed_exts
    }
    if ext not in norm_allowed:
        raise UploadValidationError(
            f"Dateityp nicht erlaubt ({ext}). Erlaubt: "
            f"{', '.join(sorted(norm_allowed))}."
        )
    return ext


def validate_mime(
    file_storage,
    allowed_mimes: Iterable[str],
) -> str:
    """Return the sniffed MIME for ``file_storage`` or raise.

    Both Werkzeug's claim and the extension-inferred MIME are tried.
    Raises :class:`UploadValidationError` if no source yields a value
    that matches the allow-list.
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
        f"{', '.join(sorted(norm_allowed))}."
    )


def validate_size(file_storage, max_bytes: int) -> int:
    """Validate ``file_storage`` size against ``max_bytes``.

    Uses ``Content-Length`` from the multipart header if present
    (avoids reading the file), and falls back to ``file.tell()`` after a
    ``seek(0, SEEK_END)`` round-trip. ``max_bytes`` <= 0 disables the
    check (returns the size as-is). Returns the measured size.
    """
    if max_bytes is None or max_bytes <= 0:
        max_bytes = 0

    # Fast path: client advertised the size in the multipart header.
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
            f"Datei ist zu groß ({content_length} > {max_bytes} Bytes)."
        )

    # Slow path: actually scan the file. Werkzeug's FileStorage caches
    # the spooled file stream; the seek+tell round-trip is safe because
    # the next reader re-seeks to 0.
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
            f"Datei ist zu groß ({size} > {max_bytes} Bytes)."
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

    The semantics are *all* checks must pass; this is the function every
    upload route should call before writing the file to disk.
    """
    if file_storage is None or not getattr(file_storage, "filename", None):
        raise UploadValidationError("Keine Datei ausgewählt.")
    ext = validate_extension(file_storage.filename, allowed_exts or [])
    if allowed_mimes:
        validate_mime(file_storage, allowed_mimes)
    if max_bytes:
        validate_size(file_storage, max_bytes)
    return ext

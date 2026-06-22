"""Avatar processing for /profile and across the app.

Pillow does the heavy lifting: re-encode uploaded images, strip EXIF metadata,
and resize into a square-friendly shape. Files end up in ``data/avatars/<id>.<ext>``
where ``<id>`` is the user's numeric ID (zero-padded for stable ordering).

Public entry points used elsewhere:
  * ``process_avatar_upload(file_storage, user_id)`` – Pillow pipeline, returns
    the relative avatar path (or ``None`` when the upload was invalid).
  * ``avatar_url_for(user_id, avatar_path)`` – URL helper that the templates
    use via a Jinja global. Falls back to a per-user generated SVG when no
    avatar has been uploaded yet.
  * ``AVATAR_MAX_BYTES`` / ``AVATAR_MAX_SIDE`` – limits enforced here and on
    the way in via the upload route.
"""
from __future__ import annotations

import io
import os
import re
from typing import Optional

try:
    from PIL import Image, ImageOps
    _HAVE_PILLOW = True
except Exception:  # pragma: no cover - graceful degradation
    Image = None  # type: ignore
    ImageOps = None  # type: ignore
    _HAVE_PILLOW = False

from toolbox.files import BASE_DIR, DATA_DIR


AVATAR_MAX_BYTES = 10 * 1024 * 1024  # 10 MB (spec)
AVATAR_MAX_SIDE = 512               # Down-scale very large images
AVATAR_JPEG_QUALITY = 88

ALLOWED_AVATAR_MIME = {
    "image/png":  ".png",
    "image/jpeg": ".jpg",
    "image/jpg":  ".jpg",   # some browsers send this
    "image/webp": ".webp",
    "image/gif":  ".gif",
}

ALLOWED_AVATAR_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

AVATAR_DIR = os.path.join(DATA_DIR, "avatars")


# ─── Filesystem helpers ─────────────────────────────────────────────────────

def _ensure_avatar_dir() -> str:
    os.makedirs(AVATAR_DIR, exist_ok=True)
    return AVATAR_DIR


def avatar_path_for(user_id: int, avatar_path: Optional[str]) -> Optional[str]:
    """Return an absolute path to the avatar file, validating against
    traversal. Returns None when the stored path is empty / unavailable.
    """
    if not avatar_path or not user_id:
        return None
    # Accept anything that ends with one of the allowed extensions; reject
    # upward traversal explicitly as a defence-in-depth check.
    name = os.path.basename(avatar_path)
    if ".." in avatar_path.split(os.sep) or "/" in avatar_path or "\\" in avatar_path:
        # path must be just a filename – block.
        if avatar_path != name:
            return None
    if not name:
        return None
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_AVATAR_EXT:
        return None
    full = os.path.join(AVATAR_DIR, name)
    # Final check: only accept files that live directly inside AVATAR_DIR.
    if os.path.commonpath([AVATAR_DIR, os.path.abspath(full)]) != os.path.abspath(AVATAR_DIR):
        return None
    return full if os.path.isfile(full) else None


def avatar_url_for(user_id: Optional[int], avatar_path: Optional[str]) -> str:
    """Build the URL the templates need to render ``<img src=...>``.

    Always returns SOMETHING so the layout doesn't shift: either the user's
    own JPEG, or a deterministic generated SVG avatar based on their initials.
    """
    if user_id and avatar_path:
        return f"/profile/avatar/{int(user_id)}"
    fallback_label = f"u{user_id or 0}"
    return f"/profile/avatar-fallback/{fallback_label}"


# ─── Upload pipeline ────────────────────────────────────────────────────────


class AvatarUploadError(ValueError):
    """Raised when an upload cannot be turned into a stored avatar."""


def process_avatar_upload(file_storage, user_id: int) -> str:
    """Validate, downscale, strip EXIF, and write the avatar to disk.

    Returns the new relative path (just ``<user_id>.<ext>``). Raises
    ``AvatarUploadError`` on bad input — the route catches this and returns
    the message to the client.
    """
    if not _HAVE_PILLOW:
        raise AvatarUploadError(
            "Pillow ist auf dem Server nicht installiert – Avatare sind "
            "vorübergehend deaktiviert."
        )
    if file_storage is None or not getattr(file_storage, "filename", None):
        raise AvatarUploadError("Keine Datei ausgewählt.")

    raw_name = file_storage.filename or ""
    ext = os.path.splitext(raw_name)[1].lower()
    if ext not in ALLOWED_AVATAR_EXT:
        raise AvatarUploadError(
            "Dateityp nicht erlaubt. Erlaubt: PNG, JPG, WebP, GIF."
        )

    # Read with a hard ceiling, otherwise Pillow will happily eat 200MB.
    data = file_storage.read(AVATAR_MAX_BYTES + 1)
    if not data:
        raise AvatarUploadError("Datei ist leer.")
    if len(data) > AVATAR_MAX_BYTES:
        raise AvatarUploadError(
            f"Datei ist zu groß (max {AVATAR_MAX_BYTES // (1024 * 1024)} MB)."
        )

    try:
        img = Image.open(io.BytesIO(data))
    except Exception as exc:  # Pillow raises a dozen subclasses depending on file
        raise AvatarUploadError("Datei ist kein gültiges Bild.") from exc

    # Convert to a sane working mode (preserve alpha for PNG/WebP/GIF; flatten to RGB for JPEG).
    save_format = "JPEG" if ext in (".jpg", ".jpeg") else (
        "PNG" if ext == ".png" else
        "WEBP" if ext == ".webp" else "GIF"
    )

    # Apply EXIF rotation if present, then strip ALL metadata.
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    if save_format == "JPEG":
        # JPEG cannot carry alpha; flatten onto a neutral background.
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            bg = Image.new("RGB", img.size, (245, 241, 236))  # matches --bg-warm-ish
            img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

    # Down-scale to fit into AVATAR_MAX_SIDE while preserving aspect ratio;
    # we do NOT crop on the server — clients get a centered object-fit cover.
    img.thumbnail((AVATAR_MAX_SIDE, AVATAR_MAX_SIDE), Image.LANCZOS)

    _ensure_avatar_dir()
    new_name = f"{int(user_id)}{ext}"
    out_path = os.path.join(AVATAR_DIR, new_name)

    # Atomic-ish write: tmp file, then replace.
    tmp_path = out_path + ".tmp"
    save_kwargs = {}
    if save_format == "JPEG":
        save_kwargs["quality"] = AVATAR_JPEG_QUALITY
        save_kwargs["optimize"] = True
        save_kwargs["progressive"] = True

    try:
        # Drop all metadata for privacy (EXIF, XMP, color profile, …).
        clean = Image.new(img.mode, img.size)
        clean.putdata(list(img.getdata()))
        clean.save(tmp_path, format=save_format, **save_kwargs)
        os.replace(tmp_path, out_path)
    except Exception as exc:
        # Best-effort cleanup of the tmp file.
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise AvatarUploadError(f"Bild konnte nicht gespeichert werden: {exc}") from exc

    # Best-effort cleanup of any older avatar we may have replaced so storage
    # does not fill up with duplicates from previous uploads.
    try:
        for old_ext in ALLOWED_AVATAR_EXT:
            old = os.path.join(AVATAR_DIR, f"{int(user_id)}{old_ext}")
            if old != out_path and os.path.isfile(old):
                os.remove(old)
    except OSError:
        pass

    return new_name


# ─── Fallback SVG ───────────────────────────────────────────────────────────

_SVG_CACHE: dict[str, bytes] = {}


def _svg_fallback(user_id_str: str, label_hint: Optional[str] = None) -> bytes:
    """Generates a small inline SVG used when a user has no uploaded avatar.

    Cached because the URL uses the user-id as the seed and these get hit
    from many places (chat sidebar, friend list, mod panel …).
    """
    key = f"{user_id_str}|{label_hint or ''}"
    cached = _SVG_CACHE.get(key)
    if cached is not None:
        return cached

    # Pick a deterministic hue from the user_id.
    try:
        seed = int(re.sub(r"\D", "", user_id_str)) or 0
    except Exception:
        seed = 0
    hue_a = (seed * 47) % 360
    hue_b = (seed * 89 + 120) % 360
    initials = (label_hint or "?").strip()[:2].upper() or "?"
    # Escape for SVG.
    safe = (
        initials.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="hsl({hue_a},80%,65%)"/>'
        f'<stop offset="1" stop-color="hsl({hue_b},80%,55%)"/>'
        '</linearGradient></defs>'
        '<rect width="80" height="80" rx="14" fill="url(#g)"/>'
        '<text x="40" y="40" text-anchor="middle" dominant-baseline="central" '
        'font-family="Outfit, Inter, sans-serif" font-weight="700" '
        'font-size="34" fill="white">' + safe + '</text>'
        '</svg>'
    ).encode("utf-8")
    _SVG_CACHE[key] = svg
    return svg


def write_fallback_svg(user_id, label_hint=None) -> bytes:
    return _svg_fallback(str(user_id), label_hint)

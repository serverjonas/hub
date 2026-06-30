"""Cryptographically random storage filenames + path-traversal defence.

Why this exists
---------------

User-supplied filenames are attacker-controlled content. Storing
uploads as ``file.save(user_filename)`` puts the upload at the mercy
of:

* path traversal (``../../etc/passwd``)
* Unicode confusables / NUL bytes / control chars
* length-extension shells (``a`` * 1024 * 1024)

Even with ``werkzeug.utils.secure_filename``, downstream code that does
its own ``os.path.join(storedir, filename)`` can still vuln itself
later. This module supplies:

* :func:`random_storage_filename` — caller discards the user-supplied
  name in favour of an unguessable token, with a stable extension so
  mime-sniffing stays sane.
* :func:`safe_join_under`         — defence-in-depth resolution check
  that rejects any path whose ``os.path.abspath`` result escapes the
  intended base directory.
* :func:`normalize_storage_filename` — werkzeug-style sanitization
  for callers that *do* want to keep meaning from the user name (e.g.
  profile picture "alice.png" → "1-alice.png" rather than a pure token).
"""

from __future__ import annotations

import os
import secrets

from werkzeug.utils import secure_filename


_DEFAULT_TOKEN_BYTES = 24           # 32 chars base64-ish; ~190 bits entropy
_MAX_FILENAME_LEN = 255              # most filesystems' cap
_MIN_TOKEN_BYTES = 8
_RESERVED_FOR_TOKEN = 1              # at least one token byte after prefix/ext


def random_storage_filename(
    *,
    prefix: str = "",
    ext: str = "",
    token_bytes: int = _DEFAULT_TOKEN_BYTES,
) -> str:
    """Return a cryptographically random storage filename.

    Format: ``<prefix><urlsafe-token><ext>`` (omits blank fields).

    The token comes from :func:`secrets.token_urlsafe`, which is
    backed by ``os.urandom`` on supported platforms.

    Constraints
    -----------
    * ``token_bytes`` must be >= ``_MIN_TOKEN_BYTES`` (8). Smaller is
      rejected with :class:`ValueError`.
    * ``len(prefix) + len(ext) + _RESERVED_FOR_TOKEN`` must fit
      inside :data:`_MAX_FILENAME_LEN`. Combined-component caps
      larger than that are also rejected with :class:`ValueError`,
      because there is no way to fit even a 1-byte random token
      without violating the filesystem-name cap.

    Truncation policy
    -----------------
    When the *total* ``prefix + token + ext`` exceeds
    ``_MAX_FILENAME_LEN``, the token (the only component a caller
    can safely shorten without breaking a downstream lookup) is
    truncated to whatever fits. The prefix and extension are
    caller-controlled and are taken verbatim.
    """
    if token_bytes < _MIN_TOKEN_BYTES:
        raise ValueError(
            f"token_bytes too small ({token_bytes}); "
            f"minimum is {_MIN_TOKEN_BYTES}."
        )

    if len(prefix) + len(ext) + _RESERVED_FOR_TOKEN > _MAX_FILENAME_LEN:
        raise ValueError(
            f"prefix+ext ({len(prefix)}+{len(ext)}) is too long: "
            f"there is no room for even a 1-byte token within the "
            f"{_MAX_FILENAME_LEN}-char filesystem cap. Shorten the "
            f"prefix or extension."
        )

    token = secrets.token_urlsafe(token_bytes)
    parts = [p for p in (prefix, token, ext) if p]
    name = "".join(parts)

    if len(name) > _MAX_FILENAME_LEN:
        # Token is the only component we can safely shorten.
        keep = _MAX_FILENAME_LEN - len(prefix) - len(ext)
        token = token[:max(0, keep)]
        parts = [p for p in (prefix, token, ext) if p]
        name = "".join(parts)
    return name


def safe_join_under(base: str, *parts: str) -> str:
    """Join paths under ``base`` and reject any traversal attempt.

    Defence-in-depth check that catches:

    * absolute paths passed as ``parts`` (e.g. ``/etc/passwd``)
    * ``..`` components in any segment
    * nested-junction-style escapes (Windows-only; harmless on POSIX)

    Raises :class:`ValueError` with a clear message if the resolved
    path is not ``base`` or a descendant of it.

    Note on symlinks
    -----------------

    This function resolves via ``os.path.abspath`` which does NOT
    follow symlinks. If the storage dir contains symlinks that escape
    ``base``, this check is insufficient — the caller should also run
    ``os.path.realpath`` and re-check, or refuse symlinks outright.
    """
    if not base:
        raise ValueError("base must be non-empty.")
    abs_base = os.path.abspath(base)
    full = os.path.abspath(os.path.join(abs_base, *parts))
    sep = os.sep
    if full != abs_base and not full.startswith(abs_base + sep):
        raise ValueError(
            f"path traversal blocked: {parts!r} resolved to {full!r}, "
            f"outside {abs_base!r}."
        )
    return full


def normalize_storage_filename(filename: str) -> str:
    """Sanitize a user-supplied filename for safe filesystem use.

    Wraps ``werkzeug.utils.secure_filename`` (which already strips
    path separators, control chars, and leading dots), then hard-
    caps length to :data:`_MAX_FILENAME_LEN` so callers can
    ``os.path.join`` the result without busting ext4/NTFS limits.

    Returns an empty string if nothing usable remains -- callers
    should fall back to :func:`random_storage_filename` in that
    case (don't store empty-named files).
    """
    if not filename:
        return ""
    safe = secure_filename(filename)
    if not safe:
        return ""
    # Avoid empty-extension vs pure-dot ambiguity.
    if safe in {".", ".."}:
        return ""
    if len(safe) > _MAX_FILENAME_LEN:
        stem, _, ext = safe.rpartition(".")
        if ext and len(ext) < _MAX_FILENAME_LEN:
            stem = stem[: _MAX_FILENAME_LEN - len(ext) - 1]
            safe = f"{stem}.{ext}"
        else:
            safe = safe[:_MAX_FILENAME_LEN]
    return safe

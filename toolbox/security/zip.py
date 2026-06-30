"""ZIP Slip-safe extraction with per-member validation.

Why this exists
---------------

Two classic ZIP attacks:

* **ZIP Slip** — a malicious member declares its name as
  ``../../etc/cron.d/evil``. A naive ``z.extractall(target)`` writes
  outside ``target``. CVE-2018-1002200 / 1002201 et al.
* **ZIP Bomb** — a tiny archive explodes to gigabytes on extract.
  42.zip is the canonical example.

This module extracts with:

* per-member path-traversal rejection (absolute, ``..``, escape)
* optional per-member extension whitelist
* member-count cap
* aggregate uncompressed-size cap

It does NOT attempt to recurse into nested zips; that's a caller
decision (typically refuse to recurse).
"""

from __future__ import annotations

import os
import zipfile
from typing import Iterable, Optional, Set


class ZipSecurityError(ValueError):
    """Raised when a ZIP archive violates safety invariants.

    Inherits from ``ValueError`` so legacy ``except ValueError``
    blocks catch it, but routes handling uploads should branch on
    this error explicitly and surface a localized flash via
    ``str(e)`` (i18n follows in P3.x).
    """


# Reasonable defaults; per-route callers (cloud/music) tighten
# via the kwargs of :func:`safe_extract_zip`.
_DEFAULT_MAX_MEMBERS = 1000
_DEFAULT_MAX_UNCOMPRESSED = 500 * 1024 * 1024 * 1024  # 500 GB


def _norm_ext_set(exts: Optional[Iterable[str]]) -> Optional[Set[str]]:
    if exts is None:
        return None
    return {
        e.lower() if e.startswith(".") else f".{e.lower()}"
        for e in exts
    }


def _safe_member_path(abs_target: str, member_name: str) -> str:
    """Resolve ``member_name`` under ``abs_target`` and reject traversal.

    Returns the resolved absolute path on success. Raises
    :class:`ZipSecurityError` if the resolved path is not ``abs_target``
    or a strict descendant of it.
    """
    normalized = member_name.replace("\\", "/")
    full = os.path.abspath(os.path.join(abs_target, normalized))
    sep = os.sep
    if full != abs_target and not full.startswith(abs_target + sep):
        raise ZipSecurityError(
            f"ZIP Slip blockiert: Eintrag {member_name!r} würde "
            f"außerhalb des Zielordners landen ({full!r})."
        )
    return full


def _is_encrypted(info) -> bool:
    """True iff ``info.flag_bits`` has the encryption bit set (bit 0,
    per APPNOTE.TXT general purpose bit flags).

    Module-private helper -- importable from tests, not part of the
    public API. The check itself is the canonical APPNOTE encoding:
    bit 0 of the local file header's general-purpose bit flag field
    signals that the entry's body is encrypted.

    Note: building a real encrypted ZIP in tests requires a crypto
    backend. Python's stdlib ``zipfile.writestr`` strips the bit
    on its own, so integration tests that produce an encrypted
    archive must come from a real fixture (e.g. an external
    ``clamscan`` test corpus). For unit testing the LOGIC of this
    helper, a stand-in ``info``-shaped object with a ``flag_bits``
    attribute suffices.
    """
    return bool(info.flag_bits & 0x1)


def safe_extract_zip(
    zip_path: str,
    target: str,
    *,
    allowed_exts: Optional[Iterable[str]] = None,
    max_members: int = _DEFAULT_MAX_MEMBERS,
    max_total_uncompressed: int = _DEFAULT_MAX_UNCOMPRESSED,
) -> int:
    """Safely extract a ZIP archive.

    Parameters
    ----------
    zip_path:
        Path to the ZIP file. Caller is responsible for having saved
        this from a validated :class:`werkzeug.FileStorage`.
    target:
        Directory to extract into. Created if missing.
    allowed_exts:
        Optional iterable of file extensions (with or without leading
        ``.``) -- if provided, every member must match. Useful for
        ``{".png", ".jpg"}`` -- i.e. the cloud route accepts image
        types only.
    max_members:
        Hard cap on the number of ZIP entries. Defaults to a few
        thousand so that a malicious archive can't make us scan
        millions of entries.
    max_total_uncompressed:
        Hard cap on the *aggregate* uncompressed byte-size. Defaults
        to 500 GB so that a 42-zip-style bomb cannot exhaust disk.

    Returns
    -------
    int
        Sum of the per-member uncompressed sizes actually written.
        Callers use this for quota bookkeeping.

    Raises
    ------
    ZipSecurityError
        On:
        * archive cannot be opened
        * any member fails the encryption check
        * any member fails the path-traversal check (ZIP Slip)
        * any member fails the extension whitelist
        * member-count cap exceeded
        * aggregate-uncompressed cap exceeded
    FileNotFoundError
        If ``zip_path`` doesn't exist.
    """
    abs_target = os.path.abspath(target)
    os.makedirs(abs_target, exist_ok=True)

    norm_exts = _norm_ext_set(allowed_exts)

    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            # Reject encrypted archives outright: we can't inspect
            # them honestly without paying the decrypt cost on every
            # member, so refuse and let the caller surface a clear
            # "encrypted archives are not supported" message.
            for info in z.infolist():
                if _is_encrypted(info):
                    raise ZipSecurityError(
                        f"Archiv enthält verschlüsselte Einträge "
                        f"({info.filename!r}); nicht unterstützt."
                    )

            infos = z.infolist()
            if len(infos) > max_members:
                raise ZipSecurityError(
                    f"Archiv enthält {len(infos)} Einträge "
                    f"(max {max_members})."
                )
            total_uncompressed = sum(i.file_size for i in infos)
            if total_uncompressed > max_total_uncompressed:
                raise ZipSecurityError(
                    f"Archiv entpackt {total_uncompressed} Bytes "
                    f"(max {max_total_uncompressed})."
                )

            written = 0
            for info in infos:
                # validate path-traversal *before* extracting
                _safe_member_path(abs_target, info.filename)

                if norm_exts is not None:
                    ext = os.path.splitext(info.filename)[1].lower()
                    if ext not in norm_exts:
                        raise ZipSecurityError(
                            f"Archiv enthält unerlaubten Eintrag: "
                            f"{info.filename!r} ({ext}). Erlaubt: "
                            f"{', '.join(sorted(norm_exts))}."
                        )

                # Honour the directory flag by creating folders.
                if info.is_dir():
                    dir_path = os.path.join(abs_target, info.filename)
                    # Even directories must pass the traversal check;
                    # _safe_member_path above already validated them.
                    os.makedirs(dir_path, exist_ok=True)
                    continue

                # Extract the file. zipfile.extract is reasonably
                # safe on Python 3.6+ when given an absolute target,
                # but we still want the validation above to win so we
                # audit the path BEFORE handing off.
                z.extract(info, abs_target)
                written += info.file_size
    except zipfile.BadZipFile as exc:
        raise ZipSecurityError(f"Archiv ist kein gültiges ZIP: {exc}") from exc

    return written

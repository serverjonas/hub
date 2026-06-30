"""toolbox.security — security primitives for the serverjonas hub.

This package is purely additive: each submodule lands incrementally in a
separate commit so existing code paths keep working until callers opt in.

Submodules (added in this order):

* ``uploads``   – MIME / extension / size validation for ``FileStorage``
* ``files``     – cryptographically random filenames + path-traversal
                  defence for storage paths
* ``zip``       – ZIP Slip-safe extraction with per-member whitelist
* ``limits``    – per-route upload size limits
                  (``config/upload_limits.toml``)
* ``antivirus`` – pluggable AV scan hook (no-op by default)

This commit ships **only** :mod:`uploads`. The remaining helpers will
re-export themselves from this ``__init__`` as each submodule lands.
"""

from .uploads import (
    UploadValidationError,
    validate_extension,
    validate_mime,
    validate_size,
    validate_upload,
    sniff_mime,
    ALLOWED_IMAGE_EXTS,
    ALLOWED_IMAGE_MIMES,
    ALLOWED_VIDEO_EXTS,
    ALLOWED_VIDEO_MIMES,
    ALLOWED_AUDIO_EXTS,
    ALLOWED_AUDIO_MIMES,
    ALLOWED_DOC_EXTS,
    ALLOWED_DOC_MIMES,
)

__all__ = [
    "UploadValidationError",
    "validate_extension",
    "validate_mime",
    "validate_size",
    "validate_upload",
    "sniff_mime",
    "ALLOWED_IMAGE_EXTS",
    "ALLOWED_IMAGE_MIMES",
    "ALLOWED_VIDEO_EXTS",
    "ALLOWED_VIDEO_MIMES",
    "ALLOWED_AUDIO_EXTS",
    "ALLOWED_AUDIO_MIMES",
    "ALLOWED_DOC_EXTS",
    "ALLOWED_DOC_MIMES",
]

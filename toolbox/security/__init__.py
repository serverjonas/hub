"""toolbox.security — security primitives for the serverjonas hub.

This package is purely additive: each submodule lands incrementally in a
separate commit so existing code paths keep working until callers opt in.

Current submodules (P1.1 .. P1.5):

* ``uploads``   – MIME / extension / size validation for ``FileStorage``
* ``files``     – cryptographically random filenames + safe path-traversal
* ``zip``       – ZIP Slip-safe extraction with per-member whitelist
* ``limits``    – per-route upload size limits
* ``antivirus`` – pluggable AV scan hook (no-op by default)

Public re-exports below use stable names so callers can write
``from toolbox.security import validate_upload`` without knowing the
submodule layout.
"""

from .antivirus import (
    AntivirusUnavailableError,
    register_scanner,
    scan_for_malware,
    unregister_scanner,
)
from .files import (
    normalize_storage_filename,
    random_storage_filename,
    safe_join_under,
)
from .limits import (
    get_all_limits,
    get_route_limit,
)
from .uploads import (
    ALLOWED_AUDIO_EXTS,
    ALLOWED_AUDIO_MIMES,
    ALLOWED_DOC_EXTS,
    ALLOWED_DOC_MIMES,
    ALLOWED_IMAGE_EXTS,
    ALLOWED_IMAGE_MIMES,
    ALLOWED_VIDEO_EXTS,
    ALLOWED_VIDEO_MIMES,
    ErrorCode,
    UploadValidationError,
    sniff_mime,
    validate_extension,
    validate_mime,
    validate_size,
    validate_upload,
)
from .zip import (
    ZipSecurityError,
    safe_extract_zip,
)

__all__ = [
    # uploads (P1.1)
    "ErrorCode",
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
    # files (P1.2)
    "random_storage_filename",
    "safe_join_under",
    "normalize_storage_filename",
    # zip (P1.3)
    "safe_extract_zip",
    "ZipSecurityError",
    # limits (P1.4)
    "get_route_limit",
    "get_all_limits",
    # antivirus (P1.5)
    "scan_for_malware",
    "AntivirusUnavailableError",
    "register_scanner",
    "unregister_scanner",
]

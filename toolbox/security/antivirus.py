"""Pluggable antivirus scan hook.

Why this exists
---------------

A real AV integration needs a daemon (ClamAV ``clamd`` via
``clamd``/``pyclamd``, or ``clamscan`` shelling out), and the host
admin policy on whether to AV-scan at all is project-specific. This
module provides:

* the call-shape :func:`scan_for_malware` -- returns ``(clean, threat)``
* a registry :func:`register_scanner` for plugging in a real scanner
* a no-op default so the call site can be wired in BEFORE the AV
  daemon is set up on the box

Plug-in pattern
---------------

::

    from toolbox.security import register_scanner, scan_for_malware

    def my_clamav_scanner(path):
        import pyclamd
        c = pyclamd.ClamdNetworkSocket()
        res = c.scan_file(path)
        if res is None:
            return True, None
        # res[path] -> stream: ('FOUND', 'Win.Trojan.Generic')
        _, payload = next(iter(res.values()))
        status, threat = payload
        return status == "OK", threat

    register_scanner(my_clamav_scanner)

Or in ``app.py`` startup::

    from toolbox.security import register_scanner
    try:
        import pyclamd
        def scanner(path):
            r = pyclamd.ClamdUnixSocket().scan_file(path)
            return (r is None) or all(v[1][0] == "OK" for v in r.values()), None
        register_scanner(scanner)
    except Exception:
        pass  # AV hook stays as no-op

Caveats
-------

* Scan happens AFTER the file is written to its final destination.
  Callers must therefore be prepared to delete the file on a
  ``clean=False`` result.
* Streaming uploads (e.g. chunked films) cannot be fully AV-scanned
  per-chunk; chain the scan on the assembled file at the
  ``/upload/finish`` step instead.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional, Tuple

log = logging.getLogger("security.antivirus")


# Returns (clean, threat). clean=True means "no threat found". When
# clean=False, threat should be a short identifier suitable for logging /
# UI (e.g. "Win.Trojan.Generic").
ScannerFn = Callable[[str], Tuple[bool, Optional[str]]]

_scanner: Optional[ScannerFn] = None


class AntivirusUnavailableError(RuntimeError):
    """Raised when a registered scanner cannot itself operate.

    Distinct from a "clean=False" return; this one signals "we cannot
    tell because the scanner itself failed". Callers typically
    surface this as a 503 / "try again later".
    """


def register_scanner(scanner: ScannerFn) -> None:
    """Register a scanner. Replaces any previous registration."""
    global _scanner
    _scanner = scanner
    log.info("antivirus scanner registered: %s", _safe_name(scanner))


def unregister_scanner() -> None:
    """Remove the registered scanner; scan_for_malware becomes a no-op
    again. Primarily useful for tests."""
    global _scanner
    _scanner = None


def _safe_name(fn: Optional[Callable[..., object]]) -> str:
    if fn is None:
        return "<none>"
    return getattr(fn, "__name__", repr(fn))


def scan_for_malware(path: str) -> Tuple[bool, Optional[str]]:
    """Run the configured scanner on ``path``.

    Returns ``(clean, threat)`` where ``threat`` is ``None`` on clean
    and a short identifier like ``Win.Trojan.Generic`` otherwise.

    If no scanner is registered, this is a no-op returning
    ``(True, None)`` -- callers should NOT assume that translates
    to "file is provably clean", only "no scanner chose to flag it".
    """
    if _scanner is None:
        log.debug(
            "antivirus: no scanner registered; passing %s", path,
        )
        return True, None

    try:
        clean, threat = _scanner(path)
    except AntivirusUnavailableError:
        # Scanner said "I cannot scan". Bubble up -- caller decides
        # between refusing the upload or accepting with a logged
        # warning.
        raise
    except Exception as exc:
        log.warning("antivirus scanner raised on %s: %s", path, exc)
        # Treat as a "couldn't scan" -- flag conservatively.
        return False, f"scanner_error: {type(exc).__name__}"

    return bool(clean), threat


def _noop_scanner(path: str) -> Tuple[bool, Optional[str]]:
    """Reference no-op scanner; used as a registry default in tests."""
    return True, None

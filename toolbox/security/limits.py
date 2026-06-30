"""Per-route upload size limits, loaded from ``config/security_limits.toml``.

Why this exists
---------------

Hard-coding ``max_bytes=200*1024*1024`` in every upload route is the
default anti-pattern. Centralising limits in one TOML file gives:

* ops a single knob to raise/lower caps without touching code;
* per-route overrides without code branches;
* a single spot where the limits can be documented.

Loading policy
--------------

* Config is loaded once at import time from
  ``config/security_limits.toml`` (relative to project root).
* Falls back to a defaults table on: missing file, malformed TOML,
  unknown sections. Misconfig MUST NOT crash the app — but a startup
  log warning SHOULD be emitted so it's visible in ops dashboards.
"""

from __future__ import annotations

import logging
import os
import tomllib
from typing import Any

log = logging.getLogger("security.limits")


_BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_LIMITS_PATH = os.path.join(_BASE_DIR, "config", "security_limits.toml")


# In-code defaults mirror config/security_limits.toml. Used as a
# fallback when the TOML is missing / unreadable / malformed.
_DEFAULTS: dict[str, dict[str, int]] = {
    "cloud": {
        "max_file":       200 * 1024 * 1024,
        "max_zip_total":  1024 * 1024 * 1024,
        "max_members":    1000,
    },
    "memes": {
        "max_file":        25 * 1024 * 1024,
        "max_zip_total":   0,
        "max_members":     0,
    },
    "films": {
        "max_chunk":        8 * 1024 * 1024,
        "max_file":        50 * 1024 * 1024 * 1024,
        "max_zip_total":   0,
        "max_members":     0,
    },
    "music": {
        "max_file":        200 * 1024 * 1024,
        "max_zip_total":  1024 * 1024 * 1024,
        "max_members":    1000,
    },
    "avatar": {
        "max_file":       10 * 1024 * 1024,
        "max_zip_total":  0,
        "max_members":    0,
    },
}


def _load_config() -> dict[str, dict[str, int]]:
    if not os.path.isfile(_LIMITS_PATH):
        log.info(
            "security_limits.toml not found at %s; using in-code defaults.",
            _LIMITS_PATH,
        )
        return {k: dict(v) for k, v in _DEFAULTS.items()}

    try:
        with open(_LIMITS_PATH, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        log.warning(
            "security_limits.toml is malformed (%s); using in-code "
            "defaults so the app stays up.", exc,
        )
        return {k: dict(v) for k, v in _DEFAULTS.items()}

    merged: dict[str, dict[str, int]] = {}
    for section, defaults in _DEFAULTS.items():
        merged[section] = dict(defaults)
        if section in data:
            for key, value in data[section].items():
                if isinstance(value, bool):
                    # bool is subclass of int -- guard against
                    # accidental "true" being treated as 1 byte.
                    log.warning(
                        "security_limits.toml: %s.%s is a bool; "
                        "ignoring.", section, key,
                    )
                    continue
                if isinstance(value, (int, float)) and value >= 0:
                    merged[section][key] = int(value)
                else:
                    log.warning(
                        "security_limits.toml: %s.%s = %r is not a "
                        "non-negative integer; keeping default.",
                        section, key, value,
                    )
    return merged


_LIMITS: dict[str, dict[str, int]] = _load_config()


def get_route_limit(route: str, key: str, default: int = 0) -> int:
    """Look up a per-route limit. Returns ``default`` if unset.

    Routes map to sections in ``config/security_limits.toml``:
    ``cloud``, ``memes``, ``films``, ``music``, ``avatar``.
    """
    section = _LIMITS.get(route, {})
    return int(section.get(key, default))


def get_all_limits() -> dict[str, dict[str, int]]:
    """Return the full resolved config (defensive copy)."""
    return {k: dict(v) for k, v in _LIMITS.items()}


def _reload_for_tests() -> None:
    """Re-read the config from disk. Tests use this; production code
    should not."""
    global _LIMITS
    _LIMITS = _load_config()

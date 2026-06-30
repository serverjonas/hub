"""Translation version manifest.

Scans the ``static/`` directory at server startup, computes a stable
content-hash for every translation JSON file (``*_<lang>.json``) and
exposes a ``build_translation_manifest`` helper that returns the mapping
``{filename: short_hash}``.

This is what makes the persistent i18n cache on the client possible: the
browser compares the hash returned in ``/i18n/manifest.json`` against the
one it stored in ``localStorage`` for that filename and only refetches
when they differ.
"""

import hashlib
import os

from .files import BASE_DIR

# Languages whose ``<page>_<lang>.json`` files we recognise.
_KNOWN_LANGS = frozenset(
    {"deu", "eng", "spa", "fra", "ita", "nld", "por", "pol", "rus"}
)

# Where translation files live (mirrors ``app.py``'s ``static_folder``).
_TRANSLATIONS_SUBDIR = "static"

# Length of the hash we surface in the manifest. 12 hex chars = 48 bits
# so the birthday-bound collision risk stays negligible even as the static/
# translation set grows beyond a few hundred files.
_HASH_LEN = 12


def is_translation_filename(name: str) -> bool:
    """True if ``name`` looks like a ``<page>_<lang>.json`` file.

    The pattern is intentionally strict — only files that follow the
    ``<page>_<lang>.json`` shape with a known language suffix are
    included, so we never accidentally publish a version for an
    unrelated JSON asset.
    """
    if not name or not name.endswith(".json"):
        return False
    stem = name[:-5]  # strip ".json"
    if "_" not in stem:
        return False
    lang = stem.rsplit("_", 1)[-1]
    return lang in _KNOWN_LANGS


def compute_file_version(file_path: str) -> str:
    """Return a short (8-char) SHA-256 hex digest of the file contents.

    An empty string is returned if the file cannot be read — callers
    should treat a missing/empty version as "do not try to cache this".
    """
    try:
        with open(file_path, "rb") as f:
            data = f.read()
    except OSError:
        return ""
    if not data:
        return ""
    return hashlib.sha256(data).hexdigest()[:_HASH_LEN]


def build_translation_manifest(translations_dir: str | None = None) -> dict:
    """Build ``{filename: short_hash}`` for every translation file.

    Pass ``translations_dir`` to override the default ``static/`` lookup
    (mainly useful for tests). The result is sorted by filename so the
    manifest is byte-stable across runs and easy to diff.
    """
    folder = translations_dir or os.path.join(BASE_DIR, _TRANSLATIONS_SUBDIR)
    manifest: dict = {}
    if not os.path.isdir(folder):
        return manifest
    for name in sorted(os.listdir(folder)):
        if not is_translation_filename(name):
            continue
        full = os.path.join(folder, name)
        if not os.path.isfile(full):
            continue
        version = compute_file_version(full)
        if version:
            manifest[name] = version
    return manifest

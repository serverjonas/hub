#!/usr/bin/env python3
"""Apply languages — validate per-page language JSONs.

For every page that has ``<page>_<lang>.json`` files in ``static/`` this
script:

* builds the union of translatable keys across every language,
* flags every key whose presence set is not the full list of supported
  languages,
* prompts the user — per inconsistency — whether to delete it from
  every JSON of that page **or** translate the existing value (Google
  Translate) into the languages currently missing it.

The script defaults to a dry-run ``--check`` mode; pass ``--apply`` to
make changes, and ``--yes`` to accept the recommended translation
without interactive prompts.

Translation round-trips are made safe for ``{var}`` placeholders by
masking them with neutral tokens that Google Translate won't touch and
restoring the originals afterwards. Successful translations are verified
so that a reply that broke interpolation falls back to a safe value
instead of silently corrupting the file.

Examples
--------
    # report only
    python apply_languages.py

    # walk through inconsistencies interactively
    python apply_languages.py --apply

    # auto-translate from English (skip prompts)
    python apply_languages.py --apply --yes

    # only the "base" page
    python apply_languages.py --apply --page base
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "apply_languages.py needs the 'requests' package (it's already in requirements.txt).\n"
    )
    raise


# ─── Project layout & language tables ────────────────────────────────────
HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"

# Mirrors i18n.js — order is preserved when reporting diffs.
LANGS: list[str] = ["deu", "eng", "spa", "fra", "ita", "nld", "por", "pol", "rus"]
# 3-letter code → ISO 639-1 code (Google Translate expects the short form).
LANG_HTML: dict[str, str] = {
    "eng": "en", "deu": "de", "spa": "es", "fra": "fr", "ita": "it",
    "nld": "nl", "por": "pt", "pol": "pl", "rus": "ru",
}
LANG_LABEL: dict[str, str] = {
    "eng": "English",  "deu": "German",   "spa": "Spanish",  "fra": "French",
    "ita": "Italian",  "nld": "Dutch",    "por": "Portuguese",
    "pol": "Polish",   "rus": "Russian",
}

GOOGLE_URL = "https://translate.googleapis.com/translate_a/single"

# `_meta` is page metadata, not a translation — its shape legitimately
# differs between pages (some only have `page`, others only `html_lang`)
# and `html_lang` is always language-specific, so we skip it.
SKIP_TOP_LEVEL_KEYS: frozenset = frozenset({"_meta"})


# ─── {var}-style placeholder masking ─────────────────────────────────────
_VAR_RE = re.compile(r"\{(\w+)\}")
# Non-breaking-space + degree-symbol pairs form a token that Google
# Translate reliably leaves alone, so ``{count}`` survives the round-trip.
_PLACEHOLDER_OPEN = "\u00A0\u00B0VAR_"
_PLACEHOLDER_CLOSE = "\u00B0\u00A0"


def mask_vars(text: str) -> Tuple[str, dict[str, str]]:
    """Replace ``{var}`` placeholders with neutral tokens."""
    mapping: dict[str, str] = {}

    def _sub(m: "re.Match[str]") -> str:
        token = f"{_PLACEHOLDER_OPEN}{len(mapping)}{_PLACEHOLDER_CLOSE}"
        mapping[token] = "{" + m.group(1) + "}"
        return token

    return _VAR_RE.sub(_sub, text), mapping


def unmask_vars(text: str, mapping: dict[str, str]) -> str:
    out = text
    for token, original in mapping.items():
        out = out.replace(token, original)
    return out


# ─── File discovery ───────────────────────────────────────────────────────
def _filename_to_page_lang(name: str) -> Optional[Tuple[str, str]]:
    """Return `(page, lang)` for ``<page>_<lang>.json`` else None."""
    if not name.endswith(".json"):
        return None
    stem = name[:-5]
    if "_" not in stem:
        return None
    lang = stem.rsplit("_", 1)[-1]
    if lang not in LANGS:
        return None
    page = stem[: -len(lang) - 1]
    if not page:
        return None
    return page, lang


def load_all(static_dir: Path) -> dict[str, dict[str, dict]]:
    """Read every ``page_<lang>.json``; return ``{page: {lang: data}}``."""
    out: dict[str, dict[str, dict]] = {}
    for path in sorted(static_dir.iterdir()):
        if not path.is_file():
            continue
        parsed = _filename_to_page_lang(path.name)
        if not parsed:
            continue
        page, lang = parsed
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"  ! JSON parse error in {path.name}: {e}\n")
            continue
        out.setdefault(page, {})[lang] = data
    return out


# ─── Consistency checking ────────────────────────────────────────────────
def _translation_keys(data: dict) -> set[str]:
    """Top-level translatable keys for one language of a page."""
    return {k for k in data.keys() if k not in SKIP_TOP_LEVEL_KEYS}


def compute_diffs(by_page: dict[str, dict[str, dict]]) -> list[dict]:
    """For each page, list keys whose presence set is not the full page-language set.

    Missing language *files* for a page are reported separately via
    ``compute_missing_files`` — they're a structural issue, not a
    translation issue, and conflating the two would silently lie about
    how many files a fix actually changed.
    """
    diffs: list[dict] = []
    for page in sorted(by_page.keys()):
        langs_present = by_page[page]
        if not langs_present:
            continue
        all_keys: set[str] = set()
        for data in langs_present.values():
            all_keys.update(_translation_keys(data))
        # Compare only across the languages that actually have a JSON file
        # for this page — languages whose file doesn't exist are handled
        # by `compute_missing_files` instead, so the diff report matches
        # what `_persist_page` will actually write.
        page_langs = list(langs_present.keys())
        for key in sorted(all_keys):
            present = [lang for lang in page_langs if key in langs_present[lang]]
            missing = [lang for lang in page_langs if key not in langs_present[lang]]
            if missing:
                diffs.append({
                    "page": page,
                    "key": key,
                    "present": present,
                    "missing": missing,
                    "values": {lang: langs_present[lang][key] for lang in present},
                })
    return diffs


def compute_missing_files(by_page: dict[str, dict[str, dict]]) -> dict[str, list[str]]:
    """For each page, list languages that have no JSON file at all."""
    out: dict[str, list[str]] = {}
    for page, langs in by_page.items():
        absent = [lang for lang in LANGS if lang not in langs]
        if absent:
            out[page] = absent
    return out


# ─── Google Translate ─────────────────────────────────────────────────────
def translate(text: str, source: str, target: str, retries: int = 3) -> Optional[str]:
    """Translate ``text`` from ``source`` to ``target``; return None on failure."""
    if not text.strip():
        return text
    if source == target:
        return text
    sl = LANG_HTML.get(source)
    tl = LANG_HTML.get(target)
    if not sl or not tl:
        sys.stderr.write(f"  ! unknown language code: {source}→{target}\n")
        return None

    masked, mapping = mask_vars(text)
    params = {"client": "gtx", "sl": sl, "tl": tl, "dt": "t", "q": masked}
    last_err: Optional[str] = None
    for attempt in range(retries):
        try:
            r = requests.get(GOOGLE_URL, params=params, timeout=10)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                time.sleep(0.5 * (2 ** attempt))
                continue
            payload = r.json()
            if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
                last_err = "unexpected payload shape"
                continue
            chunks = payload[0]
            translated = "".join(c[0] for c in chunks if isinstance(c, list) and c)
            translated = unmask_vars(translated, mapping)
            # Sanity check: every masked token must still be present, otherwise
            # the reply got mangled — fall back to a safe value so we never
            # silently drop a placeholder.
            if any(token not in translated for token in mapping):
                sys.stderr.write(
                    f"  ! placeholder lost during {source}→{target}; keeping source value\n"
                )
                return None
            return translated
        except (requests.RequestException, ValueError) as e:
            last_err = str(e)
            time.sleep(0.5 * (2 ** attempt))
    sys.stderr.write(f"  ! translate failed ({source}→{target}): {last_err}\n")
    return None


# ─── JSON writing (preserve order, stable indent) ────────────────────────
def _detect_indent(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "    "
    m = re.search(r"\n(\s+)\"", text)
    return m.group(1) if m else "    "


def _move_meta_first(obj: dict) -> dict:
    """Keep ``_meta`` at the top so it stays findable in the file."""
    if "_meta" not in obj:
        return obj
    meta = obj.pop("_meta")
    new: dict = {"_meta": meta}
    new.update(obj)
    return new


def save_page(path: Path, data: dict, indent: str) -> None:
    data = _move_meta_first(data)
    text = json.dumps(data, ensure_ascii=False, indent=indent)
    path.write_text(text + "\n", encoding="utf-8")


# ─── Interactive prompt ───────────────────────────────────────────────────
def _choose_action(diff: dict, default_source: str, auto: bool) -> str:
    """Return ``'delete'``, ``'skip'``, or ``'translate:<lang>'``."""
    if auto:
        src = default_source if default_source in diff["present"] else (
            diff["present"][0] if diff["present"] else None
        )
        if not src:
            return "skip"
        return f"translate:{src}"

    sys.stdout.write(f"\n[{diff['page']}] key '{diff['key']}'\n")
    sys.stdout.write(f"  present in: {', '.join(diff['present'])}\n")
    sys.stdout.write(f"  missing in: {', '.join(diff['missing'])}\n")
    sys.stdout.write("  existing values:\n")
    for lang in diff["present"]:
        sys.stdout.write(f"    {lang}: {diff['values'][lang]!r}\n")
    sys.stdout.write("\nchoose action:\n")
    opts: list[Tuple[str, str]] = []
    for lang in diff["present"]:
        marker = " (recommended)" if lang == default_source else ""
        opts.append((lang, f"translate from {lang}{marker}"))
    n = len(opts)
    opts.append(("__delete__", "delete from every JSON for this page"))
    opts.append(("__skip__", "skip — leave as-is"))
    for idx, (label, desc) in enumerate(opts, start=1):
        sys.stdout.write(f"  [{idx}] {desc}\n")
    while True:
        try:
            choice = input("choice: ").strip()
        except EOFError:
            return "skip"
        if not choice.isdigit():
            sys.stdout.write("  please pick one of the listed options\n")
            continue
        idx = int(choice)
        if not 1 <= idx <= n + 2:
            sys.stdout.write("  please pick one of the listed options\n")
            continue
        label = opts[idx - 1][0]
        if label == "__delete__":
            return "delete"
        if label == "__skip__":
            return "skip"
        return f"translate:{label}"


# ─── Apply a single inconsistency ────────────────────────────────────────
def apply_diff(
    diff: dict,
    by_page: dict[str, dict[str, dict]],
    static_dir: Path,
    *,
    default_source: str,
    auto: bool,
) -> Tuple[bool, bool]:
    """Apply one diff. Returns ``(modified, ok)``."""
    action = _choose_action(diff, default_source, auto)
    page = diff["page"]
    key = diff["key"]
    langs_data = by_page[page]

    if action == "skip":
        sys.stdout.write("  …skipped\n")
        return False, True

    if action == "delete":
        for data in langs_data.values():
            if key in data:
                del data[key]
        _persist_page(page, langs_data, static_dir)
        sys.stdout.write(f"  ✓ deleted '{key}' from {page}_*.json\n")
        return True, True

    if action.startswith("translate:"):
        source = action.split(":", 1)[1]
        source_value = diff["values"][source]
        for target in diff["missing"]:
            sys.stdout.write(f"  …translating {LANG_LABEL[source]} → {LANG_LABEL[target]}\n")
            translated = translate(source_value, source, target)
            if translated is None:
                return False, False
            # Only ever write to languages that already have a file for
            # this page (we never create new files from scratch).
            langs_data[target][key] = translated
        _persist_page(page, langs_data, static_dir)
        sys.stdout.write(
            f"  ✓ added '{key}' to {len(diff['missing'])} language(s) of {page}\n"
        )
        return True, True

    return False, False


def _persist_page(page: str, langs_data: dict[str, dict], static_dir: Path) -> None:
    for lang in LANGS:
        if lang not in langs_data:
            continue
        path = static_dir / f"{page}_{lang}.json"
        if not path.exists():
            continue
        indent = _detect_indent(path)
        save_page(path, langs_data[lang], indent)


# ─── CLI ─────────────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--check", action="store_true",
        help="Report inconsistencies only [default when --apply is not set].",
    )
    p.add_argument(
        "--apply", action="store_true",
        help="Prompt for each inconsistency and apply fixes.",
    )
    p.add_argument(
        "--yes", action="store_true",
        help="Auto-accept the recommended translation (use with --apply). "
             "Never auto-deletes.",
    )
    p.add_argument(
        "--source", default="eng",
        help="Default source language for translations (default: eng).",
    )
    p.add_argument(
        "--page", action="append", default=[],
        help="Only validate this page (repeatable).",
    )
    p.add_argument(
        "--static-dir", default=str(STATIC_DIR),
        help="Path to the static/ folder (default: project root + /static).",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.apply:
        args.check = True

    static_dir = Path(args.static_dir)
    if not static_dir.is_dir():
        sys.stderr.write(f"static dir not found: {static_dir}\n")
        return 2
    if args.source not in LANGS:
        sys.stderr.write(f"unknown --source language: {args.source}\n")
        return 2

    by_page = load_all(static_dir)

    if args.page:
        wanted = set(args.page)
        by_page = {p: v for p, v in by_page.items() if p in wanted}
        missing_pages = wanted - set(by_page.keys())
        if missing_pages:
            sys.stderr.write(
                f"warning: page(s) not found: {', '.join(sorted(missing_pages))}\n"
            )

    diffs = compute_diffs(by_page)
    missing_files = compute_missing_files(by_page)

    if not diffs and not missing_files:
        sys.stdout.write("✓ every page-language JSON is consistent.\n")
        return 0

    if diffs:
        sys.stdout.write(f"found {len(diffs)} inconsistent field(s):\n")
        for d in diffs:
            sys.stdout.write(
                f"  [{d['page']}] {d['key']:35s}  "
                f"present={d['present']}  missing={d['missing']}\n"
            )
    if missing_files:
        sys.stdout.write(
            f"\nfound {len(missing_files)} page(s) missing language files:\n"
        )
        for page, absent in sorted(missing_files.items()):
            sys.stdout.write(f"  [{page}] no file for: {', '.join(absent)}\n")

    sys.stdout.write(
        "\nnote: `_meta` is page metadata (e.g. html_lang, page name) and is "
        "intentionally not part of the translation check.\n"
    )

    if args.check and not args.apply:
        sys.stdout.write(
            "\nrun with --apply to fix these; pass --yes to skip prompts.\n"
        )
        return 1

    modified_pages: set[str] = set()
    failed = 0
    for d in diffs:
        try:
            modified, ok = apply_diff(
                d, by_page, static_dir,
                default_source=args.source,
                auto=args.yes,
            )
        except KeyboardInterrupt:
            sys.stdout.write("\ninterrupted.\n")
            return 130
        if modified:
            modified_pages.add(d["page"])
        if not ok:
            failed += 1

    sys.stdout.write(
        f"\nfinished. modified pages: {len(modified_pages)} "
        f"({', '.join(sorted(modified_pages)) or 'none'}). "
        f"failed diffs: {failed}.\n"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

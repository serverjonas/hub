# Changelog

All notable changes to this project are documented here. Each entry
follows the format `[YYYY-MM-DD]` followed by typed bullet points
(`Add`, `Change`, `Fix`, `Refactor`, …).

## serverjonas

### Add
- `[2026-07-03]` Add: New **notes** module — markdown editor backed by the cloud folder. Every `.md` file in `data/cloud/<user_id>/…` is automatically picked up; new notes save alongside existing cloud files using the user's chosen filename. Includes live preview (marked.js + DOMPurify), autosave with debounce, Ctrl/Cmd+S shortcut, recursive sidebar, optional nested folders, storage-quota enforcement, and full 9-language i18n.
- `[2026-07-03]` Add: Top-level `/notes` navigation link in the desktop nav and mobile-nav menu.
- `[2026-07-03]` Add: `nav.notes` translation key in all `static/base_<lang>.json` files (deu/eng/spa/fra/ita/nld/por/pol/rus).
- `[2026-07-03]` Add: `static/notes_<lang>.json` translation files for all 9 supported languages.

### Change
- `[2026-07-03]` Change: `modules.toml` — registered the new `notes` module at `/notes`.

## earlier history

See `git log` for prior commits.

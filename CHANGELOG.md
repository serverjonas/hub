# Changelog

All notable changes to this project are documented here. Each entry
follows the format `[YYYY-MM-DD]` followed by typed bullet points
(`Add`, `Change`, `Fix`, `Refactor`, …).

## serverjonas

### Add
- `[2026-07-22]` Add: New **casino** module — five play-money games seeded from a per-user SQLite DB (`data/casino/casino.db`). Daily wallet of **1000 coins**, reset lazily to 1000 each night on the user's next access. All RNG is `secrets.SystemRandom` (OS CSPRNG). Games: Slots (weighted reels, triple-7 pays 50×), Blackjack (S17 dealer, 3:2 natural, hit/stand/double), European Roulette (single-zero, red/black/parity/range/dozen/straight-up), Dice (low/high/7/exact-sum) and Texas Hold'em heads-up poker (best 5-of-7 hand-takes-pot, push on ties). Every wallet mutation runs inside `BEGIN IMMEDIATE` so two parallel requests from the same user can't double-debit.
- `[2026-07-22]` Add: Casino lobby + five game pages under `templates/casino/` with hand-drawn tile UI, animated reels, pip-style dice, playing cards with deal animations, and shared "fair by design" fairness banner.
- `[2026-07-22]` Add: `/casino`-API endpoints `GET /api/balance`, `POST /api/slots/spin`, `POST /api/blackjack/deal`, `POST /api/blackjack/action`, `POST /api/roulette/spin`, `POST /api/dice/roll`, `POST /api/poker/play`.
- `[2026-07-22]` Add: Top-level `/casino` navigation link in desktop nav and mobile-nav menu.
- `[2026-07-22]` Add: `nav.casino` translation key in all `static/base_<lang>.json` files (deu/eng/spa/fra/ita/nld/por/pol/rus).
- `[2026-07-22]` Add: `static/casino_<lang>.json` translation files for all 9 supported languages (≈80 keys each, full translations plus English fallback).

### Change
- `[2026-07-22]` Change: `modules.toml` — registered the new `casino` module at `/casino`.
- `[2026-07-22]` Change: `templates/base.html` — added the casino link to both desktop `.nav-links` and the `.mobile-nav` menu between Notes and Cloud.
- `[2026-07-03]` Add: New **notes** module — markdown editor backed by the cloud folder. Every `.md` file in `data/cloud/<user_id>/…` is automatically picked up; new notes save alongside existing cloud files using the user's chosen filename. Includes live preview (marked.js + DOMPurify), autosave with debounce, Ctrl/Cmd+S shortcut, recursive sidebar, optional nested folders, storage-quota enforcement, and full 9-language i18n.
- `[2026-07-03]` Add: Top-level `/notes` navigation link in the desktop nav and mobile-nav menu.
- `[2026-07-03]` Add: `nav.notes` translation key in all `static/base_<lang>.json` files (deu/eng/spa/fra/ita/nld/por/pol/rus).
- `[2026-07-03]` Add: `static/notes_<lang>.json` translation files for all 9 supported languages.

### Change
- `[2026-07-03]` Change: `modules.toml` — registered the new `notes` module at `/notes`.
- `[2026-07-08]` Change: `app.py` - moved load_modules() to if __name__ == "__main__" 

## earlier history

See `git log` for prior commits.

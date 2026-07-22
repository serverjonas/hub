"""
SJN Casino — play-money casino games.

A small Flask-only casino with:
  * Slot Machine
  * Blackjack
  * Roulette (European, single 0)
  * Dice (Hi-Lo 2d6)
  * Texas Hold'em poker (heads-up vs the dealer, single bet, full deal)

Money model
-----------
Every user starts each day with exactly **1000 fake coins**. At **0:00
local server time**, every wallet is reset back to 1000 — any coins
above 1000 kept across the day are lost on the user's next interaction
(this is intentional: a real casino is equally merciless).

The reset is *lazy*: we check the wallet's ``last_reset_date`` against
``date.today()``. If they differ, the wallet is brought back to 1000
**inside the same transaction** as the bet/credit that triggered the
reset — so a wallet row is always in a consistent state at commit
time. No background scheduler thread required.

Concurrency
-----------
All wallet mutations run inside ``BEGIN IMMEDIATE`` transactions so two
parallel requests from the same user cannot double-debit or
double-credit. SQLite's ``BEGIN IMMEDIATE`` acquires the database write
lock up-front, which serialises the read-modify-write inside each
request.

RNG
---
``secrets.SystemRandom`` everywhere (cryptographic-class RNG from the
OS). No exotic seeding, no need for a separate fairness scheme —
explicitly documented in the lobby page so players understand nothing's
rigged.

State
-----
A few games (notably blackjack) need multi-step state. We keep a tiny
``casino_blackjack`` table keyed on ``user_id``. One active hand per
user at a time; the row is dropped as soon as the hand settles.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import date
from typing import Optional

from flask import (
    Blueprint,
    abort,
    jsonify,
    render_template,
    request,
)

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
)

from toolbox.files import BASE_DIR
from toolbox.user import get_current_user, is_banned

from modules.casino.poker_eval import (
    best_5_of_7,
    category_name,
    make_deck,
)


# ─── Configuration ──────────────────────────────────────────────────────────
STARTING_BALANCE = 1000
MIN_BET = 1
# Sanity-clamp maximum single-chip bet so a user can't blow the daily
# allowance in one chip. Per-spin caps (e.g. roulette total stake) are
# derived from this.
MAX_BET = STARTING_BALANCE   # per-chip cap (single bet * 1 chip)
MAX_BETS_PER_ROUND = 12       # hard ceiling on multi-bet rounds (e.g. roulette)
# Per-round total-stake cap: the largest *aggregate* stake a single round
# can deduct from the wallet. Equal to per-chip cap times the chip
# ceiling so a player can spread chips across the table.
MAX_TOTAL_STAKE = MAX_BET * MAX_BETS_PER_ROUND

DB_DIR = os.path.join(BASE_DIR, "data", "casino")
DB_PATH = os.path.join(DB_DIR, "casino.db")

# ─── RNG (CSPRNG from OS) ────────────────────────────────────────────────────
# Single shared instance. ``secrets.SystemRandom`` is documented as
# thread-safe (it pulls from the OS entropy pool per call).
RNG = secrets.SystemRandom()

# ─── Blueprint ──────────────────────────────────────────────────────────────
bp = Blueprint("casino", __name__)


# ─── Database init (idempotent) ─────────────────────────────────────────────
def _init_db() -> None:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS casino_wallets (
            user_id        INTEGER PRIMARY KEY,
            balance        INTEGER NOT NULL DEFAULT 1000 CHECK (balance >= 0),
            last_reset_date TEXT    NOT NULL DEFAULT (date('now','localtime')),
            updated_at     INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS casino_blackjack (
            user_id        INTEGER PRIMARY KEY,
            bet_amount     INTEGER NOT NULL,
            player_cards   TEXT    NOT NULL,    -- JSON list of [rank, suit]
            dealer_cards   TEXT    NOT NULL,
            doubled        INTEGER NOT NULL DEFAULT 0,
            status         TEXT    NOT NULL,    -- 'active' | 'done'
            result         TEXT,
            last_action_at INTEGER NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


_init_db()


# ─── Wallet helpers (all mutations go through BEGIN IMMEDIATE) ───────────────

@contextmanager
def _wallet_session(user_id: int):
    """Open a write-locked SQLite tx, ensure the wallet row exists for
    *today*, and yield ``(conn, balance)``.

    The wallet row is created with 1000 coins if it doesn't exist yet,
    and any prefix-day balance is overwritten to 1000. The
    contextmanager handles ``commit`` on success or ``rollback`` on
    exception, so callers only need to issue the final ``UPDATE`` (if
    any) and the implicit commit fires on exit.
    """
    today = date.today().strftime("%Y-%m-%d")  # server local
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()
        cur.execute(
            "SELECT balance, last_reset_date FROM casino_wallets "
            "WHERE user_id = ?",
            (user_id,),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                """
                INSERT INTO casino_wallets
                    (user_id, balance, last_reset_date, updated_at)
                VALUES (?, ?, ?, strftime('%s','now'))
                """,
                (user_id, STARTING_BALANCE, today),
            )
            balance = STARTING_BALANCE
        else:
            balance, last_reset = int(row[0]), row[1]
            if last_reset != today:
                balance = STARTING_BALANCE
                cur.execute(
                    """
                    UPDATE casino_wallets
                       SET balance = ?, last_reset_date = ?,
                           updated_at = strftime('%s','now')
                     WHERE user_id = ?
                    """,
                    (STARTING_BALANCE, today, user_id),
                )
        yield conn, balance
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except sqlite3.OperationalError:
            pass
        raise
    finally:
        conn.close()


def get_balance(user_id: int) -> int:
    """Pure read; applies daily reset if needed."""
    with _wallet_session(user_id) as (_conn, balance):
        return int(balance)


def deduct_bet(user_id: int, amount: int):
    """Atomically deduct *amount* from the user's wallet.

    Returns ``(ok, balance_after_or_error_code, error_code_or_None)``.
    The upper bound is per-round (``MAX_TOTAL_STAKE``), not per-chip.
    Single-bet routes still pass ``MIN_BET..MAX_BET`` via
    ``_parse_bet`` for the per-chip cap; multi-bet routes pass
    ``stake * len(bets)`` which lives in ``[MIN_BET, MAX_TOTAL_STAKE]``.
    The actual hard ceiling is the user's current balance, checked
    atomically below.
    """
    if amount < MIN_BET or amount > MAX_TOTAL_STAKE:
        return False, 0, "invalid_bet"
    with _wallet_session(user_id) as (conn, balance):
        if balance < amount:
            return False, int(balance), "insufficient"
        new_balance = balance - amount
        conn.execute(
            "UPDATE casino_wallets SET balance = ?, "
            "updated_at = strftime('%s','now') WHERE user_id = ?",
            (new_balance, user_id),
        )
        return True, int(new_balance), None


def credit_winnings(user_id: int, amount: int) -> int:
    """Atomically add *amount* (winnings) to the user's wallet.

    Returns the new balance. No-op if ``amount <= 0``.
    """
    if amount <= 0:
        return get_balance(user_id)
    with _wallet_session(user_id) as (conn, balance):
        new_balance = balance + amount
        conn.execute(
            "UPDATE casino_wallets SET balance = ?, "
            "updated_at = strftime('%s','now') WHERE user_id = ?",
            (new_balance, user_id),
        )
        return int(new_balance)


# ─── Slots ──────────────────────────────────────────────────────────────────
# Symbol weights — higher = more common.
_SLOT_SYMBOLS = [
    ("🍒", 60),  # Cherry
    ("🍋", 45),  # Lemon
    ("BAR", 35), # BAR
    ("🔔", 25),  # Bell
    ("⭐", 18),  # Star
    ("💎", 12),  # Diamond
    ("7",  5),   # Seven (jackpot)
]
_SLOT_WEIGHTED = [(s, w) for s, w in _SLOT_SYMBOLS]

# Payout table: triple match → multiplier.
_SLOT_PAYOUTS = {
    frozenset({"7"}): 50,
    frozenset({"💎"}): 25,
    frozenset({"⭐"}): 15,
    frozenset({"🔔"}): 10,
    frozenset({"🍒"}): 5,
    frozenset({"🍋"}): 5,
    frozenset({"BAR"}): 5,
}
# 2-of-a-kind payouts for the three premium symbols.
_SLOT_2X_PAYOUTS = {
    "💎": 3,
    "⭐": 2,
    "🔔": 2,
}


def _play_slots(bet: int):
    """Spin three weighted-reel slots. Returns
    ``(reel, multiplier, payout, hit_kind)`` where ``hit_kind`` is one
    of ``'triple'``/``'double'``/``'none'``.
    """
    pool = [s for s, w in _SLOT_WEIGHTED for _ in range(w)]
    reel = [RNG.choice(pool) for _ in range(3)]
    unique = frozenset(reel)

    multiplier = 0
    hit_kind = "none"

    if len(unique) == 1:
        symbol = next(iter(unique))
        multiplier = _SLOT_PAYOUTS.get(frozenset({symbol}), 0)
        if multiplier:
            hit_kind = "triple"
    elif len(unique) == 2:
        counts = Counter(reel)
        paired = next(sym for sym, c in counts.items() if c == 2)
        m = _SLOT_2X_PAYOUTS.get(paired)
        if m:
            multiplier = m
            hit_kind = "double"

    payout = bet * multiplier
    return reel, multiplier, int(payout), hit_kind


# ─── Blackjack ──────────────────────────────────────────────────────────────
# Rank encoding (must match poker_eval.py): 0=2, 1=3, ..., 7=9, 8=10,
# 9=J, 10=Q, 11=K, 12=A.

def _bj_value(rank: int) -> int:
    """Face value of a single card for blackjack. Aces are counted as
    11 in this raw pass; the 11→1 reduction is done in ``_bj_total``.
    """
    if rank == 12:        # Ace
        return 11
    if rank >= 8:         # Ten / J / Q / K (ranks 8..11)
        return 10
    return rank + 2       # Literal pip: rank 0=2, rank 7=9.


def _bj_total(cards) -> int:
    """Best (≤21) total for a blackjack hand. Reduces an Ace from 11
    to 1 whenever doing so avoids a bust.
    """
    total = 0
    aces = 0
    for r, _s in cards:
        if r == 12:
            aces += 1
        total += _bj_value(r)
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def _bj_is_natural(cards) -> bool:
    """Natural 21 (Ace + 10-value) on the opening 2 cards."""
    return len(cards) == 2 and _bj_total(cards) == 21


def _bj_dealer_should_hit(cards) -> bool:
    """Dealer hits until total ≥ 17 (S17)."""
    return _bj_total(cards) < 17


def _bj_serialize(cards) -> str:
    return json.dumps([list(c) for c in cards])


def _bj_deserialize(raw: str):
    return [tuple(c) for c in json.loads(raw)]


def _bj_new_hand(user_id: int, bet: int):
    """Deal a fresh blackjack hand:
    1. Deduct the bet.
    2. Deal two cards to player + dealer each.
    3. If player has a natural 21, settle immediately (no action round).
       Otherwise persist the hand as ``active``.
    """
    ok, _balance_after, err = deduct_bet(user_id, bet)
    if not ok:
        return {"error": err, "balance": get_balance(user_id)}

    deck = make_deck()
    player = [deck.pop(), deck.pop()]
    dealer = [deck.pop(), deck.pop()]

    # Natural 21 → settle now, no action round.
    if _bj_is_natural(player):
        if _bj_is_natural(dealer):
            settle = bet           # push — return the bet
            result = "push"
        else:
            settle = int(bet * 3 // 2)   # 3:2 blackjack payout
            result = "bj"
        new_balance = credit_winnings(user_id, settle)
        return {
            "state": "done",
            "player_cards": [list(c) for c in player],
            "dealer_cards": [list(c) for c in dealer],
            "player_total": _bj_total(player),
            "dealer_total": _bj_total(dealer),
            "dealer_revealed": True,
            "result": result,
            "payout": settle,
            "balance": int(new_balance),
            "bet": bet,
        }

    # Otherwise hand is active — persist in DB.
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            INSERT INTO casino_blackjack
                (user_id, bet_amount, player_cards, dealer_cards,
                 doubled, status, last_action_at)
            VALUES (?, ?, ?, ?, 0, 'active', strftime('%s','now'))
            ON CONFLICT(user_id) DO UPDATE SET
                bet_amount     = excluded.bet_amount,
                player_cards   = excluded.player_cards,
                dealer_cards   = excluded.dealer_cards,
                doubled        = 0,
                status         = 'active',
                result         = NULL,
                last_action_at = excluded.last_action_at
            """,
            (user_id, bet, _bj_serialize(player), _bj_serialize(dealer)),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "state": "active",
        "player_cards": [list(c) for c in player],
        # Hide the dealer's hole card behind None so the client can't
        # peek into it before the player stands.
        "dealer_cards": [list(dealer[0]), None],
        "player_total": _bj_total(player),
        "dealer_total": None,
        "dealer_revealed": False,
        "result": None,
        "payout": 0,
        "balance": int(_balance_after),
        "bet": bet,
    }


def _bj_load_active(user_id: int):
    """Return ``(bet_amount, player, dealer, doubled_flag)`` for the
    user's active hand, or ``None`` if there isn't one.
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT bet_amount, player_cards, dealer_cards, doubled "
            "FROM casino_blackjack WHERE user_id = ? AND status = 'active'",
            (user_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return (
            int(row[0]),
            _bj_deserialize(row[1]),
            _bj_deserialize(row[2]),
            int(row[3]),
        )
    finally:
        conn.close()


def _bj_persist_active(user_id, bet_amount, player, dealer, doubled):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            UPDATE casino_blackjack
               SET bet_amount     = ?,
                   player_cards   = ?,
                   dealer_cards   = ?,
                   doubled        = ?,
                   last_action_at = strftime('%s','now')
             WHERE user_id = ? AND status = 'active'
            """,
            (
                bet_amount,
                _bj_serialize(player),
                _bj_serialize(dealer),
                int(bool(doubled)),
                user_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _bj_drop_hand(user_id):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "DELETE FROM casino_blackjack WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _bj_draw_from(deck) -> tuple:
    """Pop one card; if the deck is empty, build a fresh one. Simulates
    an improperly cut shoe — slightly unrealistic, but the player has
    no way to exploit it.
    """
    if not deck:
        deck.extend(make_deck())
    return deck.pop()


def _bj_dealer_play(dealer, deck):
    while _bj_dealer_should_hit(dealer):
        dealer.append(_bj_draw_from(deck))


def _bj_compare(player, dealer, bet):
    """Returns ``(payout, result)`` for a settled hand.

    Payout is the amount credited on top of the bet that was already
    deducted: 0 = lose, bet = push, 2*bet = win.
    """
    p_total = _bj_total(player)
    d_total = _bj_total(dealer)
    if p_total > 21:
        return 0, "lose"
    if d_total > 21:
        return 2 * bet, "win"
    if p_total > d_total:
        return 2 * bet, "win"
    if p_total < d_total:
        return 0, "lose"
    return bet, "push"


def _bj_settle_payload(user_id, player, dealer, bet, result, payout, balance=None):
    return {
        "state": "done",
        "player_cards": [list(c) for c in player],
        "dealer_cards": [list(c) for c in dealer],
        "player_total": _bj_total(player),
        "dealer_total": _bj_total(dealer),
        "dealer_revealed": True,
        "result": result,
        "payout": int(payout),
        "balance": int(balance if balance is not None else get_balance(user_id)),
        "bet": int(bet),
    }


def _bj_action(user_id: int, action: str):
    """Apply ``action`` ∈ {hit, stand, double} to the user's active hand."""
    action = (action or "").strip().lower()
    if action not in {"hit", "stand", "double"}:
        return {"error": "unknown_action"}
    loaded = _bj_load_active(user_id)
    if loaded is None:
        return {"error": "no_active_hand"}
    bet_amount, player, dealer, doubled_flag = loaded

    shoe = make_deck()

    if action == "double":
        if doubled_flag:
            return {"error": "already_doubled"}
        if len(player) != 2:
            return {"error": "double_only_on_two_cards"}
        ok, _balance_after, err = deduct_bet(user_id, bet_amount)
        if not ok:
            return {"error": err, "balance": get_balance(user_id)}
        bet = bet_amount * 2
        player.append(_bj_draw_from(shoe))
        if _bj_total(player) > 21:
            _bj_drop_hand(user_id)
            return _bj_settle_payload(user_id, player, dealer, bet, "lose", 0)
        _bj_dealer_play(dealer, shoe)
        payout, result = _bj_compare(player, dealer, bet)
        new_balance = credit_winnings(user_id, payout)
        _bj_drop_hand(user_id)
        return _bj_settle_payload(
            user_id, player, dealer, bet, result, payout, new_balance
        )

    if action == "hit":
        player.append(_bj_draw_from(shoe))
        if _bj_total(player) > 21:
            _bj_drop_hand(user_id)
            return _bj_settle_payload(
                user_id, player, dealer, bet_amount, "lose", 0
            )
        _bj_persist_active(user_id, bet_amount, player, dealer, doubled=0)
        return {
            "state": "active",
            "player_cards": [list(c) for c in player],
            "dealer_cards": [list(dealer[0]), None],
            "player_total": _bj_total(player),
            "dealer_total": None,
            "dealer_revealed": False,
            "result": None,
            "payout": 0,
            "balance": get_balance(user_id),
            "bet": bet_amount,
        }

    # stand
    _bj_dealer_play(dealer, shoe)
    payout, result = _bj_compare(player, dealer, bet_amount)
    new_balance = credit_winnings(user_id, payout)
    _bj_drop_hand(user_id)
    return _bj_settle_payload(
        user_id, player, dealer, bet_amount, result, payout, new_balance
    )


# ─── Roulette ──────────────────────────────────────────────────────────────
# European (single zero). 37 outcomes: 0 + 1..36.
ROULETTE_RED = {
    1, 3, 5, 7, 9, 12, 14, 16, 18,
    19, 21, 23, 25, 27, 30, 32, 34, 36,
}
ROULETTE_BLACK = {
    2, 4, 6, 8, 10, 11, 13, 15, 17,
    20, 22, 24, 26, 28, 29, 31, 33, 35,
}
_ROULETTE_BET_TYPES = frozenset({"color", "parity", "range", "dozen", "number"})


def _roulette_payout(bet_type: str, bet_value, lucky: int):
    """Returns ``(multiplier, lost)``. Exactly one is set on a win;
    ``multiplier`` is the bet multiplier (e.g. 35 for a single number).
    """
    if bet_type == "color":
        v = (bet_value or "").lower()
        if v == "red" and lucky in ROULETTE_RED:
            return 1, False
        if v == "black" and lucky in ROULETTE_BLACK:
            return 1, False
        return 0, True
    if bet_type == "parity":
        v = (bet_value or "").lower()
        if lucky == 0:
            return 0, True
        if v == "even" and lucky % 2 == 0:
            return 1, False
        if v == "odd" and lucky % 2 == 1:
            return 1, False
        return 0, True
    if bet_type == "range":
        v = (bet_value or "").lower()
        if v == "low" and 1 <= lucky <= 18:
            return 1, False
        if v == "high" and 19 <= lucky <= 36:
            return 1, False
        return 0, True
    if bet_type == "dozen":
        try:
            dozen = int(bet_value)
        except (TypeError, ValueError):
            return 0, True
        if dozen == 1 and 1 <= lucky <= 12:
            return 2, False
        if dozen == 2 and 13 <= lucky <= 24:
            return 2, False
        if dozen == 3 and 25 <= lucky <= 36:
            return 2, False
        return 0, True
    if bet_type == "number":
        try:
            n = int(bet_value)
        except (TypeError, ValueError):
            return 0, True
        if 0 <= n <= 36 and n == lucky:
            return 35, False
        return 0, True
    return 0, True


def _roulette_color(n: int) -> str:
    if n == 0:
        return "green"
    return "red" if n in ROULETTE_RED else "black"


# ─── Dice (Hi-Lo 2d6) ───────────────────────────────────────────────────────
DICE_PAYOUTS = {
    2: 35, 3: 17, 4: 11, 5: 8, 6: 6, 7: 5,
    8: 6, 9: 8, 10: 11, 11: 17, 12: 35,
}


def _dice_payout(bet_type: str, lucky_sum: int) -> int:
    if bet_type == "sum":
        return DICE_PAYOUTS.get(lucky_sum, 0)
    if bet_type == "low" and 2 <= lucky_sum <= 6:
        return 1
    if bet_type == "seven" and lucky_sum == 7:
        return 4
    if bet_type == "high" and 8 <= lucky_sum <= 12:
        return 1
    return 0


# ─── Poker (Texas Hold'em heads-up) ─────────────────────────────────────────
def _poker_round(user_id: int, bet: int):
    """Single-shot Texas Hold'em: deal 7 cards (2 hole + 5 community),
    evaluate both hands, settle. ``bet`` is on the line; winner takes
    ``2*bet``; tie returns the bet (push)."""
    ok, _balance_after, err = deduct_bet(user_id, bet)
    if not ok:
        return {"error": err, "balance": get_balance(user_id)}

    deck = make_deck()
    player_hole = [deck.pop(), deck.pop()]
    ai_hole = [deck.pop(), deck.pop()]
    community = [deck.pop() for _ in range(5)]

    p_score = best_5_of_7(player_hole + community)
    a_score = best_5_of_7(ai_hole + community)

    if p_score > a_score:
        result = "win"
        payout = 2 * bet
    elif p_score < a_score:
        result = "lose"
        payout = 0
    else:
        result = "push"
        payout = bet

    new_balance = credit_winnings(user_id, payout)

    return {
        "result": result,
        "player_hole": [list(c) for c in player_hole],
        "ai_hole": [list(c) for c in ai_hole],
        "community": [list(c) for c in community],
        "player_score": list(p_score),
        "player_hand": category_name(p_score),
        "ai_score": list(a_score),
        "ai_hand": category_name(a_score),
        "payout": int(payout),
        "balance": int(new_balance),
        "bet": int(bet),
    }


# ─── Routes ─────────────────────────────────────────────────────────────────

def _require_active_user():
    cu = get_current_user()
    if cu is None:
        abort(401)
    banned, _ = is_banned(cu["id"])
    if banned:
        abort(403)
    return cu


def _parse_bet(raw) -> Optional[int]:
    """Validate the bet amount. Returns the integer value, or ``None``
    if invalid (caller should return a 400).
    """
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return None
    if v < MIN_BET or v > MAX_BET:
        return None
    return v


# ─── Lobby ──────────────────────────────────────────────────────────────────
@bp.route("/", methods=["GET"])
def lobby():
    cu = _require_active_user()
    balance = get_balance(cu["id"])
    today = date.today().strftime("%Y-%m-%d")
    return render_template(
        "casino/lobby.html",
        user=cu["name"],
        balance=int(balance),
        starting_balance=STARTING_BALANCE,
        today=today,
    )


@bp.route("/api/balance", methods=["GET"])
def api_balance():
    cu = _require_active_user()
    return jsonify(balance=int(get_balance(cu["id"])))


# ─── Slots ──────────────────────────────────────────────────────────────────
@bp.route("/slots", methods=["GET"])
def slots_page():
    cu = _require_active_user()
    return render_template(
        "casino/slots.html",
        user=cu["name"],
        balance=int(get_balance(cu["id"])),
        min_bet=MIN_BET,
        max_bet=MAX_BET,
    )


@bp.route("/api/slots/spin", methods=["POST"])
def api_slots_spin():
    cu = _require_active_user()
    bet = _parse_bet(request.form.get("bet"))
    if bet is None:
        return jsonify(error="invalid_bet"), 400
    ok, balance_after, err = deduct_bet(cu["id"], bet)
    if not ok:
        return jsonify(error=err, balance=int(balance_after)), 400
    reels, multiplier, payout, hit_kind = _play_slots(bet)
    new_balance = credit_winnings(cu["id"], payout)
    return jsonify(
        reels=reels,
        multiplier=int(multiplier),
        payout=int(payout),
        hit_kind=hit_kind,
        balance=int(new_balance),
        bet=int(bet),
    )


# ─── Blackjack ──────────────────────────────────────────────────────────────
@bp.route("/blackjack", methods=["GET"])
def blackjack_page():
    cu = _require_active_user()
    return render_template(
        "casino/blackjack.html",
        user=cu["name"],
        balance=int(get_balance(cu["id"])),
        min_bet=MIN_BET,
        max_bet=MAX_BET,
    )


@bp.route("/api/blackjack/deal", methods=["POST"])
def api_blackjack_deal():
    cu = _require_active_user()
    bet = _parse_bet(request.form.get("bet"))
    if bet is None:
        return jsonify(error="invalid_bet"), 400
    return jsonify(_bj_new_hand(cu["id"], bet))


@bp.route("/api/blackjack/action", methods=["POST"])
def api_blackjack_action():
    cu = _require_active_user()
    action = (request.form.get("action") or "").strip().lower()
    return jsonify(_bj_action(cu["id"], action))


# ─── Roulette ──────────────────────────────────────────────────────────────
@bp.route("/roulette", methods=["GET"])
def roulette_page():
    cu = _require_active_user()
    return render_template(
        "casino/roulette.html",
        user=cu["name"],
        balance=int(get_balance(cu["id"])),
        min_bet=MIN_BET,
        max_bet=MAX_BET,
    )


@bp.route("/api/roulette/spin", methods=["POST"])
def api_roulette_spin():
    """Multi-bet spin: the client posts one ``bet_type``/``bet_value``
    pair per chip placed on the table, repeating the keys for each
    chip. The shared ``bet`` key is the stake *per chip* (so the total
    deducted from the wallet equals ``len(bets) * bet``).
    """
    cu = _require_active_user()
    stake = _parse_bet(request.form.get("bet"))
    if stake is None:
        return jsonify(error="invalid_bet"), 400

    raw_types = request.form.getlist("bet_type")
    raw_values = request.form.getlist("bet_value")
    # Pad bet_value if the client only sent one — supports single-chip
    # requests without forcing repeated empty values.
    while len(raw_values) < len(raw_types):
        raw_values.append("")

    # Drop empty/whitespace-only bet_type entries so a single stray `&bet_type=`
    # doesn't masquerade as an invalid type. Real "no bets at all" still
    # surfaces as ``no_bets``.
    bets = []
    for bt_raw, bv_raw in zip(raw_types, raw_values):
        bt = (bt_raw or "").strip().lower()
        bv = (bv_raw or "").strip().lower()
        if not bt:
            continue
        if bt not in _ROULETTE_BET_TYPES:
            return jsonify(error="invalid_bet_type"), 400
        bets.append((bt, bv))
    if not bets:
        return jsonify(error="no_bets"), 400
    if len(bets) > MAX_BETS_PER_ROUND:
        return jsonify(error="too_many_bets"), 400

    total_stake = stake * len(bets)
    # Single-round cap is the user's wallet; no extra static MAX_TOTAL_STAKE.

    ok, balance_after, err = deduct_bet(cu["id"], total_stake)
    if not ok:
        return jsonify(error=err, balance=int(balance_after)), 400

    lucky = RNG.randint(0, 36)

    total_payout = 0
    hits = []
    for i, (bt, bv) in enumerate(bets):
        mult, lost = _roulette_payout(bt, bv, lucky)
        chip_payout = int(stake * mult) if not lost else 0
        total_payout += chip_payout
        if not lost:
            hits.append({
                "index": i,
                "bet_type": bt,
                "bet_value": bv,
                "multiplier": int(mult),
                "payout": int(chip_payout),
            })

    new_balance = credit_winnings(cu["id"], total_payout)
    return jsonify(
        lucky=int(lucky),
        color=_roulette_color(lucky),
        bets=hits,
        won=bool(hits),
        payout=int(total_payout),
        balance=int(new_balance),
        stake=int(stake),
        bet_count=len(bets),
    )


# ─── Dice ───────────────────────────────────────────────────────────────────
@bp.route("/dice", methods=["GET"])
def dice_page():
    cu = _require_active_user()
    return render_template(
        "casino/dice.html",
        user=cu["name"],
        balance=int(get_balance(cu["id"])),
        min_bet=MIN_BET,
        max_bet=MAX_BET,
    )


@bp.route("/api/dice/roll", methods=["POST"])
def api_dice_roll():
    cu = _require_active_user()
    bet = _parse_bet(request.form.get("bet"))
    if bet is None:
        return jsonify(error="invalid_bet"), 400
    bet_type = (request.form.get("bet_type") or "").strip().lower()
    target = None
    if bet_type == "sum":
        try:
            target = int(request.form.get("bet_value"))
        except (TypeError, ValueError):
            return jsonify(error="invalid_bet_value"), 400
        if target not in DICE_PAYOUTS:
            return jsonify(error="invalid_sum"), 400
    elif bet_type not in {"low", "high", "seven"}:
        return jsonify(error="invalid_bet_type"), 400
    ok, balance_after, err = deduct_bet(cu["id"], bet)
    if not ok:
        return jsonify(error=err, balance=int(balance_after)), 400
    d1 = RNG.randint(1, 6)
    d2 = RNG.randint(1, 6)
    s = d1 + d2
    if bet_type == "sum" and s == target:
        mult = DICE_PAYOUTS[s]
    else:
        mult = _dice_payout(bet_type, s)
    payout = int(bet * mult)
    new_balance = credit_winnings(cu["id"], payout)
    return jsonify(
        die1=int(d1),
        die2=int(d2),
        sum=int(s),
        bet_type=bet_type,
        bet_value=(str(target) if bet_type == "sum" else bet_type),
        multiplier=int(mult),
        won=mult > 0,
        payout=int(payout),
        balance=int(new_balance),
        bet=int(bet),
    )


# ─── Poker ──────────────────────────────────────────────────────────────────
@bp.route("/poker", methods=["GET"])
def poker_page():
    cu = _require_active_user()
    return render_template(
        "casino/poker.html",
        user=cu["name"],
        balance=int(get_balance(cu["id"])),
        min_bet=MIN_BET,
        max_bet=MAX_BET,
    )


@bp.route("/api/poker/play", methods=["POST"])
def api_poker_play():
    cu = _require_active_user()
    bet = _parse_bet(request.form.get("bet"))
    if bet is None:
        return jsonify(error="invalid_bet"), 400
    return jsonify(_poker_round(cu["id"], bet))

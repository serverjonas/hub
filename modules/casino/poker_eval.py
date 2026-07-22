"""
Pure-function poker hand evaluator for Texas Hold'em.

Card representation
-------------------
* Rank ``r`` is an int in ``[0, 12]`` where ``0`` = 2 ... ``12`` = Ace.
* Suit ``s`` is an int in ``[0, 3]`` (0=♣, 1=♦, 2=♥, 3=♠ — display order only).

Hand categories (higher = better) and their tuple shape
-------------------------------------------------------
1. ``High Card``        ``(1, r1, r2, r3, r4, r5)``     ordered desc
2. ``One Pair``         ``(2, pair_rank, k1, k2, k3)``  ordered desc
3. ``Two Pair``         ``(3, hi_pair, lo_pair, kicker)``
4. ``Three of a Kind``  ``(4, trip_rank, k1, k2)``
5. ``Straight``         ``(5, high_card)``
6. ``Flush``            ``(6, r1, r2, r3, r4, r5)``
7. ``Full House``       ``(7, trip_rank, pair_rank)``
8. ``Four of a Kind``   ``(8, quad_rank, kicker)``
9. ``Straight Flush``   ``(9, high_card)``

The tuple's category index (``1``..``9``) is the first element, so plain
Python ``max()`` over two hands already does the right thing — and
``itertools.combinations(cards, 5)`` over seven cards gives us the best
5-card hand automatically.

Ace-low straight
----------------
A-2-3-4-5 is a straight but its **high card** is the 5 (rank 3).
Without an override the natural rank order ``[12, 3, 2, 1, 0]`` would
treat it as Ace-high and unfairly beat a 6-high straight, so we patch
``straight_high`` to ``3`` for this specific case.
"""

from __future__ import annotations

from itertools import combinations
from collections import Counter


# ─── Rank helpers ────────────────────────────────────────────────────────────

_RANK_CHARS = "23456789TJQKA"


def rank_to_char(rank: int) -> str:
    """``0`` → ``'2'`` ... ``12`` → ``'A'``."""
    return _RANK_CHARS[rank]


_SUIT_CHARS = "♣♦♥♠"


def suit_to_char(suit: int) -> str:
    return _SUIT_CHARS[suit]


def card_to_str(card: tuple[int, int]) -> str:
    """``(12, 3)`` → ``'A♠'``."""
    return rank_to_char(card[0]) + suit_to_char(card[1])


# ─── Deck generation (used by the gameplay layer) ───────────────────────────

def make_deck() -> list[tuple[int, int]]:
    """Fresh shuffled 52-card deck (Fisher–Yates). Caller picks what they
    need; this helper exists so the gameplay layer has a single source of
    truth."""
    import secrets
    deck = [(r, s) for r in range(13) for s in range(4)]
    rng = secrets.SystemRandom()
    # Fisher–Yates from the top
    for i in range(len(deck) - 1, 0, -1):
        j = rng.randint(0, i)
        deck[i], deck[j] = deck[j], deck[i]
    return deck


# ─── 5-card evaluator ───────────────────────────────────────────────────────

def _eval_5(hand) -> tuple:
    """Score a 5-card hand. Returns a totally-ordered tuple."""
    ranks = sorted((c[0] for c in hand), reverse=True)
    suits = [c[1] for c in hand]

    is_flush = len(set(suits)) == 1

    is_straight = False
    is_ace_low = False
    if len(set(ranks)) == 5:
        if ranks[0] - ranks[4] == 4:
            # 5 consecutive ranks when sorted descending — covers every
            # straight EXCEPT the Ace-low case (because Ace is rank 12
            # there but its positional rank in *the straight* is the
            # LOW end, which breaks the rank[0]-rank[4]==4 invariant).
            is_straight = True
        elif set(ranks) == {0, 1, 2, 3, 12}:
            # A-2-3-4-5: Ace is rank 12 (Ace), the other four are 2-5
            # (ranks 0..3). High card of THIS straight is the 5 (rank 3).
            is_straight = True
            is_ace_low = True

    # Group (count desc, rank desc) so the tuple encoded below is
    # always ordered as natural tiebreaker would expect: highest group
    # first, then kickers in descending weight.
    grouped = sorted(
        Counter(ranks).items(),
        key=lambda x: (x[1], x[0]),
        reverse=True,
    )
    # pattern: tuple of counts.   e.g. (2, 2, 1) for two pair
    pattern = tuple(g[1] for g in grouped)
    # group-ranks in the same order: which rank each group represents
    # sorted desc so the "highest card" within a group leads naturally
    grouped_ranks = tuple(g[0] for g in grouped)
    # All five ranks, sorted desc, so each remaining card has a
    # canonical position in the final tuple.
    sorted_desc = tuple(sorted(set(ranks), reverse=True))

    if is_straight and is_flush:
        # Straight Flush / Royal. high card = the highest rank in the
        # straight (or 3 if it's the Ace-low straight).
        high = 3 if is_ace_low else ranks[0]
        return (9, high)
    if pattern == (4, 1):
        # Four of a kind — quad rank first, kicker after.
        return (8, grouped_ranks[0], grouped_ranks[1])
    if pattern == (3, 2):
        # Full House — trip rank first, pair rank second.
        return (7, grouped_ranks[0], grouped_ranks[1])
    if is_flush:
        # Flush — kickers in descending order.
        return (6, *sorted_desc)
    if is_straight:
        # See the straight-flush branch above for the Ace-low override.
        high = 3 if is_ace_low else ranks[0]
        return (5, high)
    if pattern == (3, 1, 1):
        # Three of a kind, then top two remaining kickers.
        k1, k2 = sorted((grouped_ranks[1], grouped_ranks[2]), reverse=True)
        return (4, grouped_ranks[0], k1, k2)
    if pattern == (2, 2, 1):
        # Two pair — high pair first, low pair second, kicker last.
        return (3, grouped_ranks[0], grouped_ranks[1], grouped_ranks[2])
    if pattern == (2, 1, 1, 1):
        # One pair, then top three kickers.
        kickers = sorted(grouped_ranks[1:], reverse=True)
        return (2, grouped_ranks[0], *kickers)
    # High card — emit all five ranks in descending order. ``sorted_desc``
    # is already that order, so we can just splat it.
    return (1, *sorted_desc)


# ─── Best 5 of 7 ────────────────────────────────────────────────────────────

def best_5_of_7(cards) -> tuple:
    """Return the best (highest-scoring) 5-card hand from 7 cards.

    Cards are (rank, suit) tuples. Returns the same shape as
    ``_eval_5``: a totally-ordered tuple suitable for direct comparison.
    """
    return max(_eval_5(h) for h in combinations(cards, 5))


# ─── Name lookup for display ────────────────────────────────────────────────

_CAT_NAMES = {
    9: "Straight Flush",
    8: "Four of a Kind",
    7: "Full House",
    6: "Flush",
    5: "Straight",
    4: "Three of a Kind",
    3: "Two Pair",
    2: "One Pair",
    1: "High Card",
}


def category_name(score: tuple) -> str:
    if not score:
        return ""
    cat = int(score[0])
    return _CAT_NAMES.get(cat, f"Unknown({cat})")

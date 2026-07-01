"""API v1 authentication.

Tokens are issued in the form::

    sjn_<prefix>_<secret>

* ``prefix`` is stored in plaintext in the DB and indexed for O(1) lookup.
* ``secret`` is hashed with SHA-256 and stored as ``key_hash``.
* Verification uses :func:`hmac.compare_digest` so it is constant-time.

This is NOT Argon2/PBKDF2 territory — those algorithms are for low-entropy
user passwords. For 256-bit system-random secrets, a fast SHA-256 is the
correct choice and avoids the previous design's footgun (every request
required a full PBKDF2/Argon2 evaluation per stored key, i.e. a trivial
DoS surface).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from functools import wraps
from typing import Iterable, Optional

from flask import g, jsonify, request

from toolbox.files import DB_PATH
from toolbox.user import get_name


# ─── Constants ────────────────────────────────────────────────────────────

PREFIX_LEN = 8           # 8 hex chars = 32 bits of identifier entropy
SECRET_LEN = 40          # 80 hex chars = 320 bits of secret entropy
TOKEN_PREFIX = "sjn_"    # All keys start with this so we never confuse them
                          # with anything else (sessions, OAuth, …)
HEADER_NAME = "Authorization"

# Default per-key rate limit: 600 requests per 60s window.
RATE_LIMIT = 600
RATE_WINDOW_SECONDS = 60

# Maximum in-memory rate-limit entries. Prevents an attacker sending
# millions of garbage tokens from blowing up RAM.
RL_MAX_BUCKETS = 10_000

# Scopes available to user-created keys. Keep this list narrow on purpose:
# more granular scopes → harder to manage, easier to misconfigure.
VALID_SCOPES: frozenset[str] = frozenset({"films:upload", "films:read"})


# ─── Token format helpers ─────────────────────────────────────────────────


def generate_api_key() -> tuple[str, str, str]:
    """Return ``(raw_token, prefix, secret)``.

    ``raw_token`` is what the user sees *once* on creation.
    ``prefix`` is what we store in plaintext and index.
    ``secret`` is the part we hash and never store in plaintext.
    """
    prefix = secrets.token_hex(PREFIX_LEN)        # 16 hex chars
    secret = secrets.token_hex(SECRET_LEN)        # 80 hex chars
    return f"{TOKEN_PREFIX}{prefix}_{secret}", prefix, secret


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def parse_authorization_header(header_value: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Extract the (prefix, secret) parts from an Authorization header.

    Returns ``(None, None)`` for missing, malformed, or non-Bearer-sjn tokens.
    """
    if not header_value:
        return None, None
    parts = header_value.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None, None
    token = parts[1]
    if not token.startswith(TOKEN_PREFIX):
        return None, None
    body = token[len(TOKEN_PREFIX):]
    bits = body.split("_", 1)
    if len(bits) != 2 or not bits[0] or not bits[1]:
        return None, None
    return bits[0], bits[1]


# ─── DB layer ─────────────────────────────────────────────────────────────


def _db() -> sqlite3.Connection:
    """Short-lived sqlite connection. Avoid the long-lived global anti-pattern."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def insert_api_key(
    *,
    user_id: int,
    prefix: str,
    key_hash: str,
    label: str,
    scopes: list[str],
    created_at: int,
) -> None:
    import json
    conn = _db()
    try:
        conn.execute(
            """
            INSERT INTO api_keys
                (user_id, prefix, key_hash, label, scopes, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, prefix, key_hash, label, json.dumps(scopes), created_at),
        )
        conn.commit()
    finally:
        conn.close()


def delete_api_key(*, user_id: int, key_id: int) -> bool:
    conn = _db()
    try:
        cur = conn.execute(
            "DELETE FROM api_keys WHERE user_id = ? AND id = ?",
            (user_id, key_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_api_keys(user_id: int) -> list[dict]:
    import json
    conn = _db()
    try:
        rows = conn.execute(
            """
            SELECT id, user_id, label, scopes, created_at, last_used_at
            FROM api_keys
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "label": r["label"],
                "scopes": json.loads(r["scopes"] or "[]"),
                "created_at": r["created_at"],
                "last_used_at": r["last_used_at"],
                "created_at_human": _human_ts(r["created_at"]),
                "last_used_at_human": (
                    _human_ts(r["last_used_at"]) if r["last_used_at"] else "—"
                ),
            }
            for r in rows
        ]
    finally:
        conn.close()


def _human_ts(ts: Optional[int]) -> str:
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts)))


# ─── Lookup & verification ───────────────────────────────────────────────


def lookup_and_verify(prefix: str, secret: str) -> Optional[dict]:
    """O(1) lookup by prefix, then constant-time hash compare.

    Returns the row dict on success, ``None`` otherwise.
    Bumps ``last_used_at`` AT MOST ONCE PER MINUTE to avoid hammering the DB
    on the hot path of a busy API consumer.
    """
    if not prefix or not secret:
        return None
    expected_hash = _hash_secret(secret)
    conn = _db()
    try:
        row = conn.execute(
            """
            SELECT id, user_id, prefix, key_hash, label, scopes, last_used_at
            FROM api_keys
            WHERE prefix = ?
            """,
            (prefix,),
        ).fetchone()
        if row is None:
            return None
        if not hmac.compare_digest(row["key_hash"], expected_hash):
            return None
        # Bump last_used_at at most once per minute
        now = int(time.time())
        if not row["last_used_at"] or (now - int(row["last_used_at"])) >= 60:
            conn.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE prefix = ?",
                (now, prefix),
            )
            conn.commit()
        import json
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "prefix": row["prefix"],
            "label": row["label"],
            "scopes": json.loads(row["scopes"] or "[]"),
        }
    finally:
        conn.close()


# ─── Rate limiting (in-memory, per-key) ──────────────────────────────────


class _TokenBucket:
    __slots__ = ("tokens", "last_refill", "last_used")

    def __init__(self, capacity: int):
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
        self.last_used = self.last_refill

    def try_consume(self, capacity: int) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        # refill linearly
        refill = elapsed * (capacity / RATE_WINDOW_SECONDS)
        self.tokens = min(capacity, self.tokens + refill)
        self.last_refill = now
        self.last_used = now
        if self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        return True


_RL_LOCK = threading.Lock()
_RL_BUCKETS: dict[str, _TokenBucket] = {}
# Order-preserving map: oldest at front. When we hit RL_MAX_BUCKETS the
# least-recently-used entry is evicted. Bounded memory regardless of how
# many unique garbage tokens an attacker sends.
_RL_BUCKETS_ORDER: dict[str, None] = {}


def _touch_bucket(prefix: str) -> None:
    """Mark a bucket as MRU by removing and re-inserting in the order map."""
    _RL_BUCKETS_ORDER.pop(prefix, None)
    _RL_BUCKETS_ORDER[prefix] = None


def _evict_oldest() -> None:
    if not _RL_BUCKETS_ORDER:
        return
    # ``next(iter(d))`` is the oldest insertion (front of dict order in Py3.7+).
    oldest = next(iter(_RL_BUCKETS_ORDER))
    _RL_BUCKETS_ORDER.pop(oldest, None)
    _RL_BUCKETS.pop(oldest, None)


def check_rate_limit(prefix: str, *, capacity: int = RATE_LIMIT) -> bool:
    """Per-key sliding-window-ish bucket. In-memory, swap for Redis in prod.

    IMPORTANT: this MUST only be called AFTER a successful auth lookup.
    Otherwise an attacker can flood the map with garbage prefixes and OOM
    the process. (See ``RL_MAX_BUCKETS`` and the FIFO eviction in
    ``_evict_oldest``.)
    """
    with _RL_LOCK:
        bucket = _RL_BUCKETS.get(prefix)
        if bucket is None:
            if len(_RL_BUCKETS) >= RL_MAX_BUCKETS:
                _evict_oldest()
            bucket = _TokenBucket(capacity)
            _RL_BUCKETS[prefix] = bucket
        ok = bucket.try_consume(capacity)
        if ok:
            _touch_bucket(prefix)
        return ok


# ─── Decorator ────────────────────────────────────────────────────────────


def _error(code: str, message: str, status: int):
    resp = jsonify({"error": code, "message": message})
    resp.status_code = status
    return resp


def require_api_key(scopes: Optional[Iterable[str]] = None):
    """Mark a route as requiring a valid API key with the given scopes.

    Usage::

        @bp.post("/upload/init")
        @require_api_key(scopes={"films:upload"})
        def upload_init(): ...

    On success, the resolved key and owner are available as::

        g.api_key   = {"id", "user_id", "prefix", "label", "scopes"}
        g.api_user  = {"id": <int>, "name": <str>}

    Order of operations matters for security:

    1. Parse header → 401 if malformed (no DB work).
    2. DB lookup + hash verify → 401 if invalid.
    3. Scope check → 403 if insufficient.
    4. Rate limit → 429 if over (AFTER auth, so unknown prefixes can never
       leak into the in-memory bucket map).
    """
    required = set(scopes or ())

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            prefix, secret = parse_authorization_header(request.headers.get(HEADER_NAME))
            if not prefix or not secret:
                return _error(
                    "missing_token",
                    "Authorization-Header mit Bearer-Token fehlt.",
                    401,
                )
            row = lookup_and_verify(prefix, secret)
            if row is None:
                return _error("invalid_api_key", "API-Schlüssel ungültig.", 401)
            if required and not required.issubset(set(row["scopes"])):
                missing = ", ".join(sorted(required - set(row["scopes"])))
                return _error(
                    "missing_scope",
                    f"Fehlender Scope: {missing}",
                    403,
                )
            if not check_rate_limit(prefix):
                return _error(
                    "rate_limited",
                    "API-Limit erreicht. Bitte später erneut versuchen.",
                    429,
                )
            g.api_key = row
            g.api_user = {"id": row["user_id"], "name": get_name(row["user_id"])}
            return fn(*args, **kwargs)
        return wrapper
    return decorator

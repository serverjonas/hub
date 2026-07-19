"""
SJN-AI — Local AI chat powered by Ollama.

A chat-with-the-server's-local-LLM module. Each user has a single,
chronological conversation history kept in a dedicated SQLite file
(``data/sjn_ai/sjn_ai.db``) so the chat module's notification table
isn't polluted with model tokens.

Failure mode
------------
At module import time we ping ``$OLLAMA_HOST/api/tags`` with a short
timeout. If the API is unreachable (connection refused, timeout,
non-200), we raise a :class:`RuntimeError` with an actionable message.
``app.py:load_modules`` catches that, prints
``[FAILED] sjn_ai -> …`` to the terminal, and keeps the rest of the
server running. That's the whole point: a broken/missing Ollama
must NOT take down the platform — it should just make SJN-AI
invisible.

Streaming
---------
``POST /sjn-ai/send`` returns ``text/event-stream``. The browser parses
SSE frames; ``{type:"delta", text:"…"}`` updates the AI bubble
token-by-token, ``{type:"done"}`` closes the stream. If the client
disconnects mid-flight, :class:`GeneratorExit` is caught so we still
persist the partial response the way a sane chat app would.

Environment variables
---------------------
* ``OLLAMA_HOST``            — base URL, default ``http://localhost:11434``
* ``OLLAMA_MODEL``           — model tag, default ``llama3.2``
* ``OLLAMA_CONTEXT_MESSAGES``— how many past messages to send as
                               context (default 20; floor 2)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

# Match the sys.path layout used by other modules so ``toolbox.*``
# resolves without re-exporting.
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
)

try:
    import requests
except ImportError:  # defensive — `requests` is in requirements.txt
    requests = None  # type: ignore[assignment]

from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    render_template,
    request,
    stream_with_context,
)

from toolbox.files import BASE_DIR
from toolbox.user import get_current_user, is_banned


# ─── Configuration ──────────────────────────────────────────────────────────
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")
try:
    _ctx = int(os.environ.get("OLLAMA_CONTEXT_MESSAGES", "20"))
except (TypeError, ValueError):
    _ctx = 20
OLLAMA_CONTEXT_MESSAGES = max(2, _ctx)

MSG_MAX_LEN = 4000

DB_DIR = os.path.join(BASE_DIR, "data", "sjn_ai")
DB_PATH = os.path.join(DB_DIR, "sjn_ai.db")


# ─── Startup ping: refuse to load if Ollama is unreachable ───────────────
# ``/api/tags`` is the canonical health endpoint for Ollama (200 OK when
# up, with the list of locally-available models). 2s timeout prevents
# the Flask dev server from hanging during boot when the host is wrong.

def _ollama_startup_ping() -> None:
    if requests is None:
        raise RuntimeError(
            "SJN-AI: Python `requests` library is not installed. "
            "Add it to requirements.txt and reinstall."
        )
    url = f"{OLLAMA_HOST}/api/tags"
    try:
        resp = requests.get(url, timeout=2.0)
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(
            f"SJN-AI: Ollama API not reachable at {OLLAMA_HOST}. "
            f"Connection refused — is `ollama serve` running? "
            f"({exc.__class__.__name__}: {exc})"
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise RuntimeError(
            f"SJN-AI: Ollama API at {OLLAMA_HOST} timed out after 2s. "
            f"Check OLLAMA_HOST or the network. ({exc.__class__.__name__}: {exc})"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            f"SJN-AI: error contacting Ollama at {OLLAMA_HOST}. "
            f"{exc.__class__.__name__}: {exc}"
        ) from exc

    if resp.status_code != 200:
        raise RuntimeError(
            f"SJN-AI: Ollama at {OLLAMA_HOST} returned HTTP {resp.status_code} "
            f"from /api/tags. Is a working Ollama server listening there?"
        )


_ollama_startup_ping()


# ─── Database init (idempotent) ────────────────────────────────────────────
def _init_db() -> None:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sjn_ai_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            role       TEXT    NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
            content    TEXT    NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sjn_ai_user
            ON sjn_ai_messages(user_id, id);
        """
    )
    conn.commit()
    conn.close()


_init_db()


# ─── Blueprint ──────────────────────────────────────────────────────────────
bp = Blueprint("sjn_ai", __name__, template_folder="templates")


# ─── Helpers ────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are SJN-AI, the local AI assistant of the serverjonas community. "
    "Each user's conversation is private and stored on the server. "
    "Answer concisely and helpfully, in the language of the user's most "
    "recent message. Stay friendly; say so when you don't know. You run "
    "locally via Ollama, so you can be candid about your infrastructure "
    "and limitations."
)


def _conn():
    # ``check_same_thread=False`` so a single connection can be touched from
    # a streaming generator thread without Werkzeug complaining. SQLite
    # itself serialises writers, so this is safe.
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def _require_login():
    user = get_current_user()
    if user is None:
        abort(401)
    return user


def _serialize_history(user_id: int):
    """JSON-ready history with timestamps for the front-end."""
    conn = _conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, role, content, created_at
        FROM sjn_ai_messages
        WHERE user_id = ?
        ORDER BY id ASC
        """,
        (user_id,),
    )
    out = [
        {
            "id": int(r["id"]),
            "role": r["role"],
            "content": r["content"],
            "time": int(r["created_at"]),
        }
        for r in cur.fetchall()
    ]
    conn.close()
    return out


def _recent_messages(user_id: int, limit: int):
    """Return the last ``limit`` messages for ``user_id`` as
    Ollama-formatted chat history (chronological).
    """
    conn = _conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, role, content
        FROM sjn_ai_messages
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    rows.reverse()
    return rows


def _save_message(user_id: int, role: str, content: str) -> int:
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sjn_ai_messages (user_id, role, content, created_at) "
        "VALUES (?, ?, ?, ?)",
        (user_id, role, content, int(time.time())),
    )
    new_id = int(cur.lastrowid or 0)
    conn.commit()
    conn.close()
    return new_id


def _sse_format(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ─── Routes ─────────────────────────────────────────────────────────────────

@bp.route("/", methods=["GET"])
def index():
    user = _require_login()
    banned, _ = is_banned(user["id"])
    return render_template(
        "sjn_ai/index.html",
        user=user["name"],
        banned=banned,
        ollama_model=OLLAMA_MODEL,
        ollama_host=OLLAMA_HOST,
    )


@bp.route("/history", methods=["GET"])
def history():
    user = _require_login()
    return jsonify(messages=_serialize_history(user["id"]))


@bp.route("/clear", methods=["POST"])
def clear():
    user = _require_login()

    # Symmetric with ``/send``: a banned user shouldn't be able to wipe
    # their AI chat history while the platform treats them as muted.
    banned, _ = is_banned(user["id"])
    if banned:
        return jsonify(ok=False, error="banned"), 403

    # ``is_json`` is True whenever the Content-Type is JSON, but ``request.json``
    # can still legitimately be ``None`` when the body is empty. Guard it.
    body = request.get_json(silent=True) if request.is_json else None
    if body is not None:
        confirmed = bool(body.get("confirm"))
    else:
        confirmed = bool(request.form.get("confirm"))
    if not confirmed:
        return jsonify(ok=False, error="confirm_required"), 400

    conn = _conn()
    conn.execute(
        "DELETE FROM sjn_ai_messages WHERE user_id = ?",
        (user["id"],),
    )
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@bp.route("/send", methods=["POST"])
def send():
    """Persist the user prompt + stream the assistant reply over SSE."""
    user = _require_login()

    banned, _ = is_banned(user["id"])
    if banned:
        # JSON, not SSE — the client expects a quick error here.
        return jsonify(ok=False, error="banned"), 403

    # ``is_json`` is True whenever the Content-Type is JSON, but
    # ``request.json`` can still be ``None`` for an empty body. Guard it.
    body = request.get_json(silent=True) if request.is_json else None
    if body is not None:
        raw = (body.get("message") or "")
    else:
        raw = (request.form.get("message") or "")
    text = (raw or "").strip()
    if not text:
        return jsonify(ok=False, error="empty"), 400
    if len(text) > MSG_MAX_LEN:
        return jsonify(ok=False, error="too_long", limit=MSG_MAX_LEN), 400

    user_msg_id = _save_message(user["id"], "user", text)

    context = _recent_messages(user["id"], OLLAMA_CONTEXT_MESSAGES)
    ollama_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in context:
        if m["role"] in ("user", "assistant"):
            ollama_messages.append({"role": m["role"], "content": m["content"]})

    def _generate():
        try:
            resp = requests.post(
                f"{OLLAMA_HOST}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": ollama_messages,
                    "stream": True,
                },
                stream=True,
                timeout=120,
            )
        except requests.exceptions.ConnectionError as exc:
            yield _sse_format({
                "type": "error",
                "code": "ollama_unreachable",
                "detail": str(exc),
            })
            return
        except requests.exceptions.RequestException as exc:
            yield _sse_format({
                "type": "error",
                "code": "ollama_error",
                "detail": str(exc),
            })
            return

        if resp.status_code != 200:
            detail = ""
            try:
                detail = (resp.text or "")[:400]
            except Exception:
                pass
            yield _sse_format({
                "type": "error",
                "code": "ollama_http",
                "status": resp.status_code,
                "detail": detail,
            })
            return

        # Tell the client we accepted the user prompt.
        yield _sse_format({"type": "user_saved", "id": user_msg_id})

        accumulated = ""
        try:
            for raw_line in resp.iter_lines(decode_unicode=True):
                if raw_line is None:
                    continue
                line = str(raw_line).strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    yield _sse_format({
                        "type": "error",
                        "code": "ollama_error",
                        "detail": str(chunk.get("error")),
                    })
                    return
                msg = chunk.get("message") or {}
                delta = msg.get("content") or ""
                if delta:
                    accumulated += delta
                    yield _sse_format({"type": "delta", "text": delta})
                if chunk.get("done"):
                    done_payload = {"type": "done"}
                    if "eval_count" in chunk and chunk["eval_count"] is not None:
                        done_payload["tokens"] = int(chunk["eval_count"])
                    yield _sse_format(done_payload)
                    break
        except (GeneratorExit, requests.exceptions.ChunkedEncodingError):
            # Client disconnected — fall through to persistence below so the
            # partial reply is still saved.
            pass
        except Exception as exc:  # noqa: BLE001
            yield _sse_format({
                "type": "error",
                "code": "stream_failed",
                "detail": str(exc),
            })
        finally:
            full_text = accumulated.strip()
            if full_text:
                try:
                    _save_message(user["id"], "assistant", full_text)
                except Exception:
                    # Persistence must NEVER take down the worker.
                    pass

    return Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

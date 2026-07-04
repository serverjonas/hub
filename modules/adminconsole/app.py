# modules/adminconsole/app.py
# ─── Admin/Mod Command Console ─────────────────────────────────────────────
#
# A pre-defined, role-gated command runner. The page looks like a terminal
# (dark panel, monospace, prompt, command history), but every command is a
# small function below — there is **no execution of arbitrary code**, neither
# Python nor shell. Every verb declares its minimum role (admin / mod /
# both); the blueprint page itself is gated to admin-or-mod, then each
# command re-checks.
#
# Security notes
# --------------
# * Every `<input>` from the user is parsed with ``shlex.split`` and
#   matched against a closed whitelist. Unknown verbs are rejected.
# * Per-command ``min_args``/``max_args`` are enforced server-side.
# * Output is plain text; the frontend renders it with ``.textContent``
#   so any HTML in user-controlled fields (usernames, reasons, messages)
#   cannot inject markup.
# * Every invocation — including parse errors, unknown commands, and
#   permission denials — is appended to ``logs/console.log`` for
#   post-hoc audit. Failures of the audit append never break the
#   user-facing command.
# * The blueprint enforces ``require_admin_or_mod`` AND each command
#   re-checks the role (defense in depth).

from __future__ import annotations

import logging
import os
import re
import shlex
import sqlite3
import sys
import threading
import time
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
from toolbox.files import DB_PATH, LOGS_DIR
from toolbox.news import create_notification
from toolbox.user import (
    get_current_user,
    get_infos,
    require_admin_or_mod,
)

bp = Blueprint("adminconsole", __name__)
log = logging.getLogger("adminconsole")

CONSOLE_LOG_PATH = os.path.join(LOGS_DIR, "console.log")
# Cap on the raw command string the front-end POSTs. Defends against
# log spam (each command is appended to console.log) and unbounded
# shlex parsing cost per request.
MAX_INPUT_LEN = 2000
ROLE_ADMIN = "admin"
ROLE_MOD = "mod"
ROLE_BOTH = "both"

# Strict whitelist for log filenames exposed via the ``logs`` command
# (mirrors ``adminlogs/app.py:_VALID_NAME``). Used together with a
# realpath-prefix check to also defeat symlink traversal, in case a
# stray symlink inside LOGS_DIR points outside the directory.
_LOG_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.log$")
_CONSOLE_LOG_LOCK = threading.Lock()


# ─── Role gate (defense-in-depth; per-command role check below) ──────────
@bp.before_request
def _role_check():
    require_admin_or_mod()


# ─── Helpers ──────────────────────────────────────────────────────────────

def _con():
    """Open the user DB with row-dict access. Caller closes."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _human_time(ts):
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "—"


def _format_duration(seconds):
    seconds = max(0, int(seconds))
    if seconds >= 86400:
        return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


def _resolve_user_id(token):
    """Returns ``(user_id, user_name)`` or ``(None, None)``.

    Accepts either a numeric id ("42") or a case-insensitive name
    ("Ada"). Names from the DB are returned as stored (preserving
    case for display).
    """
    if not token:
        return None, None
    token = token.strip()
    if not token:
        return None, None
    con = _con()
    try:
        if token.isdigit():
            row = con.execute(
                "SELECT user_id, user_name FROM users WHERE user_id = ?",
                (int(token),),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT user_id, user_name FROM users "
                "WHERE LOWER(user_name) = LOWER(?) LIMIT 1",
                (token,),
            ).fetchone()
    finally:
        con.close()
    if not row:
        return None, None
    return row["user_id"], row["user_name"]


def _audit(command_text, user_id, user_name, status):
    """Best-effort write to ``logs/console.log``. Never raises.

    Uses a module-level lock so concurrent admin tabs running through
    a threaded WSGI server cannot interleave the makedirs + open +
    write sequence. The write itself is still racy against parallel
    processes, but in this single-process Flask-WSGI deployment the
    lock is sufficient.
    """
    try:
        with _CONSOLE_LOG_LOCK:
            os.makedirs(LOGS_DIR, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            line = f"{ts} | {user_name} (id={user_id}) | {command_text!r} | {status}\n"
            with open(CONSOLE_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line)
    except OSError:
        log.warning("Could not write console audit log", exc_info=True)


def _ctx_from_request():
    user = get_current_user()
    if user is None:
        return None
    infos = get_infos(user["id"]) or {}
    return {
        "user_id": user["id"],
        "user_name": user["name"],
        "is_admin": bool(infos.get("admin")),
        "is_mod": bool(infos.get("mod")),
    }


# ─── Command handlers ───────────────────────────────────────────────────
# Each handler signature: (ctx, args) -> str  (plain text output)


def _cmd_help(ctx):
    lines = ["Available commands for your role:"]
    for verb, meta in COMMANDS.items():
        required = meta["min_role"]
        if required == ROLE_ADMIN and not ctx["is_admin"]:
            continue
        if required == ROLE_MOD and not (ctx["is_admin"] or ctx["is_mod"]):
            continue
        lines.append(f"  {verb:<22}  {meta['usage']}")
    if len(lines) == 1:
        return "No commands available for your role."
    lines.append("")
    lines.append("Tip: separate arguments with spaces. Use quotes for args containing spaces.")
    return "\n".join(lines)


def _cmd_whoami(ctx):
    roles = []
    if ctx["is_admin"]:
        roles.append("admin")
    if ctx["is_mod"]:
        roles.append("mod")
    return (
        f"id={ctx['user_id']} name={ctx['user_name']} "
        f"roles=[{', '.join(roles) or 'none'}]"
    )


def _cmd_stats(ctx):
    now = int(time.time())
    con = _con()
    try:
        user_count = con.execute(
            "SELECT COUNT(*) AS c FROM users"
        ).fetchone()["c"]
        banned = con.execute(
            "SELECT COUNT(*) AS c FROM ban "
            "WHERE expires_at IS NULL OR expires_at > ?",
            (now,),
        ).fetchone()["c"]
        active_sessions = con.execute(
            "SELECT COUNT(*) AS c FROM sessions WHERE expires_at > ?",
            (now,),
        ).fetchone()["c"]
        try:
            pending_suggestions = con.execute(
                "SELECT COUNT(*) AS c FROM permission_suggestions "
                "WHERE status = 'pending'"
            ).fetchone()["c"]
        except sqlite3.OperationalError:
            pending_suggestions = 0
    finally:
        con.close()
    return (
        f"users={user_count} bans_active={banned} "
        f"sessions_active={active_sessions} pending_suggestions={pending_suggestions}"
    )


def _cmd_userinfo(ctx, args):
    if len(args) != 1:
        return "Usage: userinfo <name|id>"
    uid, name = _resolve_user_id(args[0])
    if uid is None:
        return f"User not found: {args[0]}"
    con = _con()
    try:
        row = con.execute(
            """SELECT user_id, user_name, email, email_active,
                admin, vip, mod, bio, profile_visibility, created_at
                FROM users WHERE user_id = ?""",
            (uid,),
        ).fetchone()
        active_sessions = con.execute(
            "SELECT COUNT(*) AS c FROM sessions "
            "WHERE user_id = ? AND expires_at > ?",
            (uid, int(time.time())),
        ).fetchone()["c"]
        banned = False
        ban_reason = None
        b = con.execute(
            "SELECT reason, expires_at FROM ban WHERE user_id = ?",
            (uid,),
        ).fetchone()
        if b:
            expires = b["expires_at"]
            if expires is None or expires > int(time.time()):
                banned = True
                ban_reason = b["reason"]
    finally:
        con.close()
    lines = [
        f"id         : {row['user_id']}",
        f"name       : {row['user_name']}",
        f"email      : {row['email'] or '-'} (active={bool(row['email_active'])})",
        f"roles      : admin={bool(row['admin'])} vip={bool(row['vip'])} mod={bool(row['mod'])}",
        f"visibility : {row['profile_visibility'] or 'public'}",
        f"sessions   : {active_sessions} active",
        f"created_at : {_human_time(row['created_at'])}",
        f"banned     : {'YES (' + (ban_reason or 'no reason') + ')' if banned else 'no'}",
    ]
    return "\n".join(lines)


def _cmd_usersessions(ctx, args):
    if len(args) != 1:
        return "Usage: usersessions <name|id>"
    uid, name = _resolve_user_id(args[0])
    if uid is None:
        return f"User not found: {args[0]}"
    con = _con()
    try:
        rows = con.execute(
            "SELECT session_id, expires_at FROM sessions "
            "WHERE user_id = ? AND expires_at > ? "
            "ORDER BY expires_at DESC",
            (uid, int(time.time())),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return f"User {name} (id={uid}) has 0 active sessions."
    out = [f"Active sessions for {name} (id={uid}): {len(rows)}"]
    for r in rows:
        out.append(
            f"  sid={r['session_id'][:8]}…  "
            f"expires_in={_format_duration(int(r['expires_at']) - int(time.time()))}"
        )
    return "\n".join(out)


def _cmd_bans(ctx, args):
    if args and args[0] != "list":
        return "Usage: bans [list]"
    con = _con()
    try:
        rows = con.execute(
            "SELECT b.user_id, u.user_name, u.admin, u.vip, u.mod, "
            "b.reason, b.expires_at "
            "FROM ban b JOIN users u ON u.user_id = b.user_id "
            "WHERE b.expires_at IS NULL OR b.expires_at > ? "
            "ORDER BY b.user_id ASC",
            (int(time.time()),),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return "No active bans."
    out = [f"Active bans ({len(rows)}):"]
    out.append(
        f"  {'id':>4}  {'name':<22}  {'AVM':<3}  "
        f"{'reason':<32}  expires"
    )
    for r in rows:
        avm = (
            ("A" if r["admin"] else "-") +
            ("V" if r["vip"] else "-") +
            ("M" if r["mod"] else "-")
        )
        if r["expires_at"] is None:
            expiry = "permanent"
        else:
            rem = int(r["expires_at"]) - int(time.time())
            expiry = "in " + _format_duration(rem)
        out.append(
            f"  {r['user_id']:>4}  {r['user_name']:<22}  {avm:<3}  "
            f"{(r['reason'] or '-')[:30]:<32}  {expiry}"
        )
    return "\n".join(out)


def _cmd_ban(ctx, args):
    if len(args) < 2:
        return "Usage: ban <name|id> <reason...>"
    uid, name = _resolve_user_id(args[0])
    if uid is None:
        return f"User not found: {args[0]}"
    if uid == ctx["user_id"]:
        return "You can't ban yourself."
    reason = " ".join(args[1:]).strip()
    if not reason:
        return "Reason is required."
    if len(reason) > 500:
        reason = reason[:500]
    con = _con()
    try:
        # Replace any existing ban row for this user (consistent with
        # the existing `/admin/ban` flow).
        con.execute("DELETE FROM ban WHERE user_id = ?", (uid,))
        con.execute(
            "INSERT INTO ban (user_id, reason, expires_at, created_at) "
            "VALUES (?, ?, NULL, ?)",
            (uid, reason, int(time.time())),
        )
        con.commit()
    finally:
        con.close()
    return f"Banned user {name} (id={uid}). Reason: {reason}"


def _cmd_unban(ctx, args):
    if len(args) != 1:
        return "Usage: unban <name|id>"
    uid, name = _resolve_user_id(args[0])
    if uid is None:
        return f"User not found: {args[0]}"
    con = _con()
    try:
        cur = con.execute("DELETE FROM ban WHERE user_id = ?", (uid,))
        deleted = cur.rowcount
        con.commit()
    finally:
        con.close()
    if deleted == 0:
        return f"User {name} was not banned."
    return f"Unbanned user {name} (id={uid})."


def _cmd_setrole(ctx, args):
    """Admin only: change a user's role directly (no suggestion flow)."""
    if not ctx["is_admin"]:
        return "Permission denied. Admin role required."
    if len(args) != 3:
        return "Usage: setrole <name|id> admin|vip|mod <0|1>"
    uid, name = _resolve_user_id(args[0])
    if uid is None:
        return f"User not found: {args[0]}"
    role = args[1].lower()
    if role not in ("admin", "vip", "mod"):
        return "Role must be one of: admin, vip, mod."
    if args[2] not in ("0", "1"):
        return "Value must be 0 or 1."
    value = int(args[2])
    if uid == ctx["user_id"] and role == "admin" and value == 0:
        return "You can't revoke your own admin role."
    con = _con()
    try:
        con.execute(
            f"UPDATE users SET {role} = ? WHERE user_id = ?",
            (value, uid),
        )
        con.commit()
    finally:
        con.close()
    return f"Set {role}={value} for user {name} (id={uid})."


def _cmd_killsessions(ctx, args):
    """Admin only: force-logout another user."""
    if not ctx["is_admin"]:
        return "Permission denied. Admin role required."
    if len(args) != 1:
        return "Usage: killsessions <name|id>"
    uid, name = _resolve_user_id(args[0])
    if uid is None:
        return f"User not found: {args[0]}"
    con = _con()
    try:
        cur = con.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
        deleted = cur.rowcount
        con.commit()
    finally:
        con.close()
    return f"Killed {deleted} session(s) for user {name} (id={uid})."


def _cmd_logs(ctx, args):
    """Show last N lines of a log file. Default: 20 lines of activity.log."""
    name = args[0] if args else "activity.log"
    n = int(args[1]) if len(args) > 1 else 20
    if n < 1 or n > 1000:
        return "N must be between 1 and 1000."
    if not _LOG_NAME_RE.match(name):
        return "Invalid log file name."
    # Symlink traversal defence: resolve the candidate path and
    # confirm it stays inside LOGS_DIR even after symlink resolution.
    real_logs = os.path.realpath(LOGS_DIR)
    candidate = os.path.join(real_logs, name)
    try:
        real_path = os.path.realpath(candidate)
    except OSError as e:
        return f"Cannot resolve log path: {e}"
    if not (real_path == real_logs or real_path.startswith(real_logs + os.sep)):
        return "Invalid log file name."
    if not os.path.isfile(real_path):
        return f"Log file not found: {name}"
    try:
        with open(real_path, "rb") as f:
            data = f.read()
        # Last N lines
        chunks = data.splitlines()[-n:]
        tail = b"\n".join(chunks).decode("utf-8", errors="replace")
        return f"--- {name} (last {n} lines) ---\n{tail}"
    except OSError as e:
        return f"Cannot read log: {e}"


def _cmd_audit(ctx, args):
    """Admin only: tail the console audit log."""
    if not ctx["is_admin"]:
        return "Permission denied. Admin role required."
    n = int(args[0]) if args else 20
    if n < 1 or n > 1000:
        return "N must be between 1 and 1000."
    if not os.path.isfile(CONSOLE_LOG_PATH):
        return "Console audit log is empty (no commands have been run yet)."
    try:
        with open(CONSOLE_LOG_PATH, "rb") as f:
            data = f.read()
        chunks = data.splitlines()[-n:]
        tail = b"\n".join(chunks).decode("utf-8", errors="replace")
        return f"--- console.log (last {n} entries) ---\n{tail}"
    except OSError as e:
        return f"Cannot read audit log: {e}"


def _cmd_notifs(ctx, args):
    """List the latest notifications for a user (read-only)."""
    if len(args) != 1:
        return "Usage: notifs <name|id>"
    uid, name = _resolve_user_id(args[0])
    if uid is None:
        return f"User not found: {args[0]}"
    con = _con()
    try:
        rows = con.execute(
            "SELECT id, message, type, read, created_at "
            "FROM notifications WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT 20",
            (uid,),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return f"No notifications for {name}."
    out = [f"Notifications for {name} (id={uid}, latest 20):"]
    for r in rows:
        msg = (r["message"] or "")[:120]
        out.append(
            f"  [{_human_time(r['created_at'])}] "
            f"read={bool(r['read'])} type={r['type'] or 'system'}: {msg}"
        )
    return "\n".join(out)


def _cmd_sendnotif(ctx, args):
    """Admin only: send a notification (mirrors the notification_manager)."""
    if not ctx["is_admin"]:
        return "Permission denied. Admin role required."
    if len(args) < 2:
        return "Usage: sendnotif <name|id|all> <message...>"
    target = args[0]
    message = " ".join(args[1:]).strip()
    if not message:
        return "Message is required."
    if len(message) > 500:
        message = message[:500]
    if target == "all":
        con = _con()
        try:
            users = con.execute("SELECT user_id FROM users").fetchall()
        finally:
            con.close()
        sent = 0
        failed = 0
        for u in users:
            try:
                create_notification(
                    u["user_id"], message,
                    type="system", sender_id=ctx["user_id"],
                )
                sent += 1
            except Exception as e:
                # One bad row shouldn't fail the whole send. Log so the
                # admin can see why in the application log.
                failed += 1
                log.warning(
                    "sendnotif: failed for user_id=%s: %s",
                    u["user_id"], e,
                )
        return (
            f"Sent notification to {sent} user(s)"
            + (f"; {failed} failed (see app log)" if failed else "")
            + "."
        )
    uid, name = _resolve_user_id(target)
    if uid is None:
        return f"User not found: {target}"
    create_notification(
        uid, message, type="system", sender_id=ctx["user_id"],
    )
    return f"Sent notification to {name} (id={uid})."


def _cmd_friends(ctx, args):
    if len(args) != 1:
        return "Usage: friends <name|id>"
    uid, name = _resolve_user_id(args[0])
    if uid is None:
        return f"User not found: {args[0]}"
    con = _con()
    try:
        rows = con.execute(
            """SELECT u.user_id, u.user_name FROM users u
               JOIN friendships f ON (
                  (f.user_id = ? AND f.friend_id = u.user_id) OR
                  (f.friend_id = ? AND f.user_id = u.user_id)
               )
               WHERE f.status = 'accepted'
               ORDER BY u.user_name COLLATE NOCASE""",
            (uid, uid),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return f"User {name} has no accepted friends."
    out = [f"Friends of {name} (id={uid}): {len(rows)}"]
    for r in rows:
        out.append(f"  #{r['user_id']:>4} {r['user_name']}")
    return "\n".join(out)


def _cmd_clear(_ctx, _args):
    """Sentinal: the frontend turns this into a screen-clear."""
    return "\x1b[CLEAR]"


# ─── Verb registry ──────────────────────────────────────────────────────
COMMANDS = {
    "help":         {"min_role": ROLE_BOTH,  "function": _cmd_help,
                     "min_args": 0, "max_args": 0,
                     "usage": "help"},
    "whoami":       {"min_role": ROLE_BOTH,  "function": _cmd_whoami,
                     "min_args": 0, "max_args": 0,
                     "usage": "whoami"},
    "stats":        {"min_role": ROLE_BOTH,  "function": _cmd_stats,
                     "min_args": 0, "max_args": 0,
                     "usage": "stats"},
    "userinfo":     {"min_role": ROLE_BOTH,  "function": _cmd_userinfo,
                     "min_args": 1, "max_args": 1,
                     "usage": "userinfo <name|id>"},
    "usersessions": {"min_role": ROLE_BOTH,  "function": _cmd_usersessions,
                     "min_args": 1, "max_args": 1,
                     "usage": "usersessions <name|id>"},
    "bans":         {"min_role": ROLE_BOTH,  "function": _cmd_bans,
                     "min_args": 0, "max_args": 1,
                     "usage": "bans [list]"},
    "ban":          {"min_role": ROLE_BOTH,  "function": _cmd_ban,
                     "min_args": 2, "max_args": None,
                     "usage": "ban <name|id> <reason...>"},
    "unban":        {"min_role": ROLE_BOTH,  "function": _cmd_unban,
                     "min_args": 1, "max_args": 1,
                     "usage": "unban <name|id>"},
    "friends":      {"min_role": ROLE_BOTH,  "function": _cmd_friends,
                     "min_args": 1, "max_args": 1,
                     "usage": "friends <name|id>"},
    "logs":         {"min_role": ROLE_BOTH,  "function": _cmd_logs,
                     "min_args": 0, "max_args": 2,
                     "usage": "logs [<name.log>] [N]"},
    "notifs":       {"min_role": ROLE_BOTH,  "function": _cmd_notifs,
                     "min_args": 1, "max_args": 1,
                     "usage": "notifs <name|id>"},
    "setrole":      {"min_role": ROLE_ADMIN, "function": _cmd_setrole,
                     "min_args": 3, "max_args": 3,
                     "usage": "setrole <name|id> admin|vip|mod <0|1>"},
    "killsessions": {"min_role": ROLE_ADMIN, "function": _cmd_killsessions,
                     "min_args": 1, "max_args": 1,
                     "usage": "killsessions <name|id>"},
    "audit":        {"min_role": ROLE_ADMIN, "function": _cmd_audit,
                     "min_args": 0, "max_args": 1,
                     "usage": "audit [N]"},
    "sendnotif":    {"min_role": ROLE_ADMIN, "function": _cmd_sendnotif,
                     "min_args": 2, "max_args": None,
                     "usage": "sendnotif <name|id|all> <message...>"},
    "clear":        {"min_role": ROLE_BOTH,  "function": _cmd_clear,
                     "min_args": 0, "max_args": 0,
                     "usage": "clear"},
}


# ─── Routes ──────────────────────────────────────────────────────────────

@bp.route("/")
def index():
    user = get_current_user()
    infos = get_infos(user["id"]) or {}
    # Distinguish the prompt between admin / mod so the user knows which
    # capabilities are currently active.
    prompt = f"{'admin' if infos.get('admin') else 'mod'}@jonas:~$"
    return render_template(
        "adminconsole.html",
        user=user["name"],
        prompt=prompt,
        is_admin=bool(infos.get("admin")),
        is_mod_only=(bool(infos.get("mod")) and not bool(infos.get("admin"))),
    )


@bp.route("/api/exec", methods=["POST"])
def api_exec():
    ctx = _ctx_from_request()
    if ctx is None:
        return jsonify({"ok": False, "error": "not_logged_in"}), 401

    payload = request.get_json(silent=True) or {}
    raw = (payload.get("command") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "empty_input"}), 400
    if len(raw) > MAX_INPUT_LEN:
        return jsonify({"ok": False, "error": "input_too_long"}), 400

    # 1. Parse arguments
    try:
        parts = shlex.split(raw, posix=True)
    except ValueError as e:
        _audit(raw, ctx["user_id"], ctx["user_name"], f"parse_error: {e}")
        return jsonify({
            "ok": False,
            "error": "parse_error",
            "output": f"Cannot parse command: {e}",
            "kind": "error",
        }), 200

    if not parts:
        return jsonify({"ok": False, "error": "empty_input"}), 400

    verb = parts[0].lower()
    args = parts[1:]

    meta = COMMANDS.get(verb)
    if meta is None:
        _audit(raw, ctx["user_id"], ctx["user_name"], "unknown_command")
        return jsonify({
            "ok": False,
            "error": "unknown_command",
            "output": f"Unknown command: {verb}. Type 'help' for the list.",
            "kind": "error",
        }), 200

    # 2. Role check (defense-in-depth on top of before_request)
    required = meta["min_role"]
    allowed = True
    if required == ROLE_ADMIN and not ctx["is_admin"]:
        allowed = False
    elif required == ROLE_MOD and not (ctx["is_admin"] or ctx["is_mod"]):
        allowed = False
    if not allowed:
        _audit(raw, ctx["user_id"], ctx["user_name"], "permission_denied")
        return jsonify({
            "ok": False,
            "error": "permission_denied",
            "output": f"Permission denied. '{verb}' requires '{required}' role.",
            "kind": "error",
        }), 403

    # 3. Arg-count check
    if len(args) < meta["min_args"]:
        _audit(raw, ctx["user_id"], ctx["user_name"], "missing_args")
        return jsonify({
            "ok": False,
            "error": "missing_args",
            "output": f"Usage: {meta['usage']}",
            "kind": "error",
        }), 400
    if meta["max_args"] is not None and len(args) > meta["max_args"]:
        _audit(raw, ctx["user_id"], ctx["user_name"], "too_many_args")
        return jsonify({
            "ok": False,
            "error": "too_many_args",
            "output": f"Usage: {meta['usage']}",
            "kind": "error",
        }), 400

    # 4. Execute
    try:
        output = meta["function"](ctx, args)
    except Exception as e:
        log.exception("Console command failed: %r", raw)
        _audit(raw, ctx["user_id"], ctx["user_name"], f"internal_error: {e!r}")
        return jsonify({
            "ok": False,
            "error": "internal_error",
            "output": f"Server error: {type(e).__name__}: {e}",
            "kind": "error",
        }), 200

    # 5. Clear-screen sentinal
    if output == "\x1b[CLEAR]":
        _audit(raw, ctx["user_id"], ctx["user_name"], "ok")
        return jsonify({"ok": True, "output": "", "kind": "info", "clear": True}), 200

    # 6. Destructive actions get a "warn" tone in the UI (yellow), so
    # colour-blind users still see the textual "BAN"/"KILL" verb in the
    # output line above the colour cue.
    kind = "success"
    if verb in ("ban", "unban", "setrole", "killsessions", "sendnotif"):
        kind = "warn"

    _audit(raw, ctx["user_id"], ctx["user_name"], "ok")
    return jsonify({"ok": True, "output": output, "kind": kind}), 200

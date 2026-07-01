"""GUI for managing personal API keys.

This is the ONLY place a logged-in user can create or revoke an API key.
There is deliberately no API endpoint for key CRUD — rotating a key from
an authenticated API would mean losing the key that's authenticating the
request, which is both insecure and a usability nightmare.

Routes (registered under ``/account/api-keys``):

* ``GET  /``               – list keys + optional "show once" raw token (via session flash)
* ``POST /``               – create a new key (label + scopes)
* ``POST /<int:key_id>/delete`` – delete one of YOUR keys
"""

import json
import sqlite3
import time

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

# Import the canonical key helpers from the API v1 auth module. The GUI is
# the single consumer that drives this subsystem; Routes import from the
# same module to verify keys.
from modules.api.v1.auth import (
    generate_api_key,
    insert_api_key,
    delete_api_key,
    list_api_keys,
)
from modules.api.v1.auth import _hash_secret   # canonical hash helper
from toolbox.user import get_current_user


bp = Blueprint("api_keys_gui", __name__, template_folder="../../templates")


# ─── Helpers ─────────────────────────────────────────────────────────────


def _check_login():
    user = get_current_user()
    if user is None:
        return None, redirect(url_for("login.login"))
    return user, None


def _csrf_token() -> str:
    """Per-session CSRF token. Project has no global CSRF extension.

    Tokens are 32 bytes (256 bits) of system-random hex.
    """
    if "_api_keys_csrf" not in session:
        import secrets
        session["_api_keys_csrf"] = secrets.token_hex(32)
    return session["_api_keys_csrf"]


def _verify_csrf():
    """Return None on success, else a (code, message) tuple for the API envelope."""
    expected = session.get("_api_keys_csrf")
    provided = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
    if not expected or not provided:
        return ("missing_csrf", "CSRF-Token fehlt.")
    import hmac
    if not hmac.compare_digest(expected, provided):
        return ("bad_csrf", "CSRF-Token ist ungültig.")
    return None


def _available_scopes() -> list[dict]:
    return [
        {"value": "films:upload", "label": "Filme/Serien hochladen"},
        {"value": "films:read", "label": "Filme/Serien lesen (später)"},
    ]


# ─── Routes ──────────────────────────────────────────────────────────────


@bp.get("/")
def index():
    user, redirect_resp = _check_login()
    if redirect_resp is not None:
        return redirect_resp

    keys = list_api_keys(user["id"])
    scopes = _available_scopes()

    # Note: We deliberately use session pop; the raw token is shown exactly
    # once after creation and then vanishes.
    raw_token = session.pop("_api_keys_new_raw", None)
    new_meta = session.pop("_api_keys_new_meta", None)

    return render_template(
        "account_api_keys.html",
        user={"id": user["id"], "name": user["name"]},
        keys=keys,
        scopes=scopes,
        csrf_token=_csrf_token(),
        raw_token=raw_token,
        new_meta=new_meta,
        now=int(time.time()),
    )


@bp.post("/")
def create():
    user, redirect_resp = _check_login()
    if redirect_resp is not None:
        return redirect_resp
    csrf_err = _verify_csrf()
    if csrf_err:
        flash(csrf_err[1], "error")
        return redirect(url_for("api_keys_gui.index"))

    label = (request.form.get("label") or "").strip()
    label = label[:80]
    if not label:
        flash("Bitte einen Namen für den Schlüssel angeben.", "error")
        return redirect(url_for("api_keys_gui.index"))

    requested_scopes = request.form.getlist("scopes")
    # Whitelist server-side; never trust the client list.
    valid_scope_values = {s["value"] for s in _available_scopes()}
    granted = [s for s in requested_scopes if s in valid_scope_values]
    if not granted:
        granted = ["films:upload"]   # sensible default for the first module

    raw_token, prefix, secret = generate_api_key()
    key_hash = _hash_secret(secret)

    insert_api_key(
        user_id=user["id"],
        prefix=prefix,
        key_hash=key_hash,
        label=label,
        scopes=granted,
        created_at=int(time.time()),
    )

    # Stash for one-time display; never persisted.
    session["_api_keys_new_raw"] = raw_token
    session["_api_keys_new_meta"] = {
        "label": label,
        "scopes": granted,
        "created_at": int(time.time()),
    }
    return redirect(url_for("api_keys_gui.index"))


@bp.post("/<int:key_id>/delete")
def delete(key_id: int):
    user, redirect_resp = _check_login()
    if redirect_resp is not None:
        return redirect_resp
    csrf_err = _verify_csrf()
    if csrf_err:
        flash(csrf_err[1], "error")
        return redirect(url_for("api_keys_gui.index"))

    deleted = delete_api_key(user_id=user["id"], key_id=key_id)
    if deleted:
        flash("API-Schlüssel gelöscht.", "success")
    else:
        flash("API-Schlüssel konnte nicht gelöscht werden.", "error")
    return redirect(url_for("api_keys_gui.index"))

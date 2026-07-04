import re
import os

from flask import (
    Blueprint,
    abort,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from toolbox.email import (
    build_verify_url,
    send_email,
    verification_email_body,
    verification_email_body_html,
)
from toolbox.user import (
    consume_email_verification,
    create_email_verification,
    email_resend_cooldown_remaining,
    get_current_user,
    get_user_email_status,
    is_email_verified,
    mark_user_email_active,
    mask_email,
    record_email_sent,
    set_user_email,
)


bp = Blueprint("hub", __name__)


EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
GATE_SKIP_PATHS = {"/hub/email", "/hub/email/resend", "/hub/email/verify"}


# ─── Flash-Mechanismus (eigene Session-Liste) ────────────────────────────────


def _push_flash(kind, key, vars_=None):
    """Hängt eine Flash-Nachricht an die Session. Lokalisierung passiert clientseitig
    über data-i18n + data-i18n-vars (siehe static/i18n.js).

    Wird ohne `SECRET_KEY` in `.env` zu einem No-Op; in dem Fall werden Flashes
    einfach verworfen, damit die Seite nicht crasht.
    """
    try:
        flashes = session.get("_email_gate_flashes", [])
        flashes.append({"kind": kind, "key": key, "vars": vars_ or {}})
        session["_email_gate_flashes"] = flashes
    except RuntimeError:
        # Session ist nicht verfügbar (kein secret_key gesetzt).
        pass


def _pop_flashes():
    """Liest und entfernt pending Flashes. Liefert [] wenn keine Session vorhanden."""
    try:
        return session.pop("_email_gate_flashes", [])
    except RuntimeError:
        return []


def _login_required():
    user = get_current_user()
    if user is None:
        # rendert templates/not_logged_in.html über den app.py-Handler
        abort(401)
    return user


def _send_verification(email_addr, username, token):
    """Kapselt den msmtp-Versand für die Verifizierungs-Mail.

    Versendet eine ``multipart/alternative``-Mail mit HTML als bevorzugter
    Darstellung und Plain-Text als Fallback.
    """
    verify_url = build_verify_url(token)
    return send_email(
        to_address=email_addr,
        subject="Bestätige deine E-Mail – serverjonas",
        text_body=verification_email_body(
            username,
            verify_url,
            ttl_hours=24,
        ),
        html_body=verification_email_body_html(
            username,
            verify_url,
            ttl_hours=24,
        ),
    )


# ─── Gate ──────────────────────────────────────────────────────────────────


@bp.before_request
def gate_unverified_email():
    """Lenkt nicht-verifizierte User (nur bei GET) auf /hub/email um.

    Wenn $DEBUG_NO_EMAIL=1 gesetzt ist (von `python app.py --debug`), wird der
    Gate komplett übersprungen — sonst müsste man in Dev den Verify-Link, den
    msmtp nie versendet hat, manuell aus den Logs fummeln.
    """
    if os.environ.get("DEBUG_NO_EMAIL", "").strip().lower() in ("1", "true", "yes"):
        return  # Debug-Modus: keine E-Mail-Sperre.

    if request.method != "GET":
        return

    if request.path in GATE_SKIP_PATHS:
        return

    if request.path.startswith("/hub/email/"):
        return

    user = get_current_user()
    if user is None:
        return  # Login-Check übernimmt app.py

    if not is_email_verified(user["id"]):
        return redirect(url_for("hub.email_setup"))


@bp.route("/")
def hub():
    user_array = get_current_user()
    user = user_array["name"] if user_array else None
    # Auth state is exposed to the JS engine via data-auth on <body>; set
    # here so render_template can thread it through.
    return render_template(
        "hub.html",
        user=user,
        user_id=(user_array or {}).get("id"),
        flashes=_pop_flashes(),
    )


@bp.route("/customize")
def customize():
    """Hub customization page: pick widgets, position them, configure them."""
    user_array = get_current_user()
    user = user_array["name"] if user_array else None
    return render_template(
        "hub_customizer.html",
        user=user,
        user_id=(user_array or {}).get("id"),
    )


# ─── E-Mail-Setup & Verifizierung ────────────────────────────────────────────


@bp.route("/email", methods=["GET", "POST"])
def email_setup():
    user = _login_required()

    email_value, active = get_user_email_status(user["id"])
    verified = is_email_verified(user["id"])
    cooldown = email_resend_cooldown_remaining(user["id"])
    show_pending = bool(email_value) and not verified

    if request.method == "POST":
        action = request.form.get("action", "submit_email")

        if action == "clear":
            set_user_email(user["id"], None)
            return redirect(url_for("hub.email_setup"))

        if verified:
            # Bereits verifiziert – kein erneuter Versand über dieses Formular.
            return redirect(url_for("hub.email_setup"))

        new_email = (request.form.get("email") or "").strip().lower()

        if not EMAIL_REGEX.fullmatch(new_email):
            _push_flash("error", "email_gate.error.invalid")
            return redirect(url_for("hub.email_setup"))

        if cooldown > 0:
            _push_flash("error", "email_gate.error.cooldown", {"seconds": cooldown})
            return redirect(url_for("hub.email_setup"))

        set_user_email(user["id"], new_email)
        token, _expires = create_email_verification(user["id"], new_email)

        ok, err = _send_verification(new_email, user["name"], token)
        if ok:
            record_email_sent(user["id"])
            _push_flash("success", "email_gate.success.sent", {"email": new_email})
        else:
            # Roll back das offene Token, damit der User es ohne Cooldown erneut
            # versuchen kann.
            consume_email_verification(token)
            _push_flash("error", "email_gate.error.send_failed",
                        {"detail": err or ""})

        return redirect(url_for("hub.email_setup"))

    return render_template(
        "email_gate.html",
        user=user["name"],
        email=email_value,
        masked_email=mask_email(email_value) if email_value else "",
        verified=verified,
        cooldown_left=cooldown,
        show_pending=show_pending,
        flashes=_pop_flashes(),
    )


@bp.route("/email/resend", methods=["POST"])
def email_resend():
    user = _login_required()
    email_value, _ = get_user_email_status(user["id"])

    if is_email_verified(user["id"]) or not email_value:
        return redirect(url_for("hub.email_setup"))

    cooldown = email_resend_cooldown_remaining(user["id"])
    if cooldown > 0:
        _push_flash("error", "email_gate.error.cooldown", {"seconds": cooldown})
        return redirect(url_for("hub.email_setup"))

    token, _ = create_email_verification(user["id"], email_value)
    ok, err = _send_verification(email_value, user["name"], token)
    if ok:
        record_email_sent(user["id"])
        _push_flash("success", "email_gate.success.resent", {"email": email_value})
    else:
        consume_email_verification(token)
        _push_flash("error", "email_gate.error.send_failed",
                    {"detail": err or ""})

    return redirect(url_for("hub.email_setup"))


@bp.route("/email/verify", methods=["GET"])
def email_verify():
    user = _login_required()
    token = (request.args.get("token") or "").strip()

    if not token:
        _push_flash("error", "email_gate.error.token_invalid")
        return redirect(url_for("hub.email_setup"))

    result = consume_email_verification(token)
    if not result:
        _push_flash("error", "email_gate.error.token_invalid")
        return redirect(url_for("hub.email_setup"))

    user_id, verified_email = result
    if user_id != user["id"]:
        _push_flash("error", "email_gate.error.wrong_user")
        return redirect(url_for("hub.email_setup"))

    mark_user_email_active(user_id)
    _push_flash("success", "email_gate.success.verified", {"email": verified_email})
    return redirect(url_for("hub.hub"))

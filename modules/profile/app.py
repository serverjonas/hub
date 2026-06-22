"""Profile module — public profiles, avatars, bio + visibility.

Routes:
    GET  /user/<username>            View a profile (visibility-aware)
    GET  /profile/avatar/<int:uid>  Serve the user's uploaded avatar (visibility-aware)
    GET  /profile/avatar-fallback/<seed>  Deterministic SVG fallback (no auth)
    POST /profile/bio               Update own bio
    POST /profile/visibility        Update own profile visibility
    POST /profile/avatar            Multipart upload (own avatar only)
    POST /profile/avatar/clear      Remove own avatar
    POST /settings/change-password  Change own password

Provides Jinja helpers used everywhere:
    * ``avatar_url(user_id, avatar_path)``  → URL string
    * ``avatar_img(user_id, avatar_path, name, size)`` → full <img> HTML
    * ``char_initials(name)``               → "JN" for "jonas"
"""
from __future__ import annotations

import os
import re
import sys
import html

from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from toolbox.files import DATA_DIR
from toolbox.user import (
    BIO_MAX_LEN,
    PROFILE_VIS_FRIENDS,
    PROFILE_VIS_PRIVATE,
    PROFILE_VIS_PUBLIC,
    VALID_PROFILE_VIS,
    are_friends,
    change_password,
    get_current_user,
    get_mutual_friends,
    get_user_by_name,
    get_user_profile,
    is_admin_or_mod,
    kill_other_sessions,
    update_user_profile,
)
from toolbox.avatar import (
    ALLOWED_AVATAR_EXT,
    AVATAR_DIR,
    AVATAR_MAX_BYTES,
    AvatarUploadError,
    avatar_path_for,
    process_avatar_upload,
    write_fallback_svg,
)


bp = Blueprint("profile", __name__,
               template_folder="templates",
               static_folder="templates")


# ─── Jinja Globals (für ALLE Templates) ────────────────────────────────────


from datetime import datetime as _dt


def avatar_href(user_id, avatar_path):
    """URL string. Always resolves to something (avatar file OR fallback)."""
    if not user_id:
        user_id = 0
    if avatar_path:
        return f"/profile/avatar/{int(user_id)}"
    return f"/profile/avatar-fallback/u{int(user_id)}"


def avatar_img(user_id, avatar_path, name="", size="md"):
    """Inline ``<img>`` HTML mit passender Klasse. ``size`` ∈
    {xs=24, sm=32, md=42, lg=96, xl=160}.

    Server-rendered, damit Layout-Shifts vermieden werden (feste Größen).
    """
    if not user_id:
        user_id = 0
    src = avatar_href(user_id, avatar_path)
    alt = html.escape(name or "")
    safe_alt = alt or "avatar"
    # Inline style für size ohne zusätzliche CSS-Klassen:
    size_px = {"xs": 24, "sm": 32, "md": 42, "lg": 96, "xl": 160}.get(size, 42)
    radius = size_px  # voll rund für xs/sm/md, weicher für lg/xl
    if size in ("lg", "xl"):
        radius = max(12, size_px // 6)
    return (
        f'<img class="sjin-avatar sjin-avatar-{size}" '
        f'src="{src}" alt="{safe_alt}" loading="lazy" '
        f'decoding="async" referrerpolicy="no-referrer" '
        f'style="width:{size_px}px; height:{size_px}px; '
        f'border-radius:{radius}px; object-fit:cover;">'
    )


def char_initials(name):
    n = (name or "").strip()
    if not n:
        return "?"
    parts = [p for p in re.split(r"[\s_-]+", n) if p]
    if len(parts) >= 2:
        return (parts[0][:1] + parts[1][:1]).upper()
    return n[:2].upper()


def datetime_ymd(ts):
    try:
        return _dt.fromtimestamp(int(ts)).strftime("%d.%m.%Y")
    except (TypeError, ValueError, OSError):
        return ""


def are_friends_check(a_id, b_id):
    """Template-side check: dünner Wrapper um toolbox.user.are_friends."""
    try:
        return are_friends(int(a_id), int(b_id))
    except (TypeError, ValueError):
        return False


bp.add_app_template_global(avatar_href, "avatar_href")
bp.add_app_template_global(avatar_img, "avatar_img")
bp.add_app_template_global(char_initials, "char_initials")
bp.add_app_template_global(are_friends_check, "are_friends_check")
bp.add_app_template_filter(datetime_ymd, "datetime_ymd")


# ─── Session flash helpers (Flask-Session → session cookie) ────────────────


def _flash(kind, key, vars_=None):
    """Optional flash via Flask session; tolerates missing SECRET_KEY."""
    try:
        flashes = session.get("_profile_flashes", [])
        flashes.append({"kind": kind, "key": key, "vars": vars_ or {}})
        session["_profile_flashes"] = flashes
    except RuntimeError:
        pass


def _pop_flashes():
    try:
        return session.pop("_profile_flashes", [])
    except RuntimeError:
        return []


# ─── Helpers ────────────────────────────────────────────────────────────────


def _viewer_dict():
    """Liefert den eingeloggten Viewer als Dict oder ``None``."""
    cu = get_current_user()
    if not cu:
        return None
    return {"id": cu["id"], "name": cu["name"],
            "admin": is_admin_or_mod(cu["id"])}


def _can_view(viewer, profile):
    """Visibility-Check. ``viewer`` ist Dict oder None.

    Regeln:
      * public    → alle (auch anonym)
      * friends   → self + akzeptierte Freunde + admin/mod
      * private   → self + admin/mod
    """
    if not profile:
        return False
    if profile["profile_visibility"] == PROFILE_VIS_PUBLIC:
        return True
    if not viewer:
        return False
    if viewer["id"] == profile["id"]:
        return True
    if viewer.get("admin"):
        return True
    if profile["profile_visibility"] == PROFILE_VIS_FRIENDS:
        return are_friends(viewer["id"], profile["id"])
    return False  # private, viewer != owner and not admin


def _profile_view_mode(viewer, profile):
    """Liefert ``"self"`` / ``"full"`` / ``"limited"`` für die Anzeige."""
    if viewer and viewer["id"] == profile["id"]:
        return "self"
    if _can_view(viewer, profile):
        return "full"
    return "limited"


# ─── /user/<username> ───────────────────────────────────────────────────────


@bp.route("/user/<username>", methods=["GET"])
def view(username):
    profile = get_user_by_name(username)
    if not profile:
        abort(404)
    viewer = _viewer_dict()
    if not _can_view(viewer, profile):
        abort(404)  # Profil nicht auffindbar → keine Info-Leaks.

    mode = _profile_view_mode(viewer, profile)

    # Mutual friends nur für self + accepted friends.
    mutual = []
    if viewer and profile["id"] != viewer["id"]:
        if are_friends(viewer["id"], profile["id"]):
            mutual = get_mutual_friends(viewer["id"], profile["id"])
    elif viewer and profile["id"] == viewer["id"]:
        mutual = []  # eigene Freundesliste braucht's nicht in mock-profil

    return render_template(
        "profile/view.html",
        profile=profile,
        viewer=viewer,
        view_mode=mode,
        mutual=mutual,
        flashes=_pop_flashes(),
    )


# ─── Avatar serving ────────────────────────────────────────────────────────


@bp.route("/profile/avatar-fallback/<seed>", methods=["GET"])
def avatar_fallback(seed):
    """Deterministischer SVG-Avatar (kein Auth-Check).

    Wird vom ``<img>`` immer aufgerufen, falls kein eigener Avatar vorhanden
    ist. Da diese SVGs immer sicher sind (kein User-Content), brauchen wir
    hier keinen Visibility-Check.
    """
    # Seed kann sowohl ein user_id ("u42") als auch ein expliziter String sein.
    seed_s = (seed or "").strip()
    label = ""
    if seed_s.startswith("u") and seed_s[1:].isdigit():
        # User-Fallback; zeige Initial aus dem Namen, wenn vorhanden, sonst "?"
        try:
            uid = int(seed_s[1:])
            prof = get_user_profile(uid)
            if prof:
                label = char_initials(prof["name"])
        except Exception:
            label = "?"
    body = write_fallback_svg(seed_s or "u0", label)
    return Response(
        body,
        mimetype="image/svg+xml",
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
        },
    )


@bp.route("/profile/avatar/<int:user_id>", methods=["GET"])
def avatar(user_id):
    """Liefert den User-Avatar (oder Fallback), Visibility-aware."""
    profile = get_user_profile(user_id)
    if not profile:
        # Anonymer Fallback statt 404 — UI bleibt stabil.
        return Response(
            write_fallback_svg(f"u{user_id}", "?"),
            mimetype="image/svg+xml",
        )
    viewer = _viewer_dict()
    if not _can_view(viewer, profile):
        # Strikte Variante: lieber Fallback als 403, damit Listen-Layouts
        # nicht "kaputt" aussehen, wenn der User kein Recht hat.
        return Response(
            write_fallback_svg(f"u{user_id}", "?"),
            mimetype="image/svg+xml",
        )

    full = avatar_path_for(user_id, profile.get("avatar_path"))
    if not full:
        # Kein Avatar → initials-SVG mit Namen.
        return Response(
            write_fallback_svg(f"u{user_id}", char_initials(profile["name"])),
            mimetype="image/svg+xml",
            headers={"Cache-Control": "public, max-age=300"},
        )

    # ETag aus mtime → 304-Sparen.
    try:
        mtime = int(os.path.getmtime(full))
    except OSError:
        mtime = 0
    etag = f'W/"avatar-{user_id}-{mtime}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304)

    resp = send_from_directory(
        AVATAR_DIR,
        os.path.basename(full),
        conditional=True,
    )
    resp.headers["Cache-Control"] = "public, max-age=3600, must-revalidate"
    resp.headers["ETag"] = etag
    # send_from_directory setzt Content-Type automatisch anhand der Extension.
    return resp


# ─── Profile updates (eigenes Profil) ───────────────────────────────────────


def _require_self(user_id):
    viewer = _viewer_dict()
    if viewer is None:
        abort(401)
    if viewer["id"] != user_id:
        abort(403)
    return viewer


@bp.route("/profile/bio", methods=["POST"])
def update_bio():
    cu = get_current_user()
    if cu is None:
        abort(401)
    viewer_id = cu["id"]
    bio = request.form.get("bio", "")
    err = update_user_profile(viewer_id, bio=bio)
    if err:
        _flash("error", "profile.error.bio_too_long", {"max": BIO_MAX_LEN})
    else:
        _flash("success", "profile.saved.bio")
    return redirect(request.form.get("next") or url_for("profile.view",
                                                        username=cu["name"]))


@bp.route("/profile/visibility", methods=["POST"])
def update_visibility():
    cu = get_current_user()
    if cu is None:
        abort(401)
    visibility = request.form.get("visibility", PROFILE_VIS_PUBLIC)
    err = update_user_profile(cu["id"], visibility=visibility)
    if err:
        _flash("error", "profile.error.visibility_invalid")
    else:
        _flash("success", "profile.saved.visibility",
               {"value": visibility})
    return redirect(request.form.get("next") or url_for("profile.view",
                                                        username=cu["name"]))


@bp.route("/profile/avatar", methods=["POST"])
def update_avatar():
    cu = get_current_user()
    if cu is None:
        abort(401)
    viewer_id = cu["id"]

    # 10 MB hard cap (auch für Rohbytes vor Pillow).
    request.max_content_length = AVATAR_MAX_BYTES + 1024

    f = request.files.get("avatar")
    if not f:
        _flash("error", "profile.error.no_file")
        return redirect(request.form.get("next") or url_for("profile.view",
                                                            username=cu["name"]))

    try:
        new_path = process_avatar_upload(f, viewer_id)
    except AvatarUploadError as exc:
        _flash("error", "profile.error.avatar", {"detail": str(exc)})
        return redirect(request.form.get("next") or url_for("profile.view",
                                                            username=cu["name"]))
    except Exception as exc:  # noqa: BLE001 — letzte Verteidigungslinie
        _flash("error", "profile.error.avatar",
               {"detail": f"Unerwarteter Fehler: {exc}"})
        return redirect(request.form.get("next") or url_for("profile.view",
                                                            username=cu["name"]))

    err = update_user_profile(viewer_id, avatar_path=new_path)
    if err:
        _flash("error", "profile.error.avatar", {"detail": err})
    else:
        _flash("success", "profile.saved.avatar")
    return redirect(request.form.get("next") or url_for("profile.view",
                                                        username=cu["name"]))


@bp.route("/profile/avatar/clear", methods=["POST"])
def clear_avatar():
    cu = get_current_user()
    if cu is None:
        abort(401)
    # Auch die Datei(en) auf der Platte löschen.
    for ext in ALLOWED_AVATAR_EXT:
        path = os.path.join(AVATAR_DIR, f"{cu['id']}{ext}")
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
    update_user_profile(cu["id"], avatar_clear=True)
    _flash("success", "profile.saved.avatar_cleared")
    return redirect(request.form.get("next") or url_for("profile.view",
                                                        username=cu["name"]))


# ─── Passwort ändern ─────────────────────────────────────────────────────────


# Mapping von Validierungsfehler → i18n Key
PWD_ERR_KEYS = {
    "current_or_new_missing": "settings.toast.pw_fill_all",
    "user_missing":           "settings.error.unknown",
    "current_wrong":          "settings.toast.pw_current_wrong",
    "pw_same":                "settings.toast.pw_same",
    "pw_short":               "settings.toast.pw_short",
    "pw_no_digit":            "settings.toast.pw_no_digit",
    "pw_common":              "settings.toast.pw_common",
}


@bp.route("/settings/change-password", methods=["POST"])
def change_password_route():
    """AJAX-style: liest FormData, antwortet JSON.

    Wird von der Settings-UI sowohl per Form-POST (Fallback) als auch per
    fetch (AJAX) aufgerufen. Browser-AJAX prüft ``Accept: application/json``.
    """
    cu = get_current_user()
    if cu is None:
        if request.accept_mimetypes.best == "application/json":
            return jsonify(ok=False, error="not_logged_in"), 401
        abort(401)
    viewer_id = cu["id"]

    cur_pw = request.form.get("current_password", "")
    new_pw = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")

    # confirm muss passen, BEVOR wir den aktuellen Hash anfassen.
    if new_pw and confirm and new_pw != confirm:
        if request.accept_mimetypes.best == "application/json":
            return jsonify(ok=False, error_code="pw_mismatch",
                           message_key="settings.toast.pw_mismatch"), 400
        _flash("error", "settings.toast.pw_mismatch")
        return redirect("/settings#security")

    err = change_password(viewer_id, cur_pw, new_pw)
    if err:
        i18n_key = PWD_ERR_KEYS.get(err, "settings.error.unknown")
        if request.accept_mimetypes.best == "application/json":
            return jsonify(ok=False, error_code=err, message_key=i18n_key), 400
        _flash("error", i18n_key)
        return redirect("/settings#security")

    # Andere Sessions des Users abmelden (aktuelle bleibt erhalten).
    current_session_id = request.cookies.get("session_id")
    killed = kill_other_sessions(viewer_id, current_session_id)

    if request.accept_mimetypes.best == "application/json":
        return jsonify(ok=True, killed_other_sessions=killed)

    _flash("success", "settings.toast.pw_changed",
           {"sessions": killed})
    return redirect("/settings#security")

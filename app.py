# main app.py
import argparse
import importlib.util
import json
import os
import sqlite3
import time
from datetime import datetime
from urllib.parse import quote

import tomllib
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
)
from toolbox.files import BASE_DIR, get_git_info
from toolbox.i18n import build_translation_manifest
from toolbox.user import (
    get_current_user,
    get_infos,
    get_lang,
    is_banned,
    is_user_active,
)

load_dotenv()

TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
MODULE_DIR = os.path.join(BASE_DIR, "modules")
DB_PATH = os.path.join(BASE_DIR, "users.db")
ACTIVITY_LOG = os.path.join(BASE_DIR, "logs", "activity.log")


app = Flask(
    __name__,
    template_folder=TEMPLATES_DIR,
    static_folder=os.path.join(BASE_DIR, "static"),
)

app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024 * 1024  # 50GB
app.secret_key = os.environ.get("SECRET_KEY")
ALLOWED_EXTENSIONS = {".html", ".css", ".js", ".png", ".jpg", ".ico", ".svg", ".txt"}

emergency_mode = False

# ─── Translation version manifest ─────────────────────────────────────────────
# Computed ONCE at startup. The browser compares the version returned by
# ``/i18n/manifest.json`` against the version it cached in ``localStorage`` for
# each translation file and only refetches when they differ.
TRANSLATION_VERSIONS = build_translation_manifest()
print(
    f"\033[92m[  OK  ]\033[0m i18n manifest \033[90m-> {len(TRANSLATION_VERSIONS)} "
    f"files hashed\033[0m"
)


@app.route("/i18n/manifest.json")
def i18n_manifest():
    """Version manifest for every translation file.

    Registered explicitly so Werkzeug's URL routing prefers this rule
    over the catch-all ``/<path:path>`` below (static path segments win
    over parameter captures regardless of registration order).

    ``Cache-Control: no-cache`` makes browsers re-validate on each load
    (cheap, the response is tiny) so the client always sees the current
    server-side hash and can decide whether a translation needs to be
    refetched.
    """
    resp = jsonify(TRANSLATION_VERSIONS)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


def load_modules():
    config_path = os.path.join(BASE_DIR, "modules.toml")

    if not os.path.isfile(config_path):
        print("\033[91m[FAILED]\033[0m \033[90mmodules.toml fehlt\033[0m")
        return

    with open(config_path, "rb") as f:
        modules = tomllib.load(f)

    for name, cfg in modules.items():
        try:
            if not cfg["active"]:
                continue

            module_path = os.path.join(MODULE_DIR, cfg["pfad"], "app.py")

            spec = importlib.util.spec_from_file_location(
                f"modules.{name}", module_path
            )

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            app.register_blueprint(module.bp, url_prefix=cfg["url"])

            print(f"\033[92m[  OK  ]\033[0m {name} \033[90m-> {cfg['url']}\033[0m")

        except Exception as e:
            print(f"\033[91m[FAILED]\033[0m {name} \033[90m-> {e}\033[0m")



@app.before_request
def check_ban():
    g.start_time = time.time()
    if request.path.startswith("/ban") or request.path.startswith("/static"):
        return

    user = get_current_user()
    if user is None:
        return

    banned, reason = is_banned(user["id"])
    if banned:
        reason_encoded = reason if reason else "Kein Grund angegeben"
        return redirect(f"/ban?reason={quote(reason_encoded)}")

    if not is_user_active(user["id"]):
        return render_template("activation_pending.html")


@app.context_processor
def inject_git_info():
    """Expose the latest commit hash + subject to every template.

    Returned as ``git_version`` (short hash) and ``git_message`` (commit
    subject). Both are ``None`` when ``BASE_DIR`` is not a git repo or git
    is unavailable — templates can decide whether to render placeholders.
    """
    info = get_git_info()
    if info:
        return {"git_version": info["version"], "git_message": info["message"]}
    return {"git_version": None, "git_message": None}


@app.context_processor
def inject_notifications():
    user = get_current_user()
    if user is None:
        return {
            "unread_count": 0,
            "user_id": None,
            "roles": {"admin": 0, "vip": 0, "mod": 0},
        }

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read = 0",
        (user["id"],),
    )
    count = cur.fetchone()[0]
    conn.close()

    return {
        "unread_count": count,
        "user_id": user["id"],
        "roles": get_infos(user["id"]),
    }


@app.after_request
def log_activity(response):
    duration = time.time() - g.start_time

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip and "," in ip:
        ip = ip.split(",")[0].strip()

    line = (
        f"{timestamp}; {ip}; {request.path}; {response.status_code}; {duration:.3f}s\n"
    )

    with open(ACTIVITY_LOG, "a") as f:
        f.write(line)

    return response


@app.errorhandler(404)
def page_not_found(e):
    user_array = get_current_user()
    user = user_array["name"] if user_array else None
    return render_template("404.html", user=user), 404


@app.errorhandler(500)
def internal_error(e):
    user_array = get_current_user()
    user = user_array["name"] if user_array else None
    return render_template("500.html", user=user), 500


@app.errorhandler(501)
def internal_error(e):
    user_array = get_current_user()
    user = user_array["name"] if user_array else None
    return render_template("501.html", user=user), 500


@app.errorhandler(403)
def forbidden(e):
    user_array = get_current_user()
    user = user_array["name"] if user_array else None
    return render_template("403.html", user=user), 403


@app.errorhandler(401)
def not_logged_in(e):
    user_array = get_current_user()
    user = user_array["name"] if user_array else None
    return render_template("not_logged_in.html", user=user), 401


@app.errorhandler(418)
def tea(e):
    return render_template("418.html"), 418


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def hub(path):
    user = get_current_user()

    if emergency_mode:
        return abort(500)

    if path == "":
        return redirect("/hub")

    if path == "make_me_a_coffee":
        abort(418)


    # NUR static files erlauben
    ext = os.path.splitext(path)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return abort(404)

    file_path = os.path.join(BASE_DIR, path)
    if os.path.isfile(file_path):
        return send_from_directory(BASE_DIR, path)

    return abort(404)


# ─── Server host/port (override via env) ──────────────────────────────────────
# Default: 0.0.0.0:5000. Examples:
#   PORT=3000 python3 app.py --debug
#   python3 app.py                   # → :5000
#   PORT=8080 python3 app.py         # → :8080
DEFAULT_HOST = os.environ.get("HOST", "0.0.0.0")
DEFAULT_PORT = int(os.environ.get("PORT", "5000"))


def RunServerDebug():
    app.run(host=DEFAULT_HOST, port=DEFAULT_PORT, debug=True)


def RunServerRelease():
    app.run(host=DEFAULT_HOST, port=DEFAULT_PORT, debug=False)


# ─── CLI parsing ──────────────────────────────────────────────────────────────────
# `python app.py --debug` startet Flask im Debug-Modus UND deaktiviert alle
# externen Side-Effects (E-Mail-Versand via msmtp, …). Praktisch für lokale
# Entwicklung, in der ein echter SMTP-Relay meist nicht verfügbar ist.
#
# Das Modul toolbox/email.py liest das Flag über os.environ.get("DEBUG_NO_EMAIL")
# und überspringt den Versand. So bleibt die Verifizierungs-Flow (Eingabe einer
# E-Mail-Adresse → verify-URL) in dev/test lauffähig, ohne tatsächlich Mails zu
# versenden.
DEBUG_NO_EMAIL_ENV = "DEBUG_NO_EMAIL"


def _parse_cli_args():
    parser = argparse.ArgumentParser(
        prog="app.py",
        description="serverjonas hub — Flask entry point.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Startet Flask im Debug-Modus und deaktiviert externe Side-Effects "
            "(E-Mail-Versand via msmtp etc.)."
        ),
    )
    return parser.parse_args()


def _apply_debug_mode():
    """Mark debug mode for the email subsystem and the rest of the app."""
    os.environ[DEBUG_NO_EMAIL_ENV] = "1"
    print(
        "\033[33m[DEBUG ]\033[0m \033[90mDebug-Modus aktiv\033[0m\n"
        f"  \033[90m• Flask reload + Tracebacks aktiv\033[0m\n"
        f"  \033[90m• E-Mail-Versand deaktiviert "
        f"(env ${DEBUG_NO_EMAIL_ENV} = 1)\033[0m\n"
        f"  \033[90m• msmtp wird nicht aufgerufen — Mails werden nur lokal "
        "geloggt\033[0m"
    )


@app.route("/masteradmin")
def index():
    return render_template("masteradmin.html")


try:
    load_modules()
except Exception as e:
    print(str(e))
    emergency_mode = True

if __name__ == "__main__":

    args = _parse_cli_args()
    if args.debug:
        _apply_debug_mode()
        RunServerDebug()
    else:
        RunServerRelease()

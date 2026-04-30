# main app.py
import importlib.util
import json
import os
import sqlite3
import time
from urllib.parse import quote

from dotenv import load_dotenv
from flask import Flask, abort, redirect, render_template, request, send_from_directory

from toolbox import get_current_user, is_banned, is_user_active

load_dotenv()

BASE_DIR = os.path.dirname(__file__)
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
MODULE_DIR = os.path.join(BASE_DIR, "modules")
DB_PATH = os.path.join(BASE_DIR, "users.db")

app = Flask(
    __name__,
    template_folder=TEMPLATES_DIR,
    static_folder=os.path.join(BASE_DIR, "static"),
)

app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024 * 1024  # 50GB
app.secret_key = os.environ.get("SECRET_KEY")
ALLOWED_EXTENSIONS = {".html", ".css", ".js", ".png", ".jpg", ".ico", ".svg", ".txt"}


def load_modules():
    config_path = os.path.join(BASE_DIR, "modules.json")
    if not os.path.isfile(config_path):
        print("❌ modules.json fehlt")
        return

    with open(config_path) as f:
        modules = json.load(f)

    for name, cfg in modules.items():
        try:
            module_path = os.path.join(MODULE_DIR, cfg["pfad"], "app.py")
            spec = importlib.util.spec_from_file_location(
                f"modules.{name}", module_path
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            app.register_blueprint(module.bp, url_prefix=cfg["url"])
            print(f"✅ Modul geladen: {name} -> {cfg['url']}")
        except Exception as e:
            print(f"❌ Modul {name} konnte nicht geladen werden:", e)


load_modules()


@app.before_request
def check_ban():
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


@app.errorhandler(404)
def page_not_found(e):
    user_array = get_current_user()
    user = user_array["name"] if user_array else None
    return render_template("404.html", user=user), 404


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
    if path == "":
        return redirect("/hub")

    if path == "make_me_a_coffee":
        abort(418)
        
    if path == "settings":
        user = get_current_user()
        return render_template("settings.html", user=user["name"] if user else None)

    # NUR static files erlauben
    ext = os.path.splitext(path)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return abort(404)

    file_path = os.path.join(BASE_DIR, path)
    if os.path.isfile(file_path):
        return send_from_directory(BASE_DIR, path)

    return abort(404)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

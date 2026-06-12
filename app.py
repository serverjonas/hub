# main app.py
import importlib.util
import json
import os
import sqlite3
import time
from urllib.parse import quote
import tomllib
from dotenv import load_dotenv
from flask import Flask, abort, redirect, render_template, request, send_from_directory,  g
from datetime import datetime
from toolbox.files import BASE_DIR
from toolbox.user import get_current_user, is_banned, is_user_active, get_lang, get_infos

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
                f"modules.{name}",
                module_path
            )

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            app.register_blueprint(
                module.bp,
                url_prefix=cfg["url"]
            )

            print(
                f"\033[92m[  OK  ]\033[0m "
                f"{name} "
                f"\033[90m-> {cfg['url']}\033[0m"
            )

        except Exception as e:
            print(
                f"\033[91m[FAILED]\033[0m "
                f"{name} "
                f"\033[90m-> {e}\033[0m"
            )


try:
    load_modules()
except Exception as e:
    print(str(e))
    emergency_mode = True

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
def inject_notifications():
    user = get_current_user()
    if user is None:
        return {"unread_count": 0, "roles":{"admin":0, "vip":0, "mod":0}}
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read = 0",
        (user["id"],)
    )
    count = cur.fetchone()[0]
    conn.close()
    
    return {"unread_count": count, "roles":get_infos(user["id"])}

@app.after_request
def log_activity(response):
    duration = time.time() - g.start_time

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip and "," in ip:
        ip = ip.split(",")[0].strip()

    line = f"{timestamp}; {ip}; {request.path}; {response.status_code}; {duration:.3f}s\n"

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

    #if path == "settings":
    #    return render_template(f"settings.html", user=user["name"] if user else None)

    # NUR static files erlauben
    ext = os.path.splitext(path)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return abort(404)

    file_path = os.path.join(BASE_DIR, path)
    if os.path.isfile(file_path):
        return send_from_directory(BASE_DIR, path)

    return abort(404)


def RunServerDebug():
    app.run(host="0.0.0.0", port=5000, debug=True)


def RunServerRelease():
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    RunServerRelease()

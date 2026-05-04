from flask import Blueprint, jsonify, request, abort
import psutil
import os
import time

bp = Blueprint("monitor", __name__)

# 🔐 optional einfacher API key
API_KEY = os.environ.get("MONITOR_API_KEY", "changeme")


def check_auth():
    key = request.headers.get("X-API-KEY")
    if key != API_KEY:
        abort(403)


@bp.route("/", methods=["GET"])
def get_status():
    check_auth()

    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    uptime = time.time() - psutil.boot_time()

    # optional GPU (nur wenn vorhanden + nvidia-smi installiert)
    gpu = None
    try:
        import subprocess
        result = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"]
        )
        gpu = int(result.decode().strip().split("\n")[0])
    except:
        gpu = None

    return jsonify({
        "status": "ok",
        "cpu": cpu,
        "ram": ram,
        "disk": disk,
        "gpu": gpu,
        "uptime": int(uptime)
    })


@bp.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "alive"})

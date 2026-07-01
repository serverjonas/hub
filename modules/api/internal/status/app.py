"""Internal server-status endpoint (relocated out of /api/v1/).

Auth strategy:
* If ``INTERNAL_STATUS_KEY`` is set in the environment, callers must send a
  matching ``X-Internal-Key`` header.
* Otherwise the endpoint falls back to a session-based admin/mod check via
  ``toolbox.user`` so the previous behaviour (``/api/v1/status`` readable
  with a global ``MONITOR_API_KEY`` env var) doesn't silently break
  installations that had it pinned.
"""

import os
import time

import psutil  # type: ignore
from flask import Blueprint, abort, jsonify, request

from toolbox.user import get_current_user, is_admin_or_mod


bp = Blueprint("internal_status", __name__)

# Optional shared-secret from environment.
INTERNAL_KEY = os.environ.get("INTERNAL_STATUS_KEY")


def check_auth():
    if INTERNAL_KEY:
        provided = request.headers.get("X-Internal-Key", "")
        if provided != INTERNAL_KEY:
            # Fall through to session check below.
            pass
        else:
            return

    user = get_current_user()
    if user is None or not is_admin_or_mod(user["id"]):
        abort(403)


@bp.get("/")
def status():
    check_auth()

    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory().percent
    try:
        disk = psutil.disk_usage("/").percent
    except Exception:
        disk = None
    uptime = int(time.time() - psutil.boot_time())

    gpu = None
    try:
        import subprocess
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=utilization.gpu",
             "--format=csv,noheader,nounits"],
            timeout=2,
        )
        gpu = int(out.decode().strip().split("\n")[0])
    except Exception:
        gpu = None

    return jsonify({
        "status": "ok",
        "cpu": cpu,
        "ram": ram,
        "disk": disk,
        "gpu": gpu,
        "uptime": uptime,
    })


@bp.get("/ping")
def ping():
    return jsonify({"status": "alive"})

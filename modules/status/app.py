from flask import Blueprint, jsonify

bp = Blueprint("status", __name__)

@bp.route("/")
def status_home():
    return jsonify({
        "status": "online",
        "server": "serverjonas",
        "linux": "better"
    })

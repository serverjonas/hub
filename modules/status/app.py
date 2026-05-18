from flask import Blueprint, jsonify, render_template

bp = Blueprint("status", __name__)

@bp.route("/")
def status_home():
    return render_template("status.html")

@bp.route("/json")
def status_json():
    return jsonify({
        "status": "online",
        "server": "serverjonas",
        "linux": "better"
    })

from flask import Blueprint, jsonify
from toolbox.toolbox import get_notifications
from toolbox.user import get_current_user, get_name

bp = Blueprint("internal_api_news", __name__)

@bp.route("/")
def get_notifications_route():
    user = get_current_user()  # liest session cookie intern aus

    if not user or not user["id"]:
        return jsonify({"error": "unauthorized"}), 401

    user_id = user["id"]

    rows = get_notifications(user_id)

    notifications = []

    for row in rows:
        if row["read"] == 0:
            notifications.append({
                "id": row["id"],
                "sender_id": row["sender"],
                "sender_name" : get_name(row["sender"]) if row["sender"] else "System",
                "type": row["type"],
                "message": row["message"],
                "created_at": row["created_at"]
            })

    return jsonify({"notifications": notifications})
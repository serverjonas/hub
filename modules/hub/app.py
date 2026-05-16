from flask import Blueprint, render_template, request, redirect
from toolbox.user import get_current_user

bp = Blueprint("hub", __name__)

@bp.route("/")
def hub():
    user_array = get_current_user()
    user = user_array["name"] if user_array else None
    return render_template("hub.html", user=user)
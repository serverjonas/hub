from flask import Blueprint, request, render_template
 
bp = Blueprint("ban", __name__, template_folder="templates")
 
@bp.route("/")
def ban_page():
    reason = request.args.get("reason", "Kein Grund angegeben")
    return render_template("ban.html", reason=reason), 403

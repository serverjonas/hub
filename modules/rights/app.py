from flask import Blueprint, render_template, abort

bp = Blueprint("rights", __name__, template_folder="templates")

DOCUMENTS = {
    "datenschutz": {
        "title": "Datenschutzerklärung",
        "icon": "🔒",
        "subtitle": "Informationspflichten gemäß Art. 13, 14 DSGVO",
    },
    "nutzungsbedingungen": {
        "title": "Nutzungsbedingungen",
        "icon": "📋",
        "subtitle": "Allgemeine Nutzungsbedingungen (ANB)",
    },
}

@bp.route("/")
def index():
    return render_template("rights/index.html", documents=DOCUMENTS)

@bp.route("/<doc_name>")
def document(doc_name):
    if doc_name not in DOCUMENTS:
        abort(404)
    doc = DOCUMENTS[doc_name]
    return render_template(f"rights/{doc_name}.html", doc=doc, documents=DOCUMENTS)

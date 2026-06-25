from flask import Blueprint, render_template

bp = Blueprint("design", __name__)


# Vordefinierte Liste der verfügbaren Designs. Wird in der Picker-Template gerendert.
DESIGNS = [
    {
        "id": "light",
        "name_key": "design.light.name",
        "default_name": "Pastel Warm",
        "tagline_key": "design.light.tagline",
        "default_tagline": "Hand-drawn pencil & warm paper",
        "family": "hand-drawn",
        "preview": {
            "bg": "#fdfbf7",
            "fg": "#2d2d2d",
            "accent": "#ff4d4d",
            "shape": "wobbly",
        },
    },
    {
        "id": "dark",
        "name_key": "design.dark.name",
        "default_name": "Chalkboard",
        "tagline_key": "design.dark.tagline",
        "default_tagline": "Dark surface, white chalk lines",
        "family": "hand-drawn",
        "preview": {
            "bg": "#2a2826",
            "fg": "#fdfbf7",
            "accent": "#ff6b6b",
            "shape": "wobbly",
        },
    },
    {
        "id": "oled",
        "name_key": "design.oled.name",
        "default_name": "OLED Black",
        "tagline_key": "design.oled.tagline",
        "default_tagline": "Pure black, max contrast",
        "family": "hand-drawn",
        "preview": {
            "bg": "#000000",
            "fg": "#fdfbf7",
            "accent": "#ff6b6b",
            "shape": "wobbly",
        },
    },
    {
        "id": "paper",
        "name_key": "design.paper.name",
        "default_name": "Aged Paper",
        "tagline_key": "design.paper.tagline",
        "default_tagline": "Warm sepia, new-book-pages",
        "family": "hand-drawn",
        "preview": {
            "bg": "#f0e8d4",
            "fg": "#2a2418",
            "accent": "#c23535",
            "shape": "wobbly",
        },
    },
    {
        "id": "neuromorph",
        "name_key": "design.neuromorph.name",
        "default_name": "Soft UI (Neumorphism)",
        "tagline_key": "design.neuromorph.tagline",
        "default_tagline": "Cool clay, raised shadows, no borders",
        "family": "neumorph",
        "preview": {
            "bg": "#E0E5EC",
            "fg": "#3D4852",
            "accent": "#6C63FF",
            "shape": "round",
        },
    },
]


@bp.route("/", methods=["GET"])
def picker():
    """Hauptseite des Design-Pickers: zeigt alle verfügbaren Stile als Karten
    mit Preview-Schwämmchen + Beschreibung + Auswählen-Button.

    Wird über die Navbar / Settings / First-Visit-Banner erreicht.
    """
    from toolbox.user import get_current_user
    user_data = get_current_user()
    user = user_data["name"] if user_data else None
    return render_template(
        "design.html",
        user=user,
        designs=DESIGNS,
    )

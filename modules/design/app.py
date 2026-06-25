from flask import Blueprint, render_template

bp = Blueprint("design", __name__)


# ─── Design families ─────────────────────────────────────────────────────────────
#  serverjonas ships 4 families:
#   1. hand-drawn       — wobbly pencils, warm paper            (light + dark)
#   2. soft_ui          — cool clay, soft opposing shadows      (light + dark)
#   3. nineties_retro   — Windows 95 bevels, MS Sans Serif      (light + dark)
#   4. synthwave        — pure OLED black + neon glow + scans   (dark only)
#
#  Theme IDs:
#    paper · paper-dark · neuromorph · neuromorph-dark
#    win95-silver · win95-amber · synthwave
# ─────────────────────────────────────────────────────────────────────────────

DESIGN_FAMILIES = {
    "hand-drawn": {
        "id": "hand-drawn",
        "label_key": "design.family.hand_drawn.label",
        "tagline_key": "design.family.hand_drawn.tagline",
        "default_label": "Hand-Drawn",
        "default_tagline": "Wobbly pencils, warm paper, hard offset shadows.",
    },
    "soft_ui": {
        "id": "soft_ui",
        "label_key": "design.family.soft_ui.label",
        "tagline_key": "design.family.soft_ui.tagline",
        "default_label": "Soft UI (Neumorphism)",
        "default_tagline": "Cool clay, soft opposing shadows, no borders.",
    },
    "nineties_retro": {
        "id": "nineties_retro",
        "label_key": "design.family.nineties_retro.label",
        "tagline_key": "design.family.nineties_retro.tagline",
        "default_label": "90s Retro Nostalgia",
        "default_tagline": "Windows 95 bevels, MS Sans Serif, CRT-monitor look.",
    },
    "synthwave":    {
        "id": "synthwave",
        "family": "synthwave",
        "family_id": "synthwave",
        "mode": "dark",
        "name_key": "design.oled_standard.name",
        "default_name": "OLED Standard",
        "tagline_key": "design.oled_standard.tagline",
        "default_tagline": "Same as every other Dark Mode — clean, distraction-free OLED web.",
        "icon": "terminal",
        "preview": {
            "bg": "#0f1216",
            "fg": "#e7e9ee",
            "accent": "#4ea3ff",
            "shape": "oled",
        },
    },
}


# ─── Designs ─────────────────────────────────────────────────────────────────────
DESIGNS = [
    # ── Hand-Drawn family ──────────────────────────────────────────────
    {
        "id": "paper",
        "family": "hand-drawn",
        "family_id": "hand-drawn",
        "mode": "light",
        "name_key": "design.paper.name",
        "default_name": "Warm Paper",
        "tagline_key": "design.paper.tagline",
        "default_tagline": "Bright hand-drawn surface with classic Kalam marker.",
        "icon": "sun",
        "preview": {
            "bg": "#fdfbf7",
            "fg": "#2d2d2d",
            "accent": "#ff4d4d",
            "shape": "wobbly",
        },
    },
    {
        "id": "paper-dark",
        "family": "hand-drawn",
        "family_id": "hand-drawn",
        "mode": "dark",
        "name_key": "design.oled_standard.name",
        "default_name": "OLED Standard",
        "tagline_key": "design.oled_standard.tagline",
        "default_tagline": "Same as every other Dark Mode — clean, distraction-free OLED web.",
        "icon": "moon",
        "preview": {
            "bg": "#0f1216",
            "fg": "#e7e9ee",
            "accent": "#4ea3ff",
            "shape": "oled",
        },
    },
    # ── Soft UI / Neumorphism family ──────────────────────────────────
    {
        "id": "neuromorph",
        "family": "soft_ui",
        "family_id": "soft_ui",
        "mode": "light",
        "name_key": "design.neuromorph.name",
        "default_name": "Soft UI Light",
        "tagline_key": "design.neuromorph.tagline",
        "default_tagline": "Cool clay, raised cards, friendly violet CTAs.",
        "icon": "circle",
        "preview": {
            "bg": "#E0E5EC",
            "fg": "#3D4852",
            "accent": "#6C63FF",
            "shape": "round",
        },
    },
    {
        "id": "neuromorph-dark",
        "family": "soft_ui",
        "family_id": "soft_ui",
        "mode": "dark",
        "name_key": "design.oled_standard.name",
        "default_name": "OLED Standard",
        "tagline_key": "design.oled_standard.tagline",
        "default_tagline": "Same as every other Dark Mode — clean, distraction-free OLED web.",
        "icon": "moon",
        "preview": {
            "bg": "#0f1216",
            "fg": "#e7e9ee",
            "accent": "#4ea3ff",
            "shape": "oled",
        },
    },
    # ── 90s Retro / Windows 95 family ──────────────────────────────────
    {
        "id": "win95-silver",
        "family": "nineties_retro",
        "family_id": "nineties_retro",
        "mode": "light",
        "name_key": "design.win95_silver.name",
        "default_name": "Windows 95 Silver",
        "tagline_key": "design.win95_silver.tagline",
        "default_tagline": "Classic 90s look with bevel buttons and navy title bars.",
        "icon": "monitor",
        "preview": {
            "bg": "#c0c0c0",
            "fg": "#000000",
            "accent": "#0000ff",
            "shape": "bevel",
        },
    },
    {
        "id": "win95-amber",
        "family": "nineties_retro",
        "family_id": "nineties_retro",
        "mode": "dark",
        "name_key": "design.oled_standard.name",
        "default_name": "OLED Standard",
        "tagline_key": "design.oled_standard.tagline",
        "default_tagline": "Same as every other Dark Mode — clean, distraction-free OLED web.",
        "icon": "moon",
        "preview": {
            "bg": "#0f1216",
            "fg": "#e7e9ee",
            "accent": "#4ea3ff",
            "shape": "oled",
        },
    },
    # ── Synthwave OLED family (1 dark design only) ─────────────────────
    {
        "id": "synthwave",
        "family": "synthwave",
        "family_id": "synthwave",
        "mode": "dark",
        "name_key": "design.oled_standard.name",
        "default_name": "OLED Standard",
        "tagline_key": "design.oled_standard.tagline",
        "default_tagline": "Same as every other Dark Mode — clean, distraction-free OLED web.",
        "icon": "moon",
        "preview": {
            "bg": "#0f1216",
            "fg": "#e7e9ee",
            "accent": "#4ea3ff",
            "shape": "oled",
        },
    },
]


@bp.route("/", methods=["GET"])
def picker():
    """Hauptseite des Design-Pickers: zeigt alle verfügbaren Designs
    in 4 Familien gruppiert (Hand-Drawn + Soft UI + 90s Retro + Synthwave).
    Wird über die Navbar / Settings / First-Visit-Redirect erreicht.
    """
    from toolbox.user import get_current_user
    user_data = get_current_user()
    user = user_data["name"] if user_data else None

    # Group DESIGNS by family for the template.
    # Insertion-order of DESIGN_FAMILIES dict is preserved on Python 3.7+.
    families = []
    for fam_id, fam_meta in DESIGN_FAMILIES.items():
        fam_designs = [d for d in DESIGNS if d["family_id"] == fam_id]
        families.append({**fam_meta, "designs": fam_designs})

    return render_template(
        "design.html",
        user=user,
        designs=DESIGNS,
        families=families,
    )

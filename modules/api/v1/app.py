"""Master blueprint for API v1.

All v1 routes are registered under the ``/api/v1`` URL prefix. New modules
should live under ``modules/api/v1/routes/<name>.py`` and be imported +
registered here so all sub-modules share a single host blueprint and
inherit the same error handlers and JSON response format.

Error envelope (stable, machine-readable)::

    { "error": "<snake_case_code>", "message": "<human readable>" }

Never ``abort()`` directly in sub-routes — return the envelope with the
appropriate status so clients can rely on a single shape.
"""

from flask import Blueprint, jsonify

# Build the host blueprint. Sub-routes will be mounted under additional
# prefixes (e.g. ``/films``, ``/news``).
bp = Blueprint("api_v1", __name__)


# ─── Root: discovery / version info ──────────────────────────────────────


@bp.get("/")
def root():
    return jsonify({
        "name": "serverjonas API",
        "version": "v1",
        "endpoints": [
            "GET  /api/v1/ping",
            "POST /api/v1/films/upload/init",
            "POST /api/v1/films/upload/chunk",
            "POST /api/v1/films/upload/finish",
        ],
        "auth": {
            "scheme": "Bearer",
            "header": "Authorization",
            "scopes": ["films:upload", "films:read"],
        },
    })


@bp.get("/ping")
def ping():
    return jsonify({"status": "ok"})


# ─── Register sub-blueprints ─────────────────────────────────────────────

# Importing them here (rather than via app.py) keeps module loading order
# local: sub-blueprints are guaranteed loaded before ``bp`` any route is
# hit for the first time.

def _register_subblueprints(host: Blueprint) -> None:
    # Local import to avoid importing sub-blueprints at module load time,
    # which would otherwise force every ``from modules.api.v1.app import bp``
    # to fail if a sub-blueprint has a typo.
    from modules.api.v1.routes import films as films_routes
    host.register_blueprint(films_routes.bp, url_prefix="/films")


_register_subblueprints(bp)


# ─── Uniform JSON error envelope ─────────────────────────────────────────
# ``app.register_blueprint(bp)`` only attaches the bp to a Flask app. These
# error handlers are attached AFTER any blueprint is registered, so they
# fire for the WHOLE v1 surface (root + sub-modules) thanks to Flask
# blueprint-scoped error handlers installed by the loader in app.py.

@bp.errorhandler(400)
def _bad_request(e):
    return jsonify({"error": "bad_request", "message": str(e.description)}), 400


@bp.errorhandler(401)
def _unauthorized(e):
    return jsonify({"error": "unauthorized", "message": str(e.description)}), 401


@bp.errorhandler(403)
def _forbidden(e):
    return jsonify({"error": "forbidden", "message": str(e.description)}), 403


@bp.errorhandler(404)
def _not_found(e):
    return jsonify({"error": "not_found", "message": str(e.description)}), 404


@bp.errorhandler(405)
def _method_not_allowed(e):
    return jsonify({"error": "method_not_allowed", "message": str(e.description)}), 405


@bp.errorhandler(413)
def _too_large(e):
    return jsonify({"error": "payload_too_large", "message": str(e.description)}), 413


@bp.errorhandler(429)
def _too_many(e):
    return jsonify({"error": "rate_limited", "message": str(e.description)}), 429


@bp.errorhandler(500)
def _server_error(e):
    from flask import current_app
    current_app.logger.exception("Unhandled error in API v1")
    # Don't leak traceback strings into messages.
    return jsonify({
        "error": "server_error",
        "message": "Etwas ist schiefgelaufen. Bitte erneut versuchen.",
    }), 500


# Make `bp` importable both as ``modules.api.v1.app.bp`` and via re-export.
__all__ = ["bp"]

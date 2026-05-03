# serverjonas/hub

Core repository of the serverjonas network.

This project is the central backend system that provides routing, module loading, authentication hooks, and web interface handling for connected services.

---

## Overview

This application is a Flask-based web backend that acts as a modular system host.

It provides:

- Dynamic module loading from `modules/`
- Central routing system (`app.py`)
- User state handling via `toolbox.py`
- Template rendering system (`templates/`)
- Static file serving (`static/`)
- Access control and ban handling
- Custom HTTP error pages

---

## Architecture

### Entry Point

`app.py` is the main application file. It initializes:

- Flask application instance
- Environment configuration
- Module loader
- Request hooks
- Error handlers
- Routing logic

---

## Module System

Modules are defined in `modules.json` and loaded dynamically at runtime.

Each module:

- Must expose a Flask Blueprint named `bp`
- Is loaded from `modules/<path>/app.py`
- Is registered with a URL prefix defined in configuration


Example `modules.json` entry:

```json
{
  "example": {
    "pfad": "example",
    "url": "/example"
  }
}

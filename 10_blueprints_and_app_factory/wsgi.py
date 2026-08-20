"""
Day 10 — WSGI entry point.

This is what the ``flask`` CLI and a production server (gunicorn, Day 20) load.
It stays deliberately tiny: its only job is to call the factory.

    flask --app 10_blueprints_and_app_factory/wsgi.py run
    gunicorn "10_blueprints_and_app_factory.wsgi:app"

The CLI can also discover ``create_app`` on its own, so
``flask --app inventory run`` works when the package is importable.
"""

from __future__ import annotations

import os

from inventory import create_app

# One instance for the server to serve. Note the config is chosen at RUN time
# from the environment, not hardcoded — the same file boots dev and production.
app = create_app(os.environ.get("FLASK_CONFIG", "development"))

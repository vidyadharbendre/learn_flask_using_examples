"""
Day 20 — WSGI entry point.

This is what gunicorn imports. Note there is **no** ``app.run()`` call here:
gunicorn imports this module and calls ``app`` itself, so a ``run()`` at import
time would either be ignored or start a second, unwanted server.
"""

from __future__ import annotations

from shipit import create_app

app = create_app()

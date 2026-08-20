"""Day 13 — WSGI entry point."""

from __future__ import annotations

import os

from portal import create_app

app = create_app(os.environ.get("FLASK_CONFIG", "development"))

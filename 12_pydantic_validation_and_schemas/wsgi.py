"""Day 12 — WSGI entry point."""

from __future__ import annotations

import os

from catalogue import create_app

app = create_app(os.environ.get("FLASK_CONFIG", "development"))

"""Day 11 — WSGI entry point. Its only job is to call the factory."""

from __future__ import annotations

import os

from bookstore import create_app

app = create_app(os.environ.get("FLASK_CONFIG", "development"))

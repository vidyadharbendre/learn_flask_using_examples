"""
Day 10 — Extension objects, created without an app (see Day 08).

Every extension is instantiated bare here and bound inside ``create_app()``.
That is what allows several application instances — one per test, say — to
coexist in a single process.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect


class Base(DeclarativeBase):
    """Declarative base shared by every model."""


db = SQLAlchemy(model_class=Base)
migrate = Migrate()
csrf = CSRFProtect()

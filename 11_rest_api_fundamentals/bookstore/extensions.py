"""Day 11 — Extension objects (Day 08/10 pattern)."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

from flask_sqlalchemy import SQLAlchemy


class Base(DeclarativeBase):
    """Declarative base for every model."""


db = SQLAlchemy(model_class=Base)

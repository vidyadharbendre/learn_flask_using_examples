"""Day 12 — Extension objects."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

from flask_sqlalchemy import SQLAlchemy


class Base(DeclarativeBase):
    """Declarative base for every model."""


db = SQLAlchemy(model_class=Base)

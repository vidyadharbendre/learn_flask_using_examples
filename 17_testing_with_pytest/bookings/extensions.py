"""Day 17 — Extension objects."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

from flask_sqlalchemy import SQLAlchemy


class Base(DeclarativeBase):
    """Declarative base shared by every model."""


db = SQLAlchemy(model_class=Base)

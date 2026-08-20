"""
Day 09 — Extensions: ``db`` and ``migrate``, both deferred (see Day 08).

Flask-Migrate needs *both* the app and the ``SQLAlchemy`` object, which is why
it is initialised with two arguments in :mod:`app`::

    migrate.init_app(app, db)
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy


class Base(DeclarativeBase):
    """Declarative base shared by every model in this application."""


db = SQLAlchemy(model_class=Base)

# Migrate wires Alembic into the `flask` CLI, giving you `flask db …`.
# It reads your models' metadata to work out what changed.
migrate = Migrate()

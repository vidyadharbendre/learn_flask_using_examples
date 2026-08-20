"""
Day 13 — Extensions, including the login manager.

:class:`~flask_login.LoginManager` is configured in the factory, not here: the
messages and endpoint names it needs are application decisions, and this module
must stay importable by :mod:`portal.models` without a cycle (Day 08).
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect


class Base(DeclarativeBase):
    """Declarative base shared by every model."""


db = SQLAlchemy(model_class=Base)
csrf = CSRFProtect()
login_manager = LoginManager()

"""Day 14 — Extension objects, all deferred (Days 08, 10, 13)."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect


class Base(DeclarativeBase):
    """Declarative base shared by every model."""


db = SQLAlchemy(model_class=Base)
migrate = Migrate()
csrf = CSRFProtect()
login_manager = LoginManager()

"""Day 21 — Extension objects, all deferred (Days 08, 10)."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
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
cache = Cache()
limiter = Limiter(key_func=get_remote_address)

"""Day 14 — Configuration classes (Day 10 pattern)."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    """Settings shared by every environment."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-for-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'taskman.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 256 * 1024

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    REMEMBER_COOKIE_HTTPONLY = True

    TASKS_PER_PAGE = 10

    @staticmethod
    def init_app(app: object) -> None:
        """Hook for environment-specific set-up."""


class DevelopmentConfig(Config):
    """Local development."""

    DEBUG = True
    SQLALCHEMY_ECHO = os.environ.get("SQL_ECHO", "").lower() in {"1", "true"}


class TestingConfig(Config):
    """Automated tests: isolated and fast."""

    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    SECRET_KEY = "testing-key"

    # scrypt is deliberately slow (Day 13) — excellent for security, painful in
    # a test suite that creates users constantly. Werkzeug lets us pick a
    # cheaper method for TESTS ONLY. This is a legitimate, scoped trade-off:
    # never let this value leak into any other config.
    PASSWORD_HASH_METHOD = "pbkdf2:sha256:1000"


class ProductionConfig(Config):
    """Production: strict and fail-fast."""

    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"

    @staticmethod
    def init_app(app: object) -> None:
        """Refuse to boot without a real secret key.

        Args:
            app: The application being configured.

        Raises:
            RuntimeError: when ``SECRET_KEY`` is unset.
        """
        if not os.environ.get("SECRET_KEY"):
            raise RuntimeError("SECRET_KEY must be set in the environment.")


config_by_name: dict[str, type[Config]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}

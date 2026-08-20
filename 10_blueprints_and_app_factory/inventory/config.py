"""
Day 10 — Configuration: one class per environment.
==================================================

The problem
-----------
Days 01-09 configured the app inline::

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only")

That works until you need three different answers: development wants a debug
toolbar and a local SQLite file, tests want an in-memory database and CSRF
disabled, production wants a secret key that is a hard requirement.

The pattern
-----------
One base class of shared settings, one subclass per environment, selected by
name at start-up. Flask reads any UPPERCASE class attribute via
``app.config.from_object(...)``; lowercase names are ignored, which is a handy
way to keep helpers off the config object.

Day 18 replaces these classes with ``pydantic-settings``, which validates types
and fails loudly on a missing variable. Understand the plain version first.
"""

from __future__ import annotations

import os
from pathlib import Path

# Project root = the directory containing the `inventory` package.
BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    """Settings shared by every environment.

    Attributes:
        SECRET_KEY: Signs sessions and CSRF tokens.
        SQLALCHEMY_DATABASE_URI: Database connection URL.
        SQLALCHEMY_TRACK_MODIFICATIONS: Disabled; adds overhead, no benefit.
        MAX_CONTENT_LENGTH: Request-body ceiling.
        SESSION_COOKIE_HTTPONLY / SAMESITE: Day 06 cookie hardening.
        ITEMS_PER_PAGE: Application-level setting, not a Flask one — the config
            object is the right home for your own knobs too.
    """

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-for-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'inventory.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 256 * 1024
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    ITEMS_PER_PAGE = 10

    @staticmethod
    def init_app(app: object) -> None:
        """Hook for environment-specific set-up beyond plain values.

        Args:
            app: The Flask application being configured.

        Note:
            Config classes hold *values*. Anything that needs to *run* — adding
            a log handler, registering a proxy fix — goes here, where each
            subclass can override it. See :meth:`ProductionConfig.init_app`.
        """


class DevelopmentConfig(Config):
    """Local development: verbose, forgiving, insecure by design."""

    DEBUG = True
    SQLALCHEMY_ECHO = os.environ.get("SQL_ECHO", "").lower() in {"1", "true"}
    # Fine on http://127.0.0.1; MUST be True in production.
    SESSION_COOKIE_SECURE = False


class TestingConfig(Config):
    """Automated tests: fast, isolated, deterministic."""

    TESTING = True

    # An in-memory database is created fresh per connection and vanishes
    # afterwards. Tests that share a file-backed database leak state into each
    # other and fail depending on execution order — the worst kind of flake.
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

    # Disable CSRF so tests can POST without scraping a token out of the HTML.
    # This is safe ONLY because it is scoped to the testing config — which is
    # precisely the argument for config classes over `if app.debug` checks.
    WTF_CSRF_ENABLED = False

    SECRET_KEY = "testing-key"


class ProductionConfig(Config):
    """Production: strict, secure, fails fast on misconfiguration."""

    DEBUG = False
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"

    @staticmethod
    def init_app(app: object) -> None:
        """Refuse to start with a development secret key.

        Args:
            app: The Flask application being configured.

        Raises:
            RuntimeError: when ``SECRET_KEY`` was never set in the environment.

        Note:
            **Fail loudly at start-up, not silently at runtime.** A production
            app running on the default dev key means every session cookie in
            the world can be forged, and nothing in the logs would tell you.
            Crashing on boot is the kind, correct behaviour.
        """
        if not os.environ.get("SECRET_KEY"):
            raise RuntimeError(
                "SECRET_KEY must be set in the environment for production."
            )


# Selected by name in create_app(). A plain dict keeps the factory simple and
# makes the valid choices discoverable.
config_by_name: dict[str, type[Config]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}

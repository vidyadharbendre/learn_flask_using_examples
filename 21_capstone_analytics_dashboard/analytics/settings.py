"""Day 21 — Typed settings (Day 18)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application configuration, validated at start-up.

    Attributes:
        env: Deployment environment.
        debug: Whether to enable the debugger.
        secret_key: Signs sessions and CSRF tokens.
        database_url: SQLAlchemy connection URL.
        log_level / log_format: Logging configuration.
        cache_type: Flask-Caching backend.
        rate_limit_storage: Flask-Limiter storage URI.
        responses_per_minute: Public submission limit.
        items_per_page: Pagination size.
    """

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_prefix="APP_",
        case_sensitive=False, extra="ignore",
    )

    env: Literal["development", "testing", "production"] = "development"
    debug: bool = False
    secret_key: str = "dev-only-not-for-production"
    database_url: str = f"sqlite:///{BASE_DIR / 'instance' / 'analytics.db'}"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"

    cache_type: str = "SimpleCache"
    rate_limit_storage: str = "memory://"
    responses_per_minute: int = Field(default=20, ge=1)
    items_per_page: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def production_must_be_safe(self) -> "Settings":
        """Refuse to boot production with development settings.

        Returns:
            Settings: The validated settings.

        Raises:
            ValueError: when production is misconfigured (Day 18 §6).
        """
        if self.env != "production":
            return self
        problems: list[str] = []
        if self.secret_key == "dev-only-not-for-production":
            problems.append("APP_SECRET_KEY is still the development default")
        if self.debug:
            problems.append("APP_DEBUG is true in production")
        if problems:
            raise ValueError("Unsafe production config: " + "; ".join(problems))
        return self

    @property
    def is_production(self) -> bool:
        """Whether this is production.

        Returns:
            bool: True in production.
        """
        return self.env == "production"

    def to_flask_config(self) -> dict[str, object]:
        """Translate into Flask config keys.

        Returns:
            dict[str, object]: Keys for ``app.config.update``.
        """
        return {
            "ENV_NAME": self.env,
            "DEBUG": self.debug,
            "TESTING": self.env == "testing",
            "SECRET_KEY": self.secret_key,
            "SQLALCHEMY_DATABASE_URI": self.database_url,
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            "WTF_CSRF_ENABLED": self.env != "testing",
            "SESSION_COOKIE_HTTPONLY": True,
            "SESSION_COOKIE_SAMESITE": "Lax",
            "SESSION_COOKIE_SECURE": self.is_production,
            "MAX_CONTENT_LENGTH": 2 * 1024 * 1024,
            "CACHE_TYPE": self.cache_type,
            "CACHE_DEFAULT_TIMEOUT": 60,
            "RATELIMIT_STORAGE_URI": self.rate_limit_storage,
            "RATELIMIT_HEADERS_ENABLED": True,
            "ITEMS_PER_PAGE": self.items_per_page,
        }

    def safe_dump(self) -> dict[str, object]:
        """Return settings with secrets redacted (Day 18 §9).

        Returns:
            dict[str, object]: Safe to log.
        """
        data = self.model_dump()
        for key in ("secret_key", "database_url"):
            value = str(data[key])
            data[key] = f"{value[:4]}…{value[-4:]}" if len(value) > 12 else "***"
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in data.items()}

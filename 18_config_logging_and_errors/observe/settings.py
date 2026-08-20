"""
Day 18 — Typed configuration that fails loudly.
===============================================

The problem with config classes
-------------------------------
Day 10's config classes were a real improvement, but they still have three
weaknesses:

1. **Everything is a string.** ``os.environ.get("PORT")`` gives ``"5018"``, and
   ``PORT + 1`` is a ``TypeError`` at some unlucky moment later.
2. **Typos are silent.** Set ``DATABSE_URL`` and the app boots happily on the
   default, then talks to the wrong database.
3. **Failure is late and vague.** A missing variable surfaces as
   ``NoneType has no attribute…`` deep inside a request, hours after deploy.

``pydantic-settings`` fixes all three: values are **parsed and validated at
start-up**, unknown or malformed settings are reported *together*, and the app
refuses to boot rather than limping.

The twelve-factor rule
----------------------
**Configuration lives in the environment, not in code.** The same image runs in
dev, staging and production; only the environment differs. Anything that varies
between deployments — database URL, secret key, log level, feature flags — is
config. Anything that does not is code.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application configuration, loaded and validated from the environment.

    Attributes:
        env: Which environment this is.
        debug: Whether to enable the interactive debugger.
        secret_key: Signs sessions and CSRF tokens.
        database_url: SQLAlchemy connection URL.
        log_level: Minimum level to emit.
        log_format: ``"text"`` for humans, ``"json"`` for log aggregators.
        log_file: Optional path to also write logs to.
        request_id_header: Header carrying an inbound correlation id.
        slow_request_ms: Requests slower than this are logged as warnings.
        feature_beta_reports: An example feature flag.
        admin_email: Where alerts would be sent.
    """

    model_config = SettingsConfigDict(
        # Read a .env file in development. In production, real environment
        # variables win — and a missing .env is not an error, because
        # production should not have one.
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",

        # APP_DEBUG -> debug. A prefix keeps your variables from colliding with
        # the hundreds already in the environment (PATH, HOME, LANG…).
        env_prefix="APP_",

        case_sensitive=False,

        # Forbids unknown keys passed to the CONSTRUCTOR. Note it does NOT
        # reject unknown APP_* environment variables — pydantic-settings simply
        # ignores those. Verified against pydantic-settings 2.6: exporting
        # APP_DATABSE_URL (a typo) booted happily on the default.
        #
        # That silent-typo gap is exactly the failure mode this module exists to
        # close, so `check_unknown_env_vars()` below does it explicitly.
        extra="forbid",
    )

    env: Literal["development", "testing", "production"] = "development"
    debug: bool = False

    # A default is provided so `flask run` works out of the box, but the
    # validator below REFUSES it in production. Convenience in development must
    # never become a silent weakness in production.
    secret_key: str = "dev-only-not-for-production"

    database_url: str = f"sqlite:///{BASE_DIR / 'instance' / 'app.db'}"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["text", "json"] = "text"
    log_file: Path | None = None

    request_id_header: str = "X-Request-ID"
    slow_request_ms: int = Field(default=500, ge=1, le=60_000)

    feature_beta_reports: bool = False
    admin_email: str = "ops@example.com"

    @field_validator("secret_key")
    @classmethod
    def reject_short_keys(cls, value: str) -> str:
        """Reject a secret key that is obviously too weak.

        Args:
            value: The configured key.

        Returns:
            str: The validated key.

        Raises:
            ValueError: when the key is too short to be useful.
        """
        if len(value) < 16:
            raise ValueError("must be at least 16 characters")
        return value

    @model_validator(mode="after")
    def production_must_be_locked_down(self) -> "Settings":
        """Refuse to boot a production app with development settings.

        Returns:
            Settings: The validated settings.

        Raises:
            ValueError: when production is misconfigured.

        Note:
            **Fail at start-up, not at runtime.** An app running in production
            on the default secret key means every session cookie in the world
            can be forged, and nothing in the logs would ever say so. Crashing
            on boot is the kind behaviour — a deploy that fails loudly is a
            problem; one that succeeds quietly and insecurely is an incident.
        """
        if self.env != "production":
            return self

        problems: list[str] = []
        if self.secret_key == "dev-only-not-for-production":
            problems.append("APP_SECRET_KEY is still the development default")
        if self.debug:
            problems.append(
                "APP_DEBUG is true — the Werkzeug debugger allows remote code execution"
            )
        if self.database_url.startswith("sqlite"):
            problems.append("APP_DATABASE_URL points at SQLite, which cannot serve "
                            "concurrent writers (Day 07)")
        if problems:
            raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self

    @property
    def is_production(self) -> bool:
        """Whether this is a production deployment.

        Returns:
            bool: True in production.
        """
        return self.env == "production"

    def to_flask_config(self) -> dict[str, object]:
        """Translate settings into Flask's ``UPPERCASE`` config keys.

        Returns:
            dict[str, object]: Keys suitable for ``app.config.update``.

        Note:
            One adapter, in one place. Everything else in the application reads
            the typed ``Settings`` object, so a typo in a config key is caught
            by the type checker rather than producing ``None`` at runtime.
        """
        return {
            "ENV_NAME": self.env,
            "DEBUG": self.debug,
            "TESTING": self.env == "testing",
            "SECRET_KEY": self.secret_key,
            "SQLALCHEMY_DATABASE_URI": self.database_url,
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            "SESSION_COOKIE_HTTPONLY": True,
            "SESSION_COOKIE_SAMESITE": "Lax",
            "SESSION_COOKIE_SECURE": self.is_production,
            "PREFERRED_URL_SCHEME": "https" if self.is_production else "http",
        }

    def safe_dump(self) -> dict[str, object]:
        """Return settings with every secret redacted.

        Returns:
            dict[str, object]: Safe to log, safe to expose on a debug endpoint.

        Note:
            **Never log a settings object directly.** Its ``repr`` contains your
            secret key and database password, and log aggregators are searchable
            by people who should not have them. Redact at the boundary, and make
            the redacted version the *only* convenient one to print.
        """
        redacted_keys = {"secret_key", "database_url"}
        data: dict[str, object] = {}
        for name, value in self.model_dump().items():
            if name in redacted_keys:
                data[name] = _redact(str(value))
            else:
                data[name] = str(value) if isinstance(value, Path) else value
        return data


def _redact(value: str) -> str:
    """Mask a secret, keeping just enough to identify it.

    Args:
        value: The secret string.

    Returns:
        str: e.g. ``"post…/app"`` — enough to tell two values apart, not enough
        to use.

    Note:
        Showing the first and last few characters lets an operator confirm
        *which* value is loaded without revealing it. Printing nothing at all
        makes "is it even set?" impossible to answer from a log.
    """
    if len(value) <= 12:
        return "***"
    return f"{value[:4]}…{value[-4:]}"


class UnknownSettingError(Exception):
    """Raised when the environment contains a prefixed variable we do not know.

    Attributes:
        names: The offending variable names.
    """

    def __init__(self, names: list[str]) -> None:
        """Initialise the error.

        Args:
            names: Unrecognised environment variable names.
        """
        self.names = names
        super().__init__(
            "Unknown environment variable(s): " + ", ".join(sorted(names))
        )


def check_unknown_env_vars(environ: dict[str, str] | None = None) -> list[str]:
    """Return any ``APP_*`` variables that do not map to a known setting.

    Args:
        environ: The environment to inspect. Defaults to the real one; tests
            pass their own rather than mutating the process environment.

    Returns:
        list[str]: Unrecognised variable names, empty when all are known.

    Note:
        **Why this is hand-written.** ``extra="forbid"`` only rejects unknown
        keys passed to the constructor; pydantic-settings silently *ignores*
        environment variables that match no field. So a typo like::

            APP_DATABSE_URL=postgresql://…       # note: DATABSE

        boots happily on the SQLite default and then talks to the wrong
        database — the exact class of silent failure that typed settings are
        supposed to eliminate.

        Ten lines of explicit checking turn that into a start-up error. This is
        worth doing because the failure it prevents is both easy to cause and
        very hard to diagnose: everything *works*, just against the wrong
        backend.
    """
    import os

    environ = environ if environ is not None else dict(os.environ)
    prefix = "APP_"
    known = {f"{prefix}{name}".upper() for name in Settings.model_fields}

    return [
        name for name in environ
        if name.upper().startswith(prefix) and name.upper() not in known
    ]


def load_settings(**overrides: object) -> Settings:
    """Build a validated :class:`Settings`.

    Args:
        **overrides: Values that win over the environment. Used by tests, which
            must not depend on whatever happens to be exported in your shell.

    Returns:
        Settings: The validated configuration.

    Raises:
        pydantic.ValidationError: when the environment is invalid. Every problem
        is reported at once, rather than one per restart.
    """
    unknown = check_unknown_env_vars()
    if unknown:
        raise UnknownSettingError(unknown)
    return Settings(**overrides)  # type: ignore[arg-type]


def generate_secret_key() -> str:
    """Return a cryptographically strong secret key.

    Returns:
        str: 64 hex characters.
    """
    return secrets.token_hex(32)

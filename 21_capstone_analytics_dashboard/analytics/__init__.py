"""
Day 21 — Capstone: a feedback-analytics application.
=====================================================

What this is
------------
A small but complete product: sign up, create feedback surveys, share a public
link, collect scores, and read the results through a dashboard, a CSV export, or
a JSON API.

It exists to show the twenty preceding days **working together**, because
techniques that are clear in isolation interact in ways that only become
visible in a whole application.

Where each day shows up
-----------------------
=======  =====================================================================
Day 01   ``/healthz``, and never ``app.run()`` in production
Day 02   URL converters, ``url_for``, custom error pages
Day 03   template inheritance, macros, custom filters
Day 04   POST/Redirect/GET, 303/422, ``MAX_CONTENT_LENGTH``
Day 05   Flask-WTF forms with CSRF
Day 06   signed session cookies, flash messages, cookie hardening
Day 07   CSV export, layered modules, CLI commands
Day 08   models, relationships, cascades, ``CHECK`` constraints, SQL aggregates
Day 09   Alembic migrations rather than ``create_all()``
Day 10   application factory, blueprints, per-environment config
Day 11   versioned REST API, status codes, one error envelope, pagination
Day 12   Pydantic schemas, ``extra="forbid"``, ``exclude_unset``, ``StrictInt``
Day 13   password hashing, session fixation, open redirect, IDOR
Day 14   ownership across a chain, centralised in ``security.py``
Day 15   token authentication, and revocation by rotation
Day 16   (upload patterns — see the exercises)
Day 17   a real test suite, pure functions tested first
Day 18   typed settings, structured logging, request ids, redaction
Day 19   caching with write-path invalidation, rate limiting
Day 20   gunicorn, Docker, health and readiness probes
=======  =====================================================================

How to run
----------
From the repository root::

    source .venv/bin/activate
    cd 21_capstone_analytics_dashboard

    FLASK_APP=wsgi.py flask seed
    FLASK_APP=wsgi.py flask run --port 5021 --debug

    pytest                                   # the test suite
    gunicorn --config gunicorn.conf.py wsgi:app    # production-style
"""

from __future__ import annotations

import logging
import logging.config
import sys
import time
import uuid
from typing import Any

import click
from flask import Flask, g, jsonify, render_template, request
from flask.cli import with_appcontext
from flask.typing import ResponseReturnValue
from pydantic import ValidationError

from .extensions import cache, csrf, db, limiter, login_manager, migrate
from .settings import Settings

logger = logging.getLogger("app")


def create_app(settings: Settings | None = None) -> Flask:
    """Build and configure the application.

    Args:
        settings: Pre-built settings, mainly for tests. Loaded from the
            environment when omitted.

    Returns:
        Flask: A fully wired application.

    Raises:
        SystemExit: when the environment is invalid (Day 18 §6).
    """
    if settings is None:
        try:
            settings = Settings()
        except ValidationError as exc:
            print("\n  Configuration error — cannot start:\n")
            for problem in exc.errors():
                location = ".".join(str(p) for p in problem["loc"]) or "(settings)"
                print(f"    APP_{location.upper()}: {problem['msg']}")
            print()
            raise SystemExit(2) from exc

    _configure_logging(settings)

    app = Flask(__name__, instance_relative_config=True)
    app.config.update(settings.to_flask_config())
    app.config["RESPONSES_PER_MINUTE"] = settings.responses_per_minute
    if settings.env == "testing":
        # scrypt is slow by design; a cheap hash keeps the suite fast, scoped so
        # it can never reach production (Day 14 §9).
        app.config["PASSWORD_HASH_METHOD"] = "pbkdf2:sha256:1000"
    app.extensions["settings"] = settings

    from pathlib import Path
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    # ---- Extensions (all created bare; bound here) --------------------------
    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)
    csrf.init_app(app)
    login_manager.init_app(app)
    cache.init_app(app)
    limiter.init_app(app)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please sign in to continue."
    login_manager.login_message_category = "info"

    # Imported inside the factory: by now the package is fully imported, so a
    # circular import is structurally impossible (Day 10 §5).
    from .models import Response, Survey, User  # noqa: F401

    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        """Reload a user from the session cookie.

        Args:
            user_id: The id as a string — the cookie stores text.

        Returns:
            User | None: The user, or ``None`` when unknown.
        """
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    from .blueprints.api import api_bp
    from .blueprints.auth import auth_bp
    from .blueprints.public import public_bp
    from .blueprints.surveys import surveys_bp

    app.register_blueprint(surveys_bp, url_prefix="/surveys")
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(public_bp)
    app.register_blueprint(api_bp)

    _register_core_routes(app)
    _register_request_logging(app)
    _register_errors(app)
    _register_template_helpers(app)
    _register_commands(app)

    logger.info("Started in %s mode", settings.env,
                extra={"extra_fields": {"config": settings.safe_dump()}})
    return app


def _configure_logging(settings: Settings) -> None:
    """Configure logging for the whole process (Day 18 §8).

    Args:
        settings: Validated settings.
    """
    from flask import has_request_context

    class RequestFilter(logging.Filter):
        """Attach the request id to every record."""

        def filter(self, record: logging.LogRecord) -> bool:
            """Enrich the record.

            Args:
                record: The record being emitted.

            Returns:
                bool: Always True — nothing is filtered out.
            """
            # The guard matters: logs are also emitted at start-up and from CLI
            # commands, where touching `request` would raise.
            record.request_id = getattr(g, "request_id", "-") if has_request_context() else "-"
            return True

    class JsonFormatter(logging.Formatter):
        """Render records as one JSON object per line."""

        def format(self, record: logging.LogRecord) -> str:
            """Format the record.

            Args:
                record: The record being emitted.

            Returns:
                str: A single-line JSON object.
            """
            import json

            payload: dict[str, Any] = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
                "level": record.levelname, "logger": record.name,
                "message": record.getMessage(),
                "request_id": getattr(record, "request_id", "-"),
            }
            if record.exc_info:
                payload["exception"] = self.formatException(record.exc_info)
            payload.update(getattr(record, "extra_fields", {}))
            return json.dumps(payload, default=str)

    formatter: dict[str, Any] = (
        {"()": JsonFormatter} if settings.log_format == "json"
        else {"format": "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s",
              "datefmt": "%H:%M:%S"}
    )

    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"request": {"()": RequestFilter}},
        "formatters": {"default": formatter},
        "handlers": {"console": {"class": "logging.StreamHandler", "stream": sys.stdout,
                                 "formatter": "default", "filters": ["request"]}},
        "root": {"level": settings.log_level, "handlers": ["console"]},
        "loggers": {"werkzeug": {"level": "WARNING"}},
    })


def _register_request_logging(app: Flask) -> None:
    """Assign a request id and log every request's outcome (Day 18 §8).

    Args:
        app: The application.
    """
    request_logger = logging.getLogger("app.request")

    @app.before_request
    def start() -> None:
        """Assign a correlation id, reusing an inbound one when present."""
        g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        g.started = time.perf_counter()

    @app.after_request
    def finish(response: Any) -> Any:
        """Log the outcome and echo the correlation id.

        Args:
            response: The outgoing response.

        Returns:
            Any: The same response, with the header added.
        """
        duration_ms = (time.perf_counter() - getattr(g, "started", time.perf_counter())) * 1000
        response.headers["X-Request-ID"] = getattr(g, "request_id", "-")

        # Security headers (Day 20 §8).
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

        level = (logging.ERROR if response.status_code >= 500
                 else logging.WARNING if response.status_code >= 400 or duration_ms > 500
                 else logging.INFO)
        request_logger.log(level, "%s %s -> %s in %.1fms",
                           request.method, request.path, response.status_code, duration_ms,
                           extra={"extra_fields": {"status": response.status_code,
                                                   "duration_ms": round(duration_ms, 1)}})
        return response


def _register_core_routes(app: Flask) -> None:
    """Register the root and health endpoints.

    Args:
        app: The application.
    """

    @app.get("/")
    def home() -> ResponseReturnValue:
        """Public landing page.

        Returns:
            ResponseReturnValue: The rendered page.
        """
        return render_template("home.html")

    @app.get("/healthz")
    @limiter.exempt
    def healthz() -> ResponseReturnValue:
        """Liveness probe — no dependency checks (Day 18 §11).

        Returns:
            ResponseReturnValue: Always ``200`` while the process runs.
        """
        return jsonify(status="ok", service="analytics")

    @app.get("/readyz")
    @limiter.exempt
    def readyz() -> ResponseReturnValue:
        """Readiness probe — dependencies belong here.

        Returns:
            ResponseReturnValue: ``200`` when ready, ``503`` when not.
        """
        try:
            db.session.execute(db.text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            return jsonify(status="not-ready", database=str(exc)), 503
        return jsonify(status="ready", database="reachable")


def _register_errors(app: Flask) -> None:
    """Install error handlers.

    Args:
        app: The application.
    """

    @app.errorhandler(404)
    def not_found(error: Exception) -> ResponseReturnValue:
        """Render a 404 page.

        Args:
            error: The exception.

        Returns:
            ResponseReturnValue: The page and status code.
        """
        return render_template("error.html", code=404,
                               message=getattr(error, "description", "Not found.")), 404

    @app.errorhandler(429)
    def rate_limited(error: Exception) -> ResponseReturnValue:
        """Answer a rate-limited request (Day 19 §8).

        Args:
            error: The exception.

        Returns:
            ResponseReturnValue: The page and status code.
        """
        return render_template("error.html", code=429,
                               message="Too many requests. Please slow down."), 429

    @app.errorhandler(500)
    @app.errorhandler(Exception)
    def server_error(error: Exception) -> ResponseReturnValue:
        """Log the traceback; tell the client nothing (Day 18 §10).

        Args:
            error: The unhandled exception.

        Returns:
            ResponseReturnValue: A generic page or JSON envelope.
        """
        from werkzeug.exceptions import HTTPException

        if isinstance(error, HTTPException):
            return error  # type: ignore[return-value]

        db.session.rollback()   # a failed request must not poison the session
        logger.exception("Unhandled %s on %s", type(error).__name__, request.path)

        if request.path.startswith("/api/"):
            return jsonify(error={"status": 500, "code": "internal_error",
                                  "message": "An unexpected error occurred.",
                                  "request_id": g.get("request_id", "-")}), 500
        return render_template("error.html", code=500,
                               message="Something went wrong. It has been logged."), 500


def _register_template_helpers(app: Flask) -> None:
    """Register filters and globals used by the templates.

    Args:
        app: The application.
    """
    from .models import SurveyStatus

    # Enums go in jinja_env.globals, NOT a context processor: macros are
    # isolated from the caller's context and cannot see context-processor
    # values (Day 14 §8).
    app.jinja_env.globals.update(SurveyStatus=SurveyStatus, app_name="Pulse")

    @app.template_filter("nice_dt")
    def nice_dt(value: Any) -> str:
        """Render a datetime for display.

        Args:
            value: A datetime or ``None``.

        Returns:
            str: e.g. ``"20 Aug 2026 14:03"``, or an em dash.
        """
        return value.strftime("%d %b %Y %H:%M") if value else "—"

    @app.context_processor
    def inject_request_globals() -> dict[str, Any]:
        """Expose genuinely per-request values.

        Returns:
            dict[str, Any]: Template context additions.
        """
        return {"request_id": g.get("request_id", "-")}


def _register_commands(app: Flask) -> None:
    """Attach CLI commands.

    Args:
        app: The application.
    """

    @click.command("init-db")
    @with_appcontext
    def init_db() -> None:
        """Create tables directly (use migrations for a real database)."""
        db.create_all()
        click.echo("Tables created.")

    @click.command("seed")
    @with_appcontext
    def seed() -> None:
        """Create a demo account with surveys and responses."""
        import random

        from sqlalchemy import select

        from .models import Response, Survey, SurveyStatus, User

        db.create_all()

        user = db.session.execute(
            select(User).where(User.email == "ana@example.com")
        ).scalar_one_or_none()
        if user is None:
            user = User(email="ana@example.com", display_name="Ana Rao")
            user.set_password("CorrectHorseBattery1")
            user.rotate_token()
            db.session.add(user)
            db.session.flush()

        other = db.session.execute(
            select(User).where(User.email == "vik@example.com")
        ).scalar_one_or_none()
        if other is None:
            other = User(email="vik@example.com", display_name="Vikram Shah")
            other.set_password("CorrectHorseBattery2")
            other.rotate_token()
            db.session.add(other)
            db.session.flush()
            db.session.add(Survey(slug=Survey.new_slug(), title="Vikram's private survey",
                                  question="Ana must never see this.",
                                  status=SurveyStatus.DRAFT, owner_id=other.id))

        if not user.surveys:
            specs = [
                ("Onboarding experience", "How likely are you to recommend our onboarding?",
                 SurveyStatus.OPEN, 24),
                ("Support quality", "How satisfied were you with support?",
                 SurveyStatus.OPEN, 15),
                ("Q4 pricing research", "How do you feel about the new pricing?",
                 SurveyStatus.DRAFT, 0),
            ]
            for title, question, status, count in specs:
                survey = Survey(slug=Survey.new_slug(), title=title, question=question,
                                status=status, owner_id=user.id)
                db.session.add(survey)
                db.session.flush()
                for _ in range(count):
                    # Weighted towards higher scores, like real feedback.
                    score = random.choices(range(11), weights=[1,1,1,2,2,3,4,6,9,12,10])[0]
                    db.session.add(Response(
                        survey_id=survey.id, score=score,
                        comment=random.choice([
                            "", "", "Very helpful, thank you.", "Took a while to get going.",
                            "Best support I've had.", "Documentation could be clearer.",
                        ]),
                    ))

        db.session.commit()
        click.echo("Seeded.\n")
        click.echo("  Sign in: ana@example.com / CorrectHorseBattery1")
        click.echo(f"  API token: {user.api_token}\n")
        for survey in user.surveys:
            click.echo(f"  /s/{survey.slug}  {survey.title} ({survey.status.value})")
        click.echo("")

    @click.command("check-config")
    @with_appcontext
    def check_config() -> None:
        """Print the loaded configuration, redacted."""
        settings: Settings = app.extensions["settings"]
        click.echo("")
        for key, value in settings.safe_dump().items():
            click.echo(f"    APP_{key.upper():<24} {value}")
        click.echo("")

    app.cli.add_command(init_db)
    app.cli.add_command(seed)
    app.cli.add_command(check_config)


__all__ = ["create_app"]

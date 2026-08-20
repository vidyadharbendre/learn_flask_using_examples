"""
Day 18 — Config, Logging and Error Handling.
============================================

Real-world scenario
-------------------
The same application image runs on your laptop, in staging and in production.
Only the environment differs — and when something breaks at 3am, the logs are
the only witness.

What you will learn
-------------------
1. **Typed settings** with ``pydantic-settings``: parsed, validated, and failing
   loudly at start-up.
2. The twelve-factor rule: **config in the environment, not in code**.
3. **Structured logging**, text for humans and JSON for aggregators.
4. **Request ids** so one user's journey can be reconstructed under load.
5. **Redaction**: what must never reach a log.
6. Error handling that tells *you* everything and the client nothing.
7. **Liveness vs readiness** — two probes, two different questions.

How to run
----------
From the repository root::

    source .venv/bin/activate
    export FLASK_APP=18_config_logging_and_errors/wsgi.py
    flask run --port 5018

Try it with different configuration::

    APP_LOG_FORMAT=json APP_LOG_LEVEL=DEBUG flask run --port 5018
    APP_ENV=production flask run --port 5018          # refuses to boot
"""

from __future__ import annotations

import logging
import time
from typing import Any

import click
from flask import Flask, g, jsonify, render_template_string, request
from flask.cli import with_appcontext
from flask.typing import ResponseReturnValue
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from .logging_setup import configure_logging, register_request_logging, scrub
from .settings import Settings, UnknownSettingError, load_settings

logger = logging.getLogger("app")


def create_app(settings: Settings | None = None) -> Flask:
    """Build the application.

    Args:
        settings: Pre-built settings, mainly for tests. When omitted they are
            loaded and validated from the environment.

    Returns:
        Flask: A configured application.

    Raises:
        SystemExit: when the environment is invalid. The traceback from a
            ``ValidationError`` is noise for an operator; a clear list of what
            is wrong is what they need.
    """
    if settings is None:
        try:
            settings = load_settings()
        except UnknownSettingError as error:
            print(f"\n  Configuration error — {error}")
            print("  (a typo here would otherwise boot silently on the default)\n")
            raise SystemExit(2) from error
        except ValidationError as error:
            # Print a readable summary, then exit non-zero so the orchestrator
            # knows the deploy failed rather than restarting into the same
            # broken state forever.
            print("\n  Configuration error — the application cannot start:\n")
            for problem in error.errors():
                location = ".".join(str(part) for part in problem["loc"]) or "(settings)"
                print(f"    APP_{location.upper()}: {problem['msg']}")
            print()
            raise SystemExit(2) from error

    # Logging is configured FIRST, so that anything which fails during the rest
    # of start-up is itself logged properly.
    configure_logging(settings)

    app = Flask(__name__)
    app.config.update(settings.to_flask_config())
    app.extensions["settings"] = settings

    register_request_logging(app, settings)
    _register_routes(app, settings)
    _register_error_handlers(app, settings)
    _register_commands(app, settings)

    # Log the configuration ONCE at start-up, redacted. This single line answers
    # "which config is this instance actually running?" — a question that
    # otherwise costs an hour of guessing during an incident.
    logger.info("Starting in %s mode", settings.env,
                extra={"extra_fields": {"config": settings.safe_dump()}})

    return app


def _register_routes(app: Flask, settings: Settings) -> None:
    """Register demonstration endpoints.

    Args:
        app: The application.
        settings: Validated settings.
    """

    @app.get("/")
    def index() -> ResponseReturnValue:
        """Describe the demo endpoints.

        Returns:
            ResponseReturnValue: A map of what to try.
        """
        return jsonify({
            "service": "observe",
            "env": settings.env,
            "request_id": g.request_id,
            "try": {
                "/config": "the loaded settings, secrets redacted",
                "/log-levels": "emit one line at every level",
                "/echo (POST)": "logs the body with secrets redacted",
                "/slow?ms=800": "logged as a WARNING when over the threshold",
                "/boom": "an unhandled exception: full detail logged, nothing leaked",
                "/fail/404": "an HTTP error",
                "/healthz": "liveness — is the process alive?",
                "/readyz": "readiness — can it serve traffic?",
            },
        })

    @app.get("/config")
    def show_config() -> ResponseReturnValue:
        """Return the loaded settings with secrets redacted.

        Returns:
            ResponseReturnValue: The safe configuration dump.

        Warning:
            Even redacted, an endpoint like this belongs behind authentication
            in production (Day 13/15). It is unprotected here only to make the
            lesson visible.
        """
        return jsonify(settings.safe_dump())

    @app.get("/log-levels")
    def log_levels() -> ResponseReturnValue:
        """Emit one line at each level, to show filtering by ``APP_LOG_LEVEL``.

        Returns:
            ResponseReturnValue: What was emitted and what is visible.
        """
        logger.debug("DEBUG: verbose detail, usually off in production")
        logger.info("INFO: a normal notable event")
        logger.warning("WARNING: unexpected but handled")
        logger.error("ERROR: this request failed; a human should look")

        return jsonify(
            emitted=["DEBUG", "INFO", "WARNING", "ERROR"],
            visible_at_or_above=settings.log_level,
            hint="Re-run with APP_LOG_LEVEL=DEBUG to see them all.",
        )

    @app.post("/echo")
    def echo() -> ResponseReturnValue:
        """Log the submitted body with sensitive values redacted.

        Returns:
            ResponseReturnValue: What was received and what was logged.

        Note:
            Compare the two. ``password`` and ``api_key`` are present in the
            response (they came from the caller) and **absent** from the log.
            Logging ``request.form`` wholesale is how plaintext passwords end up
            in a retained, searchable log — a breach that password hashing does
            nothing to prevent, because the value was written to disk before it
            ever reached the hashing code.
        """
        body = request.get_json(silent=True) or request.form.to_dict()
        safe = scrub(dict(body))

        logger.info("Received %d field(s)", len(body),
                    extra={"extra_fields": {"body": safe}})

        return jsonify(received=body, logged=safe,
                       note="Check the log: the secrets are redacted there.")

    @app.get("/slow")
    def slow() -> ResponseReturnValue:
        """Sleep for a while, to demonstrate slow-request warnings.

        Returns:
            ResponseReturnValue: How long it took.
        """
        delay_ms = min(request.args.get("ms", default=800, type=int), 3000)
        time.sleep(delay_ms / 1000)
        return jsonify(slept_ms=delay_ms, threshold_ms=settings.slow_request_ms)

    @app.get("/boom")
    def boom() -> ResponseReturnValue:
        """Raise an unhandled exception on purpose.

        Returns:
            ResponseReturnValue: Never — this always raises.

        Raises:
            ZeroDivisionError: always.
        """
        customer_id = 4172
        return jsonify(result=1 / 0, customer=customer_id)  # noqa: B018

    @app.get("/fail/<int:code>")
    def fail(code: int) -> ResponseReturnValue:
        """Return a chosen HTTP error.

        Args:
            code: The status code to raise.

        Returns:
            ResponseReturnValue: Never — this aborts.
        """
        from flask import abort

        abort(code if 400 <= code <= 599 else 400)

    # -------------------------------------------------------------------------
    # Two probes, two different questions
    # -------------------------------------------------------------------------
    @app.get("/healthz")
    def healthz() -> ResponseReturnValue:
        """**Liveness**: is this process alive and able to answer?

        Returns:
            ResponseReturnValue: Always ``200`` while the process runs.

        Note:
            A liveness probe must **not** check dependencies. If it returns
            unhealthy, the orchestrator **kills and restarts the container** —
            and restarting your app does not fix someone else's database. A
            liveness probe that checks the database turns a brief DB blip into a
            restart storm across your entire fleet.
        """
        return jsonify(status="ok", check="liveness")

    @app.get("/readyz")
    def readyz() -> ResponseReturnValue:
        """**Readiness**: can this instance serve traffic right now?

        Returns:
            ResponseReturnValue: ``200`` when ready, ``503`` when not.

        Note:
            A readiness probe **should** check dependencies. Failing it removes
            this instance from the load balancer without killing it — so it
            stops receiving traffic, and rejoins automatically when the
            dependency recovers.

            Liveness: "restart me." Readiness: "skip me for now." Confusing the
            two is one of the most common Kubernetes misconfigurations.
        """
        checks = {"config": True, "storage": True}
        ready = all(checks.values())
        return jsonify(status="ready" if ready else "not-ready",
                       check="readiness", checks=checks), (200 if ready else 503)


def _register_error_handlers(app: Flask, settings: Settings) -> None:
    """Install error handlers.

    Args:
        app: The application.
        settings: Validated settings.
    """

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException) -> ResponseReturnValue:
        """Return a deliberate HTTP error in a consistent shape.

        Args:
            error: The raised exception.

        Returns:
            ResponseReturnValue: JSON envelope with the request id.

        Note:
            Including the request id lets a user quote it in a support ticket,
            turning "it broke yesterday" into one log query.
        """
        return jsonify(error={
            "status": error.code,
            "message": error.description,
            "request_id": g.get("request_id", "-"),
        }), (error.code or 500)

    @app.errorhandler(Exception)
    def handle_unexpected(error: Exception) -> ResponseReturnValue:
        """Log an unhandled exception fully; tell the client almost nothing.

        Args:
            error: The unhandled exception.

        Returns:
            ResponseReturnValue: A generic 500 carrying the request id.

        Note:
            This is the asymmetry that matters:

            - ``logger.exception`` writes the **full traceback** to your logs,
              where only you can read it.
            - The **response** contains a generic message and a request id.

            Never put ``str(error)`` in a response. Exception text leaks file
            paths, SQL fragments, internal hostnames and sometimes credentials —
            and it is exactly what an attacker probes for.

            The request id bridges the two: the user quotes it, you search for
            it, and you have the traceback in seconds.
        """
        # .exception() logs at ERROR *with* exc_info — never use .error(str(e)),
        # which throws away the traceback you will want most.
        logger.exception("Unhandled %s on %s %s",
                         type(error).__name__, request.method, request.path)

        return jsonify(error={
            "status": 500,
            "message": "An unexpected error occurred.",
            "request_id": g.get("request_id", "-"),
            "support": settings.admin_email,
        }), 500


def _register_commands(app: Flask, settings: Settings) -> None:
    """Attach CLI commands.

    Args:
        app: The application.
        settings: Validated settings.
    """

    @click.command("check-config")
    @with_appcontext
    def check_config() -> None:
        """Print the loaded configuration, redacted.

        Run this on a deployed box to answer "what is this instance actually
        running?" without guessing.
        """
        click.echo(f"\n  Environment: {settings.env}\n")
        for key, value in settings.safe_dump().items():
            click.echo(f"    APP_{key.upper():<22} {value}")
        click.echo("")

    @click.command("new-secret")
    def new_secret() -> None:
        """Generate a strong SECRET_KEY."""
        from .settings import generate_secret_key

        click.echo(f"\n  APP_SECRET_KEY={generate_secret_key()}\n")
        click.echo("  Put it in your secret manager — never in git.\n")

    app.cli.add_command(check_config)
    app.cli.add_command(new_secret)


__all__ = ["create_app"]

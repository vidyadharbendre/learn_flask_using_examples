"""
Day 10 — The application factory.
=================================

Real-world scenario
-------------------
The inventory app from Days 08-09 has outgrown a single ``app.py``. Nine
routes, three CLI commands, filters, error handlers and config are all in one
file, and two people cannot edit it without conflicting.

Today it becomes a **package**: a factory function that builds the app, and
blueprints that own their own routes and templates.

Why a factory instead of a module-level ``app``
------------------------------------------------
Everything so far did this::

    app = Flask(__name__)          # created once, at import time
    app.config[...] = ...

That single global has four real problems:

1. **You cannot configure it differently per environment.** The config is baked
   in the moment the module is imported.
2. **You cannot create two of them.** Tests want a fresh app with a fresh
   database *per test*; with a module-level app they all share one.
3. **It forces circular imports.** Anything needing ``app`` must import the
   module that defines the routes, which imports back.
4. **Import order becomes load-bearing** — the fragile "must import models
   *after* ``init_app``" dance.

A factory fixes all four::

    def create_app(config_name="default") -> Flask:
        app = Flask(__name__)
        app.config.from_object(config_by_name[config_name])
        db.init_app(app)
        app.register_blueprint(products_bp)
        return app

Nothing exists until you call it, and you can call it as often as you like with
different settings.

How to run
----------
From the repository root::

    source .venv/bin/activate
    export FLASK_APP=10_blueprints_and_app_factory/wsgi.py
    flask seed
    flask run --port 5010 --debug

The ``flask`` CLI finds ``create_app`` automatically when it is exported from a
package, which is why ``wsgi.py`` is a three-line file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from flask import Flask, render_template, render_template_string, render_template_string

from flask.typing import ResponseReturnValue
from sqlalchemy.exc import OperationalError
from .config import Config, config_by_name
from .extensions import csrf, db, migrate


def create_app(config_name: str | None = None) -> Flask:
    """Build and return a configured Flask application.

    This is the **application factory**. Everything the app needs is assembled
    here, in a deliberate order, and nothing happens at import time.

    Args:
        config_name: Which configuration to use — ``"development"``,
            ``"testing"``, ``"production"``, or ``None`` to read the
            ``FLASK_CONFIG`` environment variable (defaulting to development).

    Returns:
        Flask: A fully wired application instance.

    Example:
        >>> app = create_app("testing")          # doctest: +SKIP
        >>> client = app.test_client()           # doctest: +SKIP

        Each call returns an **independent** app. That is what makes per-test
        isolation possible.
    """
    # instance_relative_config lets Flask resolve `instance/` next to the
    # package, which is where the SQLite file and any local secrets live.
    app = Flask(__name__, instance_relative_config=True)

    # ---- 1. Configuration ---------------------------------------------------
    config_name = config_name or os.environ.get("FLASK_CONFIG", "default")
    config_class = config_by_name.get(config_name, config_by_name["default"])
    app.config.from_object(config_class)

    # from_object reads UPPERCASE attributes only. Lowercase names (like the
    # `init_app` helper) are ignored, which is how a config class can carry
    # behaviour without polluting app.config.
    config_class.init_app(app)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    # ---- 2. Extensions ------------------------------------------------------
    # Each object was created bare in extensions.py; init_app binds it to THIS
    # application. The same objects can be bound to a different app in the next
    # test, which is the whole point of deferred initialisation.
    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)
    csrf.init_app(app)

    # ---- 3. Models ----------------------------------------------------------
    # Imported INSIDE the factory, not at module top level. By the time this
    # runs, `inventory` is fully imported, so `from .models import ...` cannot
    # produce a partially-initialised module. This is the structural cure for
    # the circular import — no import-order gymnastics required.
    from . import models  # noqa: F401  (imported for its side effect: metadata)

    # ---- 4. Blueprints ------------------------------------------------------
    from .blueprints.api import api_bp
    from .blueprints.main import main_bp
    from .blueprints.products import products_bp

    app.register_blueprint(main_bp)
    # url_prefix is applied at REGISTRATION, not in the blueprint. The same
    # blueprint could be mounted at two prefixes, and moving a whole section of
    # the site is a one-line change here.
    app.register_blueprint(products_bp, url_prefix="/products")
    app.register_blueprint(api_bp, url_prefix="/api")

    # ---- 5. Cross-cutting concerns -----------------------------------------
    _register_error_handlers(app)
    _register_template_helpers(app)

    from .commands import register_commands
    register_commands(app)


    # -----------------------------------------------------------------------------
    # A friendly error for the most common beginner mistake
    # -----------------------------------------------------------------------------
    # Skip the setup step and SQLAlchemy raises `OperationalError: no such table`,
    # which tells a beginner nothing about what to do next. Catching it and naming
    # the exact command turns a dead end into a one-line fix.
    #
    # This is a small thing that matters: the quality of your error messages IS the
    # quality of your onboarding.
    @app.errorhandler(OperationalError)
    def database_not_initialised(error: OperationalError) -> ResponseReturnValue:
        """Explain a missing table instead of showing a raw SQLAlchemy traceback.

        Args:
            error: The raised ``OperationalError``.

        Returns:
            ResponseReturnValue: A 503 naming the command that fixes it, or a generic
            500 for any other database failure.
        """
        if "no such table" not in str(getattr(error, "orig", error)).lower():
            # Some other database problem — do not pretend to diagnose it.
            return render_template_string("<h1>Database error</h1><p>See the server log.</p>"), 500

        return render_template_string(
            """<!doctype html><title>Database not ready</title>
            <style>body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;
            display:grid;place-items:center;min-height:100vh;margin:0}
            div{max-width:40rem;padding:2rem}pre{background:#1e293b;padding:1rem;
            border-radius:.5rem;overflow-x:auto}code{color:#38bdf8}</style>
            <div><h1>The database has not been set up yet</h1>
            <p>The tables do not exist. Run this first, then reload:</p>
            <pre>cd 10_blueprints_and_app_factory\nFLASK_APP=wsgi.py flask seed</pre>
            <p style="color:#94a3b8">See this day's README, section 3.</p></div>"""
        ), 503

    return app


def _register_error_handlers(app: Flask) -> None:
    """Attach application-wide error handlers.

    Args:
        app: The application being built.

    Note:
        Registered on the **app**, so they cover every blueprint. A blueprint
        can also register its own handler with ``@bp.errorhandler`` — useful
        when the API section should answer with JSON while the HTML section
        renders a page. See :mod:`inventory.blueprints.api`.
    """

    @app.errorhandler(404)
    def not_found(error: Exception) -> tuple[str, int]:
        """Render the shared 404 page.

        Args:
            error: The ``NotFound`` exception.

        Returns:
            tuple[str, int]: Rendered page and status code.
        """
        return render_template("errors/404.html", error=error), 404

    @app.errorhandler(500)
    def server_error(error: Exception) -> tuple[str, int]:
        """Render the shared 500 page.

        Args:
            error: The unhandled exception.

        Returns:
            tuple[str, int]: Rendered page and status code.

        Note:
            Never leak the exception text to users — it can expose file paths,
            query fragments and internal hostnames. Log the detail (Day 18),
            show a generic message.
        """
        db.session.rollback()  # a failed request must not poison the session
        return render_template("errors/500.html"), 500


def _register_template_helpers(app: Flask) -> None:
    """Register filters and context processors used across all blueprints.

    Args:
        app: The application being built.
    """
    from decimal import Decimal

    @app.template_filter("inr")
    def inr(amount: Decimal | int | float | None) -> str:
        """Format a monetary amount as Indian Rupees.

        Args:
            amount: A ``Decimal`` from the database, or ``None``.

        Returns:
            str: e.g. ``"₹89,999.00"``.
        """
        value = Decimal(str(amount or 0))
        whole, fraction = divmod(int(round(abs(value) * 100)), 100)
        digits = str(whole)
        if len(digits) > 3:
            last3, rest = digits[-3:], digits[:-3]
            pairs: list[str] = []
            while len(rest) > 2:
                pairs.insert(0, rest[-2:])
                rest = rest[:-2]
            if rest:
                pairs.insert(0, rest)
            digits = ",".join([*pairs, last3])
        return f"{'-' if value < 0 else ''}₹{digits}.{fraction:02d}"

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        """Expose shared values to every template in every blueprint.

        Returns:
            dict[str, Any]: Template globals.
        """
        return {
            "app_name": "Stockroom",
            "config_name": app.config.get("ENV_NAME", type(app.config).__name__),
        }


__all__ = ["create_app", "Config"]

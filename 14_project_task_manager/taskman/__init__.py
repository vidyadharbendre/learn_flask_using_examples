"""
Day 14 — Week 2 Project: a task manager.
========================================

Real-world scenario
-------------------
Projects and tasks, per user: create projects, add tasks with status, priority
and due dates, filter across everything, and read the same data through a JSON
API. Small enough to hold in your head, complete enough to be real.

What this consolidates
----------------------
=======  ====================================================================
Day 08   models, relationships, transactions, SQL aggregates, N+1 avoidance
Day 09   Alembic migrations instead of ``create_all()``
Day 10   application factory, blueprints, config classes
Day 11   REST conventions: versioned prefix, JSON errors, pagination
Day 12   (schemas — this app keeps ``to_dict`` to stay readable; see exercises)
Day 13   password hashing, sessions, ownership checks, open-redirect defence
=======  ====================================================================

New today
---------
1. **Ownership that spans a chain** — ``User → Project → Task`` — centralised in
   :mod:`taskman.security` so it cannot be *almost* applied.
2. **Enums** in the model, stored portably.
3. **A real test suite** (:mod:`tests`), which Day 17 expands on.

How to run
----------
From the repository root::

    source .venv/bin/activate
    cd 14_project_task_manager

    FLASK_APP=wsgi.py flask db upgrade      # or `flask init-db` for a quick start
    FLASK_APP=wsgi.py flask seed
    FLASK_APP=wsgi.py flask run --port 5014 --debug

    pytest                                   # run the tests
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import click
from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask.cli import with_appcontext

from .config import config_by_name
from .extensions import csrf, db, login_manager, migrate


def create_app(config_name: str | None = None) -> Flask:
    """Build and configure the application.

    Args:
        config_name: ``"development"``, ``"testing"``, ``"production"``, or
            ``None`` to read ``FLASK_CONFIG``.

    Returns:
        Flask: A fully wired application instance.
    """
    app = Flask(__name__, instance_relative_config=True)

    config_name = config_name or os.environ.get("FLASK_CONFIG", "default")
    config_class = config_by_name.get(config_name, config_by_name["default"])
    app.config.from_object(config_class)
    config_class.init_app(app)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)
    csrf.init_app(app)
    login_manager.init_app(app)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please sign in to continue."
    login_manager.login_message_category = "info"

    from .models import Project, Task, User  # noqa: F401

    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        """Reload a user from the session cookie.

        Args:
            user_id: The id as a string.

        Returns:
            User | None: The user, or ``None`` when unknown.
        """
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    @login_manager.unauthorized_handler
    def unauthorized() -> Any:
        """Answer anonymous requests appropriately per audience.

        Returns:
            Any: ``401`` JSON for API paths, a redirect for HTML pages.
        """
        if request.path.startswith("/api/"):
            return jsonify(error={"status": 401, "message": "Sign in required."}), 401
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))

    from .blueprints.api import api_bp
    from .blueprints.auth import auth_bp
    from .blueprints.projects import projects_bp
    from .blueprints.tasks import tasks_bp

    # url_prefix is applied at REGISTRATION (Day 10), so the blueprint itself
    # does not know where it lives.
    app.register_blueprint(projects_bp, url_prefix="/projects")
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(tasks_bp)          # owns /tasks/ and /projects/<id>/tasks
    app.register_blueprint(api_bp)            # owns /api/v1/...

    @app.route("/")
    def home() -> Any:
        """Send the root URL to the project list.

        Returns:
            Any: A redirect. Anonymous visitors are then bounced to the login
            page by ``@login_required`` on the target, so the root works for
            both audiences without duplicating the auth check here.
        """
        return redirect(url_for("projects.index"))

    _register_errors(app)
    _register_template_helpers(app)
    _register_commands(app)

    return app


def _register_errors(app: Flask) -> None:
    """Install error handlers.

    Args:
        app: The application being built.
    """

    @app.errorhandler(404)
    def not_found(error: Exception) -> tuple[str, int]:
        """Render a 404 page.

        Args:
            error: The exception.

        Returns:
            tuple[str, int]: Rendered page and status.
        """
        return render_template("error.html", code=404,
                               message=getattr(error, "description", "Not found.")), 404

    @app.errorhandler(500)
    def server_error(error: Exception) -> tuple[str, int]:
        """Render a 500 page and roll back the session.

        Args:
            error: The exception.

        Returns:
            tuple[str, int]: Rendered page and status.
        """
        db.session.rollback()
        return render_template("error.html", code=500,
                               message="Something went wrong."), 500


def _register_template_helpers(app: Flask) -> None:
    """Register filters and globals used by the templates.

    Args:
        app: The application being built.
    """

    @app.template_filter("nice_date")
    def nice_date(value: date | None) -> str:
        """Render a date, or an em dash when absent.

        Args:
            value: The date to render.

        Returns:
            str: e.g. ``"20 Aug 2026"``.
        """
        return value.strftime("%d %b %Y") if value else "—"

    # -------------------------------------------------------------------------
    # Jinja GLOBALS vs context processors — a distinction that bites
    # -------------------------------------------------------------------------
    # A context processor injects values into the per-request template CONTEXT.
    # A macro is ISOLATED from the caller's context (Day 03 §9, Day 07 §11), so
    # a macro cannot see them and fails with:
    #     UndefinedError: 'TaskStatus' is undefined
    #
    # jinja_env.globals live on the ENVIRONMENT instead, which macros can see.
    # That makes globals the right home for constants and enums used inside
    # macros, and context processors the right home for per-request values.
    #
    # This exact error was hit while writing this example: the `status_buttons`
    # macro loops over TaskStatus.
    from .models import TaskPriority, TaskStatus

    app.jinja_env.globals.update(
        TaskStatus=TaskStatus,
        TaskPriority=TaskPriority,
        app_name="Taskman",
    )

    @app.context_processor
    def inject_request_globals() -> dict[str, Any]:
        """Expose per-request values to every template.

        Returns:
            dict[str, Any]: Values that genuinely change per request. ``today``
            must be computed now, not frozen at start-up — a long-running
            process would otherwise report yesterday's date forever.
        """
        return {"today": date.today()}


def _register_commands(app: Flask) -> None:
    """Attach CLI commands.

    Args:
        app: The application being built.
    """

    @click.command("init-db")
    @with_appcontext
    def init_db() -> None:
        """Create tables directly, bypassing migrations.

        Warning:
            Convenient for a first run and for tests. Once this project has
            migrations, use ``flask db upgrade`` on any database you care
            about — ``create_all()`` cannot evolve an existing schema (Day 09).
        """
        db.create_all()
        click.echo("Tables created.")

    @click.command("seed")
    @with_appcontext
    def seed() -> None:
        """Create a demo account with projects and tasks."""
        from sqlalchemy import select

        from .models import Project, Task, TaskPriority, TaskStatus, User

        db.create_all()

        user = db.session.execute(
            select(User).where(User.email == "ana@example.com")
        ).scalar_one_or_none()
        if user is None:
            user = User(email="ana@example.com", display_name="Ana Rao")
            user.set_password("CorrectHorseBattery1")
            db.session.add(user)
            db.session.flush()

        other = db.session.execute(
            select(User).where(User.email == "vik@example.com")
        ).scalar_one_or_none()
        if other is None:
            other = User(email="vik@example.com", display_name="Vikram Shah")
            other.set_password("CorrectHorseBattery2")
            db.session.add(other)
            db.session.flush()
            db.session.add(Project(name="Vikram's private project",
                                   description="Ana must never see this.",
                                   owner_id=other.id))

        if not user.projects:
            website = Project(name="Website relaunch",
                              description="Ship the new marketing site.",
                              owner_id=user.id)
            api = Project(name="Public API v1",
                          description="Design and document the API.",
                          owner_id=user.id)
            db.session.add_all([website, api])
            db.session.flush()

            today = date.today()
            rows = [
                (website, "Write copy for the home page", TaskStatus.DONE,
                 TaskPriority.HIGH, today - timedelta(days=5)),
                (website, "Design the pricing page", TaskStatus.IN_PROGRESS,
                 TaskPriority.HIGH, today + timedelta(days=3)),
                (website, "Set up analytics", TaskStatus.TODO,
                 TaskPriority.LOW, None),
                (website, "Fix mobile navigation", TaskStatus.BLOCKED,
                 TaskPriority.URGENT, today - timedelta(days=2)),
                (api, "Draft the OpenAPI document", TaskStatus.TODO,
                 TaskPriority.MEDIUM, today + timedelta(days=10)),
                (api, "Add rate limiting", TaskStatus.TODO,
                 TaskPriority.HIGH, today - timedelta(days=1)),
                (api, "Publish the changelog", TaskStatus.DONE,
                 TaskPriority.LOW, None),
            ]
            for project, title, status, priority, due in rows:
                task = Task(title=title, project_id=project.id,
                            priority=priority, due_on=due, assignee_id=user.id)
                task.mark(status)
                db.session.add(task)

        db.session.commit()
        click.echo("Seeded.\n")
        click.echo("Demo logins:")
        click.echo("  ana@example.com  CorrectHorseBattery1")
        click.echo("  vik@example.com  CorrectHorseBattery2")

    app.cli.add_command(init_db)
    app.cli.add_command(seed)


__all__ = ["create_app"]

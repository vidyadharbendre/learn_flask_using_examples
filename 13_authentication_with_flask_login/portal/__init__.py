"""
Day 13 — Authentication with Flask-Login: who are you, and what is yours?
=========================================================================

Real-world scenario
-------------------
A member portal: people register, sign in, and keep private notes. Small enough
to read in one sitting, and complete enough to contain every mistake that turns
a login form into a breach.

Authentication vs authorisation
-------------------------------
============================  ==============================================
**Authentication**            "Who are you?"   → ``@login_required``
**Authorisation**             "May you do this?" → an ownership or role check
============================  ==============================================

They are different questions, and conflating them is the most common serious
access-control bug in web applications. ``@login_required`` on
``/notes/<id>`` proves the visitor is *somebody*; only
``note.user_id == current_user.id`` proves the note is *theirs*.

What you will learn
-------------------
1. **Password hashing** with scrypt — and why fast hashes are disqualified.
2. ``LoginManager``, ``user_loader``, ``login_user`` / ``logout_user``,
   ``current_user``.
3. ``@login_required``, and writing your own ``@admin_required``.
4. **Session fixation** and why login must rotate the session.
5. **Open redirect** via ``?next=`` — and the validator that stops it.
6. **User enumeration** through timing and through error messages.
7. **IDOR**: scoping every query to the current user.
8. "Remember me", account suspension, and password change.

How to run
----------
From the repository root::

    source .venv/bin/activate
    export FLASK_APP=13_authentication_with_flask_login/wsgi.py
    flask seed
    flask run --port 5013 --debug

Demo accounts are printed by ``flask seed``.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import Any

import click
from flask import Flask, jsonify, render_template, request
from flask.cli import with_appcontext

from flask_login import current_user

from .extensions import csrf, db, login_manager


def create_app(config_name: str = "development") -> Flask:
    """Build the member-portal application.

    Args:
        config_name: ``"development"``, ``"testing"`` or ``"production"``.

    Returns:
        Flask: A configured application.
    """
    app = Flask(__name__, instance_relative_config=True)
    instance_dir = Path(app.instance_path)
    instance_dir.mkdir(parents=True, exist_ok=True)

    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-not-for-production"),
        SQLALCHEMY_DATABASE_URI=(
            "sqlite:///:memory:" if config_name == "testing"
            else os.environ.get("DATABASE_URL", f"sqlite:///{instance_dir / 'portal.db'}")
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=config_name == "testing",
        WTF_CSRF_ENABLED=config_name != "testing",

        # ---- Session cookie hardening (Day 06) ------------------------------
        SESSION_COOKIE_HTTPONLY=True,   # JavaScript cannot read it
        SESSION_COOKIE_SAMESITE="Lax",  # browser-level CSRF defence
        SESSION_COOKIE_SECURE=False,    # MUST be True in production (HTTPS)
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),

        # ---- "Remember me" cookie -------------------------------------------
        # A remember-me cookie is a long-lived credential. Give it the same
        # protections as the session cookie, and a deliberately bounded life —
        # "remember me forever" means a stolen laptop is compromised forever.
        REMEMBER_COOKIE_DURATION=timedelta(days=14),
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_SECURE=False,   # True in production
    )

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    # ---- LoginManager configuration -----------------------------------------
    # Where to send anonymous users who hit a @login_required view. Flask-Login
    # appends ?next=<the page they wanted> automatically — which is exactly why
    # auth.is_safe_redirect_url() must exist.
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please sign in to continue."
    login_manager.login_message_category = "info"

    # "strong" mode records a hash of the client's IP and user agent, and drops
    # the session if either changes. It raises the cost of a stolen cookie, at
    # the price of signing out mobile users who switch networks. Know the
    # trade-off you are choosing.
    login_manager.session_protection = "strong"

    from .models import Note, User  # noqa: F401

    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        """Reload a user from the id stored in the session cookie.

        Flask-Login calls this on **every request** for a signed-in user, which
        makes it worth keeping fast — it is a primary-key lookup for a reason.

        Args:
            user_id: The id as a **string**; the cookie stores text, so it must
                be converted. Forgetting the ``int()`` is a classic cause of
                "my user is always None".

        Returns:
            User | None: The user, or ``None`` if the id is unknown or invalid.
            Returning ``None`` makes the request anonymous rather than raising —
            which is what you want when an account was deleted while its cookie
            is still out there.
        """
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    from .blueprints.auth import auth_bp
    from .blueprints.main import main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")

    _register_error_handlers(app)
    _register_commands(app)

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        """Expose shared values to every template.

        Returns:
            dict[str, Any]: Template globals. ``current_user`` is injected by
            Flask-Login itself, so it does not need adding here.
        """
        return {"app_name": "Member Portal"}

    return app


def _register_error_handlers(app: Flask) -> None:
    """Install error handlers.

    Args:
        app: The application being built.
    """

    @app.errorhandler(403)
    def forbidden(error: Exception) -> tuple[str, int]:
        """Render a 403 page.

        Args:
            error: The ``Forbidden`` exception.

        Returns:
            tuple[str, int]: Rendered page and status code.
        """
        return render_template("errors.html", code=403,
                               message="You do not have access to that."), 403

    @app.errorhandler(404)
    def not_found(error: Exception) -> tuple[str, int]:
        """Render a 404 page.

        Args:
            error: The ``NotFound`` exception.

        Returns:
            tuple[str, int]: Rendered page and status code.
        """
        return render_template("errors.html", code=404,
                               message="That page does not exist."), 404

    @login_manager.unauthorized_handler
    def unauthorized() -> Any:
        """Handle an anonymous request to a protected view.

        Returns:
            Any: ``401`` JSON for API paths, otherwise Flask-Login's default
            redirect to the login page.

        Note:
            An API client wants a status code it can branch on, not an HTML
            login form with a ``302``. Branching on the path gives each audience
            the right answer — the app-level version of Day 10 §9.
        """
        if request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json":
            return jsonify(error="unauthorized",
                           message="Sign in to access this resource."), 401
        from flask import redirect, url_for

        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))


def _register_commands(app: Flask) -> None:
    """Attach CLI commands.

    Args:
        app: The application being built.
    """

    @click.command("seed")
    @with_appcontext
    def seed() -> None:
        """Create demo accounts and notes."""
        from sqlalchemy import select

        from .models import Note, User

        db.create_all()

        accounts = [
            ("ana@example.com", "Ana Rao", "member", "CorrectHorseBattery1"),
            ("vik@example.com", "Vikram Shah", "member", "CorrectHorseBattery2"),
            ("admin@example.com", "Admin", "admin", "CorrectHorseBattery3"),
        ]

        created = 0
        for email, name, role, password in accounts:
            existing = db.session.execute(
                select(User).where(User.email == email)
            ).scalar_one_or_none()
            if existing is not None:
                continue
            user = User(email=email, display_name=name, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            db.session.add(Note(
                title=f"{name}'s private note",
                body="Only the owner should ever be able to read this.",
                user_id=user.id,
            ))
            created += 1

        db.session.commit()
        click.echo(f"Created {created} account(s).\n")
        click.echo("Demo logins:")
        for email, _, role, password in accounts:
            click.echo(f"  {email:<22} {password:<24} ({role})")

    @click.command("show-hash")
    @click.argument("password")
    @with_appcontext
    def show_hash(password: str) -> None:
        """Print the hash of PASSWORD twice, to demonstrate salting."""
        from werkzeug.security import check_password_hash, generate_password_hash

        first = generate_password_hash(password)
        second = generate_password_hash(password)
        click.echo(f"\n  hash #1: {first}")
        click.echo(f"  hash #2: {second}")
        click.echo(f"\n  identical? {first == second}   <- salting: same password, different hashes")
        click.echo(f"  #1 verifies? {check_password_hash(first, password)}")
        click.echo(f"  #2 verifies? {check_password_hash(second, password)}")
        click.echo(f"  wrong password? {check_password_hash(first, password + 'x')}\n")

    app.cli.add_command(seed)
    app.cli.add_command(show_hash)


__all__ = ["create_app"]

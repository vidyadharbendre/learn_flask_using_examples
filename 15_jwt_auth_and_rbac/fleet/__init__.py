"""
Day 15 — JWT Authentication and Role-Based Access Control.
==========================================================

Real-world scenario
-------------------
A fleet-management API consumed by a driver's mobile app, a manager's dashboard,
and a nightly reporting job. None of them is a browser on your domain, so
Day 13's session cookie is the wrong tool.

What you will learn
-------------------
1. When tokens beat sessions — and when they do not.
2. **Access + refresh** token pairs, and why one long-lived token is a mistake.
3. **Claims**: putting the role in the token so authorisation needs no query.
4. **Role-based access control** with a reusable decorator.
5. **Revocation**: a blocklist for one token, ``token_version`` for all of them.
6. **Refresh-token rotation** as a theft-detection signal.
7. **Fresh tokens** for dangerous actions.
8. What must never go in a JWT.

How to run
----------
From the repository root::

    source .venv/bin/activate
    export FLASK_APP=15_jwt_auth_and_rbac/wsgi.py
    flask seed
    flask run --port 5015 --debug

The rule to remember
--------------------
**A JWT is signed, not encrypted.** Like Day 06's session cookie, anyone holding
it can read every claim. Put an id and a role in it. Never a password, an API
key, or personal data you would not print in a log.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import click
from flask import Flask, jsonify
from flask.cli import with_appcontext
from flask.typing import ResponseReturnValue

from .auth import register_jwt_callbacks
from .extensions import db, jwt


def create_app(config_name: str = "development") -> Flask:
    """Build the fleet API application.

    Args:
        config_name: ``"development"``, ``"testing"`` or ``"production"``.

    Returns:
        Flask: A configured application.
    """
    app = Flask(__name__, instance_relative_config=True)
    instance_dir = Path(app.instance_path)
    instance_dir.mkdir(parents=True, exist_ok=True)

    app.config.update(
        SQLALCHEMY_DATABASE_URI=(
            "sqlite:///:memory:" if config_name == "testing"
            else os.environ.get("DATABASE_URL", f"sqlite:///{instance_dir / 'fleet.db'}")
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=config_name == "testing",

        # ---- JWT configuration ----------------------------------------------
        # The key that SIGNS every token. Anyone holding it can mint a token for
        # any user with any role — so it is the most sensitive value in the
        # application. Keep it out of source control, and rotate it if leaked
        # (which invalidates every outstanding token, by design).
        JWT_SECRET_KEY=os.environ.get("JWT_SECRET_KEY", "dev-only-not-for-production"),

        # Short access token: it is sent on every request, so it is the one most
        # likely to end up in a log or a proxy. 15 minutes bounds the damage.
        JWT_ACCESS_TOKEN_EXPIRES=timedelta(minutes=15),

        # Long refresh token: travels only to /auth/refresh, so it is exposed
        # far less often.
        JWT_REFRESH_TOKEN_EXPIRES=timedelta(days=30),

        # Tokens arrive in the Authorization header, not a cookie. A header is
        # NOT sent automatically by the browser, which is why a header-based
        # token API needs no CSRF protection — the whole CSRF class of attack
        # depends on the browser attaching credentials for you (Day 04).
        JWT_TOKEN_LOCATION=["headers"],
        JWT_HEADER_NAME="Authorization",
        JWT_HEADER_TYPE="Bearer",

        # Enable the blocklist callback. Without this, revocation is impossible
        # and every issued token works until it expires.
        JWT_BLOCKLIST_ENABLED=True,

        # Tolerate small clock differences between servers. Without any leeway,
        # a machine whose clock is 20 seconds fast rejects tokens that are
        # perfectly valid — a genuinely maddening bug to chase.
        JWT_DECODE_LEEWAY=10,
    )

    db.init_app(app)
    jwt.init_app(app)

    from . import models  # noqa: F401
    from .api import api_bp

    register_jwt_callbacks()
    app.register_blueprint(api_bp)

    @app.get("/")
    def index() -> ResponseReturnValue:
        """Return a discoverable index of the API.

        Returns:
            ResponseReturnValue: Endpoint map and role table.
        """
        return jsonify({
            "service": "fleet-api",
            "version": "v1",
            "auth": {
                "login": "POST /api/v1/auth/login",
                "refresh": "POST /api/v1/auth/refresh",
                "logout": "POST /api/v1/auth/logout",
                "logout_all": "POST /api/v1/auth/logout-all",
                "me": "GET /api/v1/auth/me",
            },
            "roles": {
                "viewer": "read vehicles",
                "driver": "+ update odometer",
                "manager": "+ create vehicles",
                "admin": "+ delete vehicles, change roles",
            },
            "header": "Authorization: Bearer <access_token>",
        })

    @app.errorhandler(404)
    def not_found(error: Exception) -> ResponseReturnValue:
        """Return JSON for unknown routes.

        Args:
            error: The exception.

        Returns:
            ResponseReturnValue: A 404 envelope.
        """
        return jsonify(error={"code": "not_found", "message": "No such endpoint."}), 404

    _register_commands(app)
    return app


def _register_commands(app: Flask) -> None:
    """Attach CLI commands.

    Args:
        app: The application being built.
    """

    @click.command("seed")
    @with_appcontext
    def seed() -> None:
        """Create one account per role, plus some vehicles."""
        from sqlalchemy import select

        from .models import Role, User, Vehicle

        db.create_all()

        accounts = [
            ("viewer@fleet.test", "Val Viewer", Role.VIEWER),
            ("driver@fleet.test", "Dev Driver", Role.DRIVER),
            ("manager@fleet.test", "Mia Manager", Role.MANAGER),
            ("admin@fleet.test", "Ada Admin", Role.ADMIN),
        ]
        for email, name, role in accounts:
            if db.session.execute(
                select(User).where(User.email == email)
            ).scalar_one_or_none() is None:
                user = User(email=email, display_name=name, role=role)
                user.set_password("FleetPassword123")
                db.session.add(user)

        for registration, model in [("KA-01-AB-1234", "Tata Ace"),
                                    ("KA-02-CD-5678", "Mahindra Bolero"),
                                    ("KA-03-EF-9012", "Ashok Leyland Dost")]:
            if db.session.execute(
                select(Vehicle).where(Vehicle.registration == registration)
            ).scalar_one_or_none() is None:
                db.session.add(Vehicle(registration=registration, model=model))

        db.session.commit()
        click.echo("Seeded. Every account uses the password: FleetPassword123\n")
        for email, _, role in accounts:
            click.echo(f"  {email:<24} {role.value}")

    @click.command("decode-token")
    @click.argument("token")
    def decode_token(token: str) -> None:
        """Decode TOKEN **without verifying it**, to prove it is readable.

        This is the exercise that changes how people treat JWTs: anyone holding
        a token can read every claim inside it, without any key at all.
        """
        import base64
        import json

        parts = token.split(".")
        if len(parts) != 3:
            click.echo("That does not look like a JWT (expected three dot-separated parts).")
            return

        def decode(segment: str) -> dict[str, object]:
            """Base64url-decode one JWT segment.

            Args:
                segment: The segment text.

            Returns:
                dict[str, object]: The decoded JSON.
            """
            padded = segment + "=" * (-len(segment) % 4)
            return dict(json.loads(base64.urlsafe_b64decode(padded)))

        click.echo(f"\n  header:    {decode(parts[0])}")
        click.echo(f"  payload:   {decode(parts[1])}")
        click.echo(f"  signature: {parts[2][:24]}… (not verified)")
        click.echo(
            "\n  Note: NO key was needed to read that payload. A JWT is signed,\n"
            "  not encrypted. The signature stops you CHANGING it, not READING it.\n"
        )

    @click.command("prune-tokens")
    @with_appcontext
    def prune_tokens() -> None:
        """Delete blocklist rows for tokens that have already expired.

        An expired token is rejected on its own merits, so keeping it in the
        blocklist buys nothing — and an unbounded blocklist slowly degrades the
        lookup performed on every authenticated request.
        """
        from sqlalchemy import delete

        from .models import RevokedToken

        result = db.session.execute(
            delete(RevokedToken).where(RevokedToken.expires_at < RevokedToken.utcnow())
        )
        db.session.commit()
        click.echo(f"Pruned {result.rowcount} expired blocklist row(s).")

    app.cli.add_command(seed)
    app.cli.add_command(decode_token)
    app.cli.add_command(prune_tokens)


__all__ = ["create_app"]

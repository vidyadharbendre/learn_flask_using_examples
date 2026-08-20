"""
Day 11 — REST API Fundamentals: designing an interface other people can use.
============================================================================

Real-world scenario
-------------------
A bookstore catalogue API consumed by a mobile app, a partner's website, and an
internal reporting job. Three clients you do not control, which is what makes
API *design* — not implementation — the hard part.

What you will learn
-------------------
1. **Resource design**: nouns in the URL, verbs in the HTTP method.
2. **Method semantics**: safe vs idempotent, and why retries depend on it.
3. **Status codes** that mean something: 201 + ``Location``, 204, 409, 415, 422.
4. **One error envelope** for every failure, with stable machine-readable codes.
5. **Pagination** with metadata and links, and why an uncapped page size is a
   denial-of-service invitation.
6. **Filtering and sorting** driven by an allow-list.
7. **Versioning** (``/api/v1``) from the first commit.
8. **Content negotiation**, ``HEAD``, ``OPTIONS`` and CORS.

How to run
----------
From the repository root::

    source .venv/bin/activate
    export FLASK_APP=11_rest_api_fundamentals/wsgi.py
    flask seed
    flask run --port 5011 --debug

Then::

    curl -s http://127.0.0.1:5011/api/v1/ | python -m json.tool

The principle underneath all of it
----------------------------------
**An API is a contract with people you will never meet.** They cannot ask you
what a field means, they will not read your source, and they will retry after a
timeout. Predictability beats cleverness every single time.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import click
from flask import Flask, Response, jsonify, request
from flask.cli import with_appcontext

from .errors import register_error_handlers
from .extensions import db


def create_app(config_name: str = "development") -> Flask:
    """Build the bookstore API application.

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
            else os.environ.get("DATABASE_URL", f"sqlite:///{instance_dir / 'books.db'}")
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=config_name == "testing",
        MAX_CONTENT_LENGTH=512 * 1024,
        # Keep JSON keys in the order we wrote them. Sorted keys are fine too;
        # what matters is that the choice is deliberate and stable, because
        # clients sometimes (wrongly) depend on ordering.
        JSON_SORT_KEYS=False,
    )

    db.init_app(app)

    from . import models  # noqa: F401  - register metadata
    from .api import api_bp

    app.register_blueprint(api_bp)

    # Registered on the APP so the envelope covers unrouted paths too — the
    # limitation Day 10 documented. This whole application is an API, so there
    # is no HTML section to conflict with.
    register_error_handlers(app)

    _register_api_conventions(app)
    _register_commands(app)

    return app


def _register_api_conventions(app: Flask) -> None:
    """Install cross-cutting API behaviour.

    Args:
        app: The application being built.
    """

    @app.before_request
    def require_json_accept() -> Response | None:
        """Reject clients that cannot accept JSON.

        Returns:
            Response | None: ``406 Not Acceptable`` when the client explicitly
            asked for something we cannot produce; ``None`` to continue.

        Note:
            Most clients send ``Accept: */*`` or nothing at all, both of which
            are fine. Only a client that names a specific type we do not serve
            gets 406 — being stricter than that breaks ``curl`` defaults and
            annoys everybody.
        """
        accept = request.accept_mimetypes
        if not accept or accept.best_match(["application/json"]) is not None:
            return None
        if accept.provided and "*/*" not in str(accept):
            from .errors import APIError

            return APIError(
                406, "not_acceptable", "This API only produces application/json."
            ).to_response()[0]
        return None

    @app.after_request
    def add_api_headers(response: Response) -> Response:
        """Attach headers every API response should carry.

        Args:
            response: The outgoing response.

        Returns:
            Response: The same response, with headers added.

        Note:
            ``X-Content-Type-Options: nosniff`` stops a browser guessing the
            content type and treating a JSON body as HTML — a real XSS vector
            when your API echoes user input.

            The CORS header is deliberately permissive here **for learning**. In
            production, name the exact origins you trust; ``*`` means any
            website can read responses from your API in a user's browser. Use
            ``flask-cors`` rather than hand-rolling the full protocol, which
            includes preflight ``OPTIONS`` requests and credential rules.
        """
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Access-Control-Allow-Origin", "*")
        response.headers.setdefault("X-API-Version", "v1")
        return response


def _register_commands(app: Flask) -> None:
    """Attach CLI commands.

    Args:
        app: The application being built.
    """

    @click.command("init-db")
    @with_appcontext
    def init_db() -> None:
        """Create all tables."""
        db.create_all()
        click.echo("Tables created.")

    @click.command("seed")
    @with_appcontext
    def seed() -> None:
        """Insert demo authors and books."""
        from sqlalchemy import select

        from .models import Author, Book

        db.create_all()

        catalogue = {
            "Ursula K. Le Guin": [
                ("9780441478125", "The Left Hand of Darkness", "499.00", 12, 1969),
                ("9780060512750", "A Wizard of Earthsea", "425.00", 7, 1968),
            ],
            "Kazuo Ishiguro": [
                ("9780571258093", "Never Let Me Go", "550.00", 4, 2005),
                ("9780571200733", "The Remains of the Day", "475.00", 9, 1989),
            ],
            "Arundhati Roy": [
                ("9780679457312", "The God of Small Things", "399.00", 15, 1997),
            ],
            "Ted Chiang": [
                ("9781101972120", "Exhalation", "650.00", 6, 2019),
                ("9781931520904", "Stories of Your Life and Others", "599.00", 3, 2002),
            ],
        }

        created = 0
        for author_name, books in catalogue.items():
            author = db.session.execute(
                select(Author).where(Author.name == author_name)
            ).scalar_one_or_none()
            if author is None:
                author = Author(name=author_name)
                db.session.add(author)
                db.session.flush()

            for isbn, title, price, stock, year in books:
                exists = db.session.execute(
                    select(Book).where(Book.isbn == isbn)
                ).scalar_one_or_none()
                if exists is None:
                    db.session.add(Book(
                        isbn=isbn, title=title, price=Decimal(price),
                        stock=stock, published_year=year, author_id=author.id,
                    ))
                    created += 1

        db.session.commit()
        click.echo(f"Seeded {created} book(s) across {len(catalogue)} authors.")

    app.cli.add_command(init_db)
    app.cli.add_command(seed)


__all__ = ["create_app"]

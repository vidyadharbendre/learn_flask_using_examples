"""
Day 12 — Pydantic Validation and Schemas: a typed boundary for your API.
=========================================================================

Real-world scenario
-------------------
The same bookstore catalogue as Day 11, with the hand-written validation torn
out and replaced by declarative Pydantic models.

Why today matters
-----------------
Day 11's ``_validate_book()`` was ninety lines of ``isinstance`` checks — and it
still had a bug, because ``isinstance(True, int)`` is ``True`` in Python. That
function would have to be rewritten for every resource you add.

Pydantic turns validation into a **declaration**. You describe the shape you
accept; it parses, coerces, validates, reports every failure in a structured
form, and generates a JSON Schema you can serve as documentation.

What you will learn
-------------------
1. ``BaseModel``, typed fields, and ``Field`` constraints.
2. ``model_validate`` / ``model_dump`` — parsing in, serialising out.
3. ``field_validator`` and ``model_validator`` for real logic and cross-field
   rules.
4. **Separate Create / Update / Out schemas**, and the mass-assignment bug that
   sharing one model causes.
5. ``exclude_unset`` — the flag that makes ``PATCH`` correct.
6. ``from_attributes`` for reading SQLAlchemy objects directly.
7. ``computed_field`` and ``field_serializer``.
8. **Strict types**, and why ``StrictInt`` beats an ``isinstance(x, bool)``
   check.
9. Generating JSON Schema for free — documentation that cannot drift.

How to run
----------
From the repository root::

    source .venv/bin/activate
    export FLASK_APP=12_pydantic_validation_and_schemas/wsgi.py
    flask seed
    flask run --port 5012 --debug

Then::

    curl -s http://127.0.0.1:5012/api/v1/schema | python -m json.tool
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import click
from flask import Flask, Response, jsonify
from flask.cli import with_appcontext

from flask.typing import ResponseReturnValue
from sqlalchemy.exc import OperationalError
from .errors import register_error_handlers
from .extensions import db


def create_app(config_name: str = "development") -> Flask:
    """Build the catalogue API application.

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
            else os.environ.get("DATABASE_URL", f"sqlite:///{instance_dir / 'catalogue.db'}")
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=config_name == "testing",
        MAX_CONTENT_LENGTH=512 * 1024,
    )

    db.init_app(app)

    from . import models  # noqa: F401
    from .api import api_bp

    app.register_blueprint(api_bp)
    register_error_handlers(app)

    @app.after_request
    def add_headers(response: Response) -> Response:
        """Attach standard API headers.

        Args:
            response: The outgoing response.

        Returns:
            Response: The same response with headers added.
        """
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-API-Version", "v1")
        return response

    @click.command("seed")
    @with_appcontext
    def seed() -> None:
        """Insert demo authors and books."""
        from sqlalchemy import select

        from .models import Author, Book

        db.create_all()
        catalogue = {
            "Ursula K. Le Guin": [
                ("9780441478125", "The Left Hand of Darkness", "499.00", 12, 1969,
                 "scifi,classic"),
                ("9780060512750", "A Wizard of Earthsea", "425.00", 7, 1968,
                 "fantasy,classic"),
            ],
            "Ted Chiang": [
                ("9781101972120", "Exhalation", "650.00", 6, 2019, "scifi,short-stories"),
                ("9781931520904", "Stories of Your Life", "599.00", 0, 2002, "scifi"),
            ],
            "Arundhati Roy": [
                ("9780679457312", "The God of Small Things", "399.00", 15, 1997,
                 "literary,booker"),
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

            for isbn, title, price, stock, year, tags in books:
                exists = db.session.execute(
                    select(Book).where(Book.isbn == isbn)
                ).scalar_one_or_none()
                if exists is None:
                    db.session.add(Book(
                        isbn=isbn, title=title, price=Decimal(price), stock=stock,
                        published_year=year, tags=tags, author_id=author.id,
                    ))
                    created += 1

        db.session.commit()
        click.echo(f"Seeded {created} book(s).")

    app.cli.add_command(seed)

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
            return jsonify(error={"code": "database_error", "message": "A database error occurred."}), 500

        return jsonify(error={
            "code": "database_not_initialised",
            "message": "The database tables do not exist yet.",
            "fix": "cd 12_pydantic_validation_and_schemas && FLASK_APP=wsgi.py flask seed",
            "see": "This day's README, section 3.",
        }), 503

    return app


__all__ = ["create_app"]

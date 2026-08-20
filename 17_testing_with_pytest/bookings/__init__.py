"""
Day 17 — Testing with pytest.
=============================

Real-world scenario
-------------------
A meeting-room booking system. The application is deliberately small so that the
**test suite** is the subject: ``tests/`` is where today's lesson lives.

What you will learn
-------------------
1. **Fixtures**: what they are, how they compose, and how scope affects speed.
2. **Test isolation**, and the two strategies for a database.
3. ``@pytest.mark.parametrize`` for tables of cases.
4. Testing **pure functions** first, HTTP last — the test pyramid, concretely.
5. **Mocking** external HTTP calls, and injecting the clock.
6. **Markers** for slow tests, and ``-k`` for selecting them.
7. What **coverage** tells you — and what it does not.
8. What **not** to test.

How to run
----------
From the repository root::

    source .venv/bin/activate
    cd 17_testing_with_pytest

    pytest                      # everything
    pytest -v                   # one line per test
    pytest tests/test_overlap.py -v
    pytest -k "conflict"        # select by name
    pytest -m "not slow"        # skip slow tests
    pytest --cov=bookings --cov-report=term-missing

    FLASK_APP=wsgi.py flask seed
    FLASK_APP=wsgi.py flask run --port 5017 --debug
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import click
from flask import Flask, jsonify, request
from flask.cli import with_appcontext
from flask.typing import ResponseReturnValue
from sqlalchemy.exc import OperationalError
from sqlalchemy import select

from .extensions import db


def create_app(config_name: str = "development") -> Flask:
    """Build the booking application.

    Args:
        config_name: ``"development"`` or ``"testing"``.

    Returns:
        Flask: A configured application.
    """
    app = Flask(__name__, instance_relative_config=True)
    instance_dir = Path(app.instance_path)
    instance_dir.mkdir(parents=True, exist_ok=True)

    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only"),
        SQLALCHEMY_DATABASE_URI=(
            "sqlite:///:memory:" if config_name == "testing"
            else f"sqlite:///{instance_dir / 'bookings.db'}"
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=config_name == "testing",
    )

    db.init_app(app)

    from . import models  # noqa: F401
    from .models import Booking, Room
    from .services import create_booking, fetch_public_holidays

    @app.get("/api/rooms")
    def list_rooms() -> ResponseReturnValue:
        """List every room.

        Returns:
            ResponseReturnValue: ``200`` with the rooms.
        """
        rooms = db.session.execute(select(Room).order_by(Room.name)).scalars().all()
        return jsonify(data=[room.to_dict() for room in rooms])

    @app.get("/api/bookings")
    def list_bookings() -> ResponseReturnValue:
        """List bookings, optionally filtered by room.

        Returns:
            ResponseReturnValue: ``200`` with the bookings.
        """
        statement = select(Booking).order_by(Booking.starts_at)
        if room_id := request.args.get("room_id", type=int):
            statement = statement.where(Booking.room_id == room_id)
        rows = db.session.execute(statement).scalars().all()
        return jsonify(data=[booking.to_dict() for booking in rows])

    @app.post("/api/bookings")
    def make_booking() -> ResponseReturnValue:
        """Create a booking.

        Returns:
            ResponseReturnValue: ``201`` on success, ``409`` on a clash,
            ``422`` for any other rule violation.

        Note:
            The view is thin on purpose: parse, delegate, choose a status code.
            Every rule lives in :mod:`bookings.services`, where it can be tested
            without an HTTP client.
        """
        body = request.get_json(silent=True) or {}

        try:
            starts_at = datetime.fromisoformat(str(body.get("starts_at", "")))
            ends_at = datetime.fromisoformat(str(body.get("ends_at", "")))
        except ValueError:
            return jsonify(error="starts_at and ends_at must be ISO 8601 datetimes."), 422

        result = create_booking(
            room_id=int(body.get("room_id", 0)),
            booked_by=str(body.get("booked_by", "")).strip() or "anonymous",
            attendees=int(body.get("attendees", 1)),
            starts_at=starts_at,
            ends_at=ends_at,
        )

        if not result.ok:
            # 409 for a clash with existing state; 422 for an invalid request.
            # Day 11's distinction, applied.
            status = 409 if "already booked" in result.error else 422
            return jsonify(error=result.error), status

        assert result.booking is not None  # narrowed by result.ok
        return jsonify(result.booking.to_dict()), 201

    @app.get("/api/holidays/<int:year>")
    def holidays(year: int) -> ResponseReturnValue:
        """Return public holidays for a year.

        Args:
            year: The calendar year.

        Returns:
            ResponseReturnValue: ``200`` with a (possibly empty) list.
        """
        return jsonify(year=year, dates=fetch_public_holidays(year))

    @app.get("/health")
    def health() -> ResponseReturnValue:
        """Liveness probe.

        Returns:
            ResponseReturnValue: ``200``.
        """
        return jsonify(status="ok", service="bookings")

    @click.command("seed")
    @with_appcontext
    def seed() -> None:
        """Create demo rooms and one booking."""
        from .models import utcnow

        db.create_all()

        for name, capacity in [("Alpha", 4), ("Beta", 8), ("Gamma", 12)]:
            if db.session.execute(
                select(Room).where(Room.name == name)
            ).scalar_one_or_none() is None:
                db.session.add(Room(name=name, capacity=capacity))
        db.session.commit()

        room = db.session.execute(select(Room)).scalars().first()
        if room is not None and not room.bookings:
            start = utcnow() + timedelta(days=1)
            db.session.add(Booking(
                room_id=room.id, booked_by="Ana Rao", attendees=3,
                starts_at=start, ends_at=start + timedelta(hours=1),
            ))
            db.session.commit()

        click.echo("Seeded 3 rooms.")

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
            "fix": "cd 17_testing_with_pytest && FLASK_APP=wsgi.py flask seed",
            "see": "This day's README, section 3.",
        }), 503

    return app


__all__ = ["create_app"]

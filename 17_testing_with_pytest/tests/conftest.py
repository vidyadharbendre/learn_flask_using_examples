"""
Day 17 — Fixtures.
==================

What a fixture is
-----------------
A function that **prepares something a test needs**, requested by writing its
name as a test parameter. pytest resolves the whole dependency graph for you:

    def test_x(client): ...      # pytest sees `client`, builds it, injects it
    client → app                 # `client` itself asks for `app`

Anything before ``yield`` is set-up; anything after is tear-down, and it runs
even when the test fails.

Scope, and why it matters
-------------------------
============  ====================================  ==========================
``function``  rebuilt for **every test** (default)  slowest, safest
``module``    once per test file                    shared state within a file
``session``   once per whole run                    fastest, riskiest
============  ====================================  ==========================

**Default to ``function``.** A suite where tests can affect one another produces
failures that depend on execution order, and those cost hours. Widen the scope
only for something genuinely expensive *and* genuinely read-only.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask
from flask.testing import FlaskClient

from bookings import create_app
from bookings.extensions import db as _db
from bookings.models import Booking, Room

# A FIXED point in time. Every time-dependent test is anchored here rather than
# to `datetime.now()`, so the suite behaves identically today, next year, and on
# a machine whose clock is wrong. A test that passes only in November is not a
# test, it is a time bomb.
FROZEN_NOW = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def app() -> Iterator[Flask]:
    """Provide a fresh application with an empty in-memory database.

    Yields:
        Flask: A configured testing application.

    Note:
        Function-scoped, so each test gets its own database. The alternative —
        a session-scoped app wrapped in a transaction that is rolled back per
        test — is faster and is what large suites do. It is also fiddlier: code
        that commits (as ``create_booking`` does) needs nested transactions.
        Start simple; optimise when the suite is actually slow.
    """
    application = create_app("testing")
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    """Provide a test client.

    Args:
        app: The application fixture.

    Returns:
        FlaskClient: A client that issues requests with no network or server.

    Note:
        ``app.test_client()`` calls your WSGI application **directly**. There is
        no socket, no port, and nothing to start or stop — which is why a
        thousand of these run in seconds.
    """
    return app.test_client()


@pytest.fixture
def rooms(app: Flask) -> list[Room]:
    """Create three rooms of differing capacity.

    Args:
        app: The application fixture (for the app context).

    Returns:
        list[Room]: The persisted rooms.
    """
    created = [Room(name="Alpha", capacity=4),
               Room(name="Beta", capacity=8),
               Room(name="Gamma", capacity=12)]
    _db.session.add_all(created)
    _db.session.commit()
    return created


@pytest.fixture
def room(rooms: list[Room]) -> Room:
    """Return the small room.

    Args:
        rooms: The rooms fixture.

    Returns:
        Room: "Alpha", capacity 4.

    Note:
        A fixture depending on another fixture. Building small, composable
        fixtures beats one big ``setup_everything`` — each test then asks for
        exactly what it needs, and reading the signature tells you the
        preconditions.
    """
    return rooms[0]


@pytest.fixture
def booked_room(room: Room) -> Room:
    """Return a room that already has a 10:00-11:00 booking.

    Args:
        room: The room fixture.

    Returns:
        Room: The room, with one existing booking.
    """
    _db.session.add(Booking(
        room_id=room.id, booked_by="Existing Booker", attendees=2,
        starts_at=FROZEN_NOW.replace(hour=10),
        ends_at=FROZEN_NOW.replace(hour=11),
    ))
    _db.session.commit()
    return room


@pytest.fixture
def at() -> "TimeHelper":
    """Provide a helper for building times relative to :data:`FROZEN_NOW`.

    Returns:
        TimeHelper: Callable producing anchored datetimes.
    """
    return TimeHelper()


class TimeHelper:
    """Builds datetimes anchored to :data:`FROZEN_NOW`.

    Keeps tests readable — ``at(10)`` rather than
    ``datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)`` — and keeps the anchor
    in one place.
    """

    now = FROZEN_NOW

    def __call__(self, hour: float, minute: int = 0) -> datetime:
        """Return a time on the frozen day.

        Args:
            hour: Hour of the day; may be fractional, and may exceed 23 to
                reach the following day.
            minute: Minute of the hour.

        Returns:
            datetime: An aware UTC datetime.
        """
        return FROZEN_NOW.replace(hour=0, minute=0) + timedelta(hours=hour, minutes=minute)

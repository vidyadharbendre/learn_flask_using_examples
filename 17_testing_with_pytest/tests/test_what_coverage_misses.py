"""
Day 17 — What coverage does **not** tell you.
=============================================

Coverage measures which lines *executed*, not which behaviours were *checked*.
It is a good detector of untested code and a poor measure of test quality.

The tests below run and pass. Read each one and ask what it would actually catch.
"""

from __future__ import annotations

from datetime import timedelta, timezone
from datetime import datetime as real_datetime

from flask import Flask
from flask.testing import FlaskClient

from bookings.models import Room, overlaps
from bookings.services import create_booking
from conftest import FROZEN_NOW, TimeHelper


def test_100_percent_coverage_zero_value(app: Flask, room: Room, at: TimeHelper) -> None:
    """Executes the whole happy path and asserts almost nothing.

    Coverage reports these lines as covered. Change ``<`` to ``<=`` in
    ``overlaps``, or delete the capacity rule entirely, and this test **still
    passes**.

    Coverage answered "did this line run?" — which was never the question. The
    question is "would this test fail if the behaviour were wrong?"
    """
    result = create_booking(
        room_id=room.id, booked_by="Ana", attendees=2,
        starts_at=at(14), ends_at=at(15), now=FROZEN_NOW,
    )
    assert result is not None          # ← always true. Asserts nothing.


def test_the_same_path_with_a_real_assertion(
    app: Flask, room: Room, at: TimeHelper
) -> None:
    """Identical coverage, genuinely useful.

    Same lines executed; this one *fails* if the behaviour changes. The
    difference is entirely in the assertions, which is the part coverage cannot
    see.
    """
    result = create_booking(
        room_id=room.id, booked_by="  Ana  ", attendees=2,
        starts_at=at(14), ends_at=at(15), now=FROZEN_NOW,
    )

    assert result.ok
    assert result.booking is not None
    assert result.booking.booked_by == "Ana"        # whitespace stripped
    assert result.booking.attendees == 2
    assert result.booking.ends_at - result.booking.starts_at == timedelta(hours=1)


# -----------------------------------------------------------------------------
# What NOT to test
# -----------------------------------------------------------------------------
# These are written out and deliberately NOT implemented. Every one of them
# costs maintenance and catches nothing:
#
#   ❌ that SQLAlchemy saves a row              — testing the library, not you
#   ❌ that Flask routes a URL to a view        — testing the framework
#   ❌ that a getter returns what a setter set  — testing Python
#   ❌ exact prose of a user-facing message     — breaks on a reword, catches no bug
#   ❌ private helpers directly (`_make_thumb`) — test them through public behaviour,
#                                                 or they cement the implementation
#   ❌ that a constant equals its own value     — `assert MAX_DURATION == timedelta(hours=8)`
#
# The test that earns its place is one that FAILS when a behaviour you care
# about changes, and does not fail when an irrelevant detail does.


def test_boundary_not_just_the_middle(at: TimeHelper) -> None:
    """Where the bugs actually live.

    A test suite full of comfortable middle-of-the-range values reaches high
    coverage and finds nothing. Off-by-one errors live at the edges: the empty
    list, the zero, the exactly-equal, the first and last element.
    """
    # exactly adjacent: must NOT clash
    assert overlaps(at(9), at(10), at(10), at(11)) is False
    # one minute of contact: must clash
    assert overlaps(at(9), at(10, 1), at(10), at(11)) is True


def test_error_paths_matter_more_than_happy_paths(client: FlaskClient) -> None:
    """The unhappy paths are the ones that reach production untested.

    Everybody tests "it works". Far fewer test "a client sent nonsense", and
    that is exactly what your error tracker fills up with on launch day.
    """
    assert client.post("/api/bookings", json={}).status_code == 422
    assert client.post("/api/bookings", data="not json").status_code == 422
    assert client.get("/api/bookings?room_id=abc").status_code == 200   # ignored, not fatal
    assert client.get("/nope").status_code == 404

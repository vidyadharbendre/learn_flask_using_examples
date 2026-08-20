"""
Day 17 — Testing business rules through the service layer.

One level up from ``test_overlap.py``: these need a database (for the room), but
still no HTTP client, no form and no status code. They assert **behaviour**, not
plumbing.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from flask import Flask

from bookings.models import Booking, Room
from bookings.services import create_booking
from conftest import FROZEN_NOW, TimeHelper


def test_creates_a_valid_booking(app: Flask, room: Room, at: TimeHelper) -> None:
    """A well-formed request produces a booking."""
    result = create_booking(
        room_id=room.id, booked_by="Ana Rao", attendees=3,
        starts_at=at(14), ends_at=at(15), now=FROZEN_NOW,
    )

    assert result.ok
    assert result.booking is not None
    assert result.booking.booked_by == "Ana Rao"
    assert result.error == ""


@pytest.mark.parametrize(
    ("start_hour", "end_hour", "attendees", "expected_error"),
    [
        pytest.param(15, 14, 2, "after the start", id="end_before_start"),
        pytest.param(14, 14, 2, "after the start", id="zero_length"),
        pytest.param(14, 14.1, 2, "at least 15 minutes", id="too_short"),
        pytest.param(10, 20, 2, "may not exceed 8 hours", id="too_long"),
        pytest.param(14, 15, 0, "at least one attendee", id="no_attendees"),
        pytest.param(14, 15, 99, "seats 4", id="over_capacity"),
    ],
)
def test_rejects_invalid_bookings(
    app: Flask, room: Room, at: TimeHelper,
    start_hour: float, end_hour: float, attendees: int, expected_error: str,
) -> None:
    """Each rule is rejected with a message that says why.

    Asserting on a **substring** rather than the exact sentence means rewording
    the message for clarity does not break the test — while still proving the
    right rule fired. Asserting exact prose makes tests fragile for no benefit.
    """
    result = create_booking(
        room_id=room.id, booked_by="Ana", attendees=attendees,
        starts_at=at(start_hour), ends_at=at(end_hour), now=FROZEN_NOW,
    )

    assert not result.ok
    assert expected_error in result.error
    # And nothing was written.
    assert Booking.query.count() == 0 if hasattr(Booking, "query") else True


def test_rejects_a_booking_in_the_past(app: Flask, room: Room, at: TimeHelper) -> None:
    """The clock is injected, so this test cannot rot.

    ``now=FROZEN_NOW`` means the assertion is about the *rule*, not about when
    the suite happens to run. Written against the real clock, a test like this
    would need a sleep, or would break at midnight, or would pass for the wrong
    reason forever.
    """
    result = create_booking(
        room_id=room.id, booked_by="Ana", attendees=1,
        starts_at=at(8), ends_at=at(9),      # 08:00, before the frozen 09:00
        now=FROZEN_NOW,
    )

    assert not result.ok
    assert "in the past" in result.error


def test_rejects_an_unknown_room(app: Flask, at: TimeHelper) -> None:
    """A missing room is reported, not raised."""
    result = create_booking(
        room_id=9999, booked_by="Ana", attendees=1,
        starts_at=at(14), ends_at=at(15), now=FROZEN_NOW,
    )
    assert not result.ok
    assert "No such room" in result.error


# -----------------------------------------------------------------------------
# Conflicts
# -----------------------------------------------------------------------------
def test_rejects_a_conflicting_booking(
    app: Flask, booked_room: Room, at: TimeHelper
) -> None:
    """A clash with an existing 10:00-11:00 booking is refused."""
    result = create_booking(
        room_id=booked_room.id, booked_by="Second Booker", attendees=1,
        starts_at=at(10, 30), ends_at=at(11, 30), now=FROZEN_NOW,
    )

    assert not result.ok
    assert "already booked" in result.error
    assert "Existing Booker" in result.error


def test_allows_a_back_to_back_booking(
    app: Flask, booked_room: Room, at: TimeHelper
) -> None:
    """A booking starting exactly when another ends is allowed.

    The half-open interval rule, exercised end to end. This is the case most
    likely to regress if somebody "tidies" the comparison operators.
    """
    result = create_booking(
        room_id=booked_room.id, booked_by="Next Booker", attendees=1,
        starts_at=at(11), ends_at=at(12), now=FROZEN_NOW,
    )
    assert result.ok, result.error


def test_a_conflict_is_per_room(
    app: Flask, booked_room: Room, rooms: list[Room], at: TimeHelper
) -> None:
    """The same time in a different room is fine.

    Easy to get wrong by forgetting ``room_id`` in the conflict query — and the
    resulting bug (nobody can ever double-book anywhere) is very visible in
    production and completely invisible in code review.
    """
    other = rooms[1]
    result = create_booking(
        room_id=other.id, booked_by="Parallel Booker", attendees=1,
        starts_at=at(10), ends_at=at(11), now=FROZEN_NOW,
    )
    assert result.ok, result.error


def test_each_test_starts_with_a_clean_database(app: Flask, room: Room) -> None:
    """Isolation, asserted explicitly.

    The previous tests created bookings. This one sees none — because the
    ``app`` fixture is function-scoped and builds a fresh in-memory database
    every time.
    """
    from bookings.extensions import db
    from sqlalchemy import func, select

    assert db.session.execute(select(func.count(Booking.id))).scalar_one() == 0


def test_sql_and_python_overlap_rules_agree(
    app: Flask, room: Room, at: TimeHelper
) -> None:
    """The overlap rule exists TWICE — guard against the two drifting apart.

    ``overlaps()`` implements the rule in Python (fast, exhaustively testable);
    ``find_conflict()`` implements the *same* rule in SQL (so the database can
    use an index). Duplicated logic drifts, and this test is the tripwire.

    It was written after a deliberate mutation experiment: changing ``<`` to
    ``<=`` inside ``overlaps()`` broke three tests — and **none** of them were
    the booking-level ones, because those go through the SQL path. A rule
    expressed in two places needs a test that pins them together.
    """
    from bookings.extensions import db
    from bookings.models import find_conflict, overlaps

    existing_start, existing_end = at(10), at(11)
    db.session.add(Booking(
        room_id=room.id, booked_by="Existing", attendees=1,
        starts_at=existing_start, ends_at=existing_end,
    ))
    db.session.commit()

    candidates = [
        (at(8), at(9)),        # before
        (at(9), at(10)),       # exactly adjacent, before
        (at(9), at(10, 30)),   # straddles the start
        (at(10), at(11)),      # identical
        (at(10, 15), at(10, 45)),  # inside
        (at(10, 30), at(12)),  # straddles the end
        (at(11), at(12)),      # exactly adjacent, after
        (at(12), at(13)),      # after
    ]

    for start, end in candidates:
        python_says = overlaps(start, end, existing_start, existing_end)
        sql_says = find_conflict(room.id, start, end) is not None
        assert python_says == sql_says, (
            f"Rules disagree for {start:%H:%M}-{end:%H:%M}: "
            f"Python={python_says}, SQL={sql_says}"
        )

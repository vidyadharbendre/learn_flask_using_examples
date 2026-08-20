"""
Day 17 — Business logic, deliberately separated from HTTP.
==========================================================

Everything here is testable **without a request**. That separation is not
architectural purity for its own sake — it is what makes a fast, focused test
suite possible. A rule tested through an HTTP client needs an app, a client, a
session, a form and a status code before it can assert one boolean.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import requests

from .extensions import db
from .models import Booking, Room, find_conflict, utcnow

MAX_DURATION = timedelta(hours=8)
MIN_DURATION = timedelta(minutes=15)


@dataclass
class BookingResult:
    """Outcome of attempting to create a booking.

    Attributes:
        booking: The created booking, when successful.
        error: A message safe to show the user, when not.

    Note:
        Returning a **result object** rather than raising keeps the caller in
        control of how failure is presented — a 422 for an API, a flash message
        for HTML. It also makes the function trivial to test: call it, inspect
        the result, no exception handling needed.
    """

    booking: Booking | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        """Whether the booking succeeded.

        Returns:
            bool: True when a booking was created.
        """
        return self.booking is not None


def create_booking(
    *, room_id: int, booked_by: str, attendees: int,
    starts_at: datetime, ends_at: datetime, now: datetime | None = None,
) -> BookingResult:
    """Validate and create a booking.

    Args:
        room_id: The room to book.
        booked_by: Who is booking.
        attendees: Expected head count.
        starts_at: Start of the interval.
        ends_at: End of the interval (exclusive).
        now: The current time. **Injected** so tests need not wait or patch a
            global clock; defaults to the real one.

    Returns:
        BookingResult: The created booking, or an error message.

    Note:
        ``now`` as a parameter is the cheapest form of dependency injection, and
        it removes a whole class of flaky test. A test asserting "a booking in
        the past is rejected" should not depend on when the suite runs, and a
        test written on 31 December should still pass in January.
    """
    now = now or utcnow()

    room = db.session.get(Room, room_id)
    if room is None:
        return BookingResult(error="No such room.")

    if ends_at <= starts_at:
        return BookingResult(error="The end time must be after the start time.")

    duration = ends_at - starts_at
    if duration < MIN_DURATION:
        return BookingResult(error="Bookings must be at least 15 minutes.")
    if duration > MAX_DURATION:
        return BookingResult(error="Bookings may not exceed 8 hours.")

    if starts_at < now:
        return BookingResult(error="You cannot book a room in the past.")

    if attendees < 1:
        return BookingResult(error="A booking needs at least one attendee.")
    if attendees > room.capacity:
        return BookingResult(
            error=f"{room.name} seats {room.capacity}; you asked for {attendees}."
        )

    conflict = find_conflict(room_id, starts_at, ends_at)
    if conflict is not None:
        return BookingResult(
            error=f"{room.name} is already booked by {conflict.booked_by} "
                  f"until {conflict.ends_at:%H:%M}."
        )

    booking = Booking(
        room_id=room_id, booked_by=booked_by.strip(), attendees=attendees,
        starts_at=starts_at, ends_at=ends_at,
    )
    db.session.add(booking)
    db.session.commit()
    return BookingResult(booking=booking)


def fetch_public_holidays(year: int, country: str = "IN") -> list[str]:
    """Fetch public holiday dates from a third-party API.

    Args:
        year: The calendar year.
        country: ISO country code.

    Returns:
        list[str]: ISO date strings, or ``[]`` when the service is unavailable.

    Note:
        This exists to be **mocked**. Three rules for external calls:

        1. **Always set a timeout.** ``requests`` has *no* default timeout, so a
           hung third party will hang your worker until something kills it. This
           is one of the most common causes of a production outage that has
           nothing to do with your own code.
        2. **Degrade, do not crash.** A holiday calendar is a nicety; the
           booking system must keep working without it.
        3. **Never let a test hit the network.** A test that calls a real API is
           slow, fails when you are offline, fails when the third party has an
           incident, and may cost money. See ``tests/test_holidays.py``.
    """
    try:
        response = requests.get(
            f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country}",
            timeout=3,  # ← never omit this
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return []

    return [item["date"] for item in payload if isinstance(item, dict) and "date" in item]

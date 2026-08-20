"""
Day 17 — Models for a meeting-room booking system.

Deliberately small, with **one genuinely tricky rule** — overlap detection —
because that is the kind of logic where tests earn their keep.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func, select
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .extensions import db


class Room(db.Model):
    """A bookable meeting room.

    Attributes:
        id: Surrogate primary key.
        name: Unique display name.
        capacity: Maximum occupants.
        bookings: Bookings for this room.
    """

    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    capacity: Mapped[int] = mapped_column(nullable=False, default=4)

    bookings: Mapped[list["Booking"]] = relationship(
        back_populates="room", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API.

        Returns:
            dict[str, Any]: JSON-safe representation.
        """
        return {"id": self.id, "name": self.name, "capacity": self.capacity}


class Booking(db.Model):
    """A reservation of a room for a time range.

    Attributes:
        id: Surrogate primary key.
        room_id: The booked room.
        room: The related room.
        booked_by: Who made the booking.
        attendees: Expected head count.
        starts_at / ends_at: Half-open interval ``[starts_at, ends_at)``.
        created_at: Timestamp.
    """

    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    room: Mapped["Room"] = relationship(back_populates="bookings")

    booked_by: Mapped[str] = mapped_column(String(120), nullable=False)
    attendees: Mapped[int] = mapped_column(nullable=False, default=1)

    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API.

        Returns:
            dict[str, Any]: JSON-safe representation.
        """
        return {
            "id": self.id,
            "room": self.room.name if self.room else None,
            "booked_by": self.booked_by,
            "attendees": self.attendees,
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
        }


def overlaps(start_a: datetime, end_a: datetime,
             start_b: datetime, end_b: datetime) -> bool:
    """Return whether two half-open intervals overlap.

    Args:
        start_a: Start of the first interval (inclusive).
        end_a: End of the first interval (exclusive).
        start_b: Start of the second interval (inclusive).
        end_b: End of the second interval (exclusive).

    Returns:
        bool: True when the intervals share any instant.

    Note:
        Intervals here are **half-open**: ``[start, end)``. A booking ending at
        10:00 and one starting at 10:00 do **not** clash — which is what people
        expect of back-to-back meetings, and is why the comparisons are strict.

        The condition is the classic one, and it is much easier to get right
        than the four-case version people usually reach for first::

            they overlap  ⟺  a starts before b ends  AND  b starts before a ends

        This is a **pure function** — no database, no request, no clock. That is
        exactly why it can be tested exhaustively in microseconds, and it is the
        single most valuable habit in this whole day.
    """
    return start_a < end_b and start_b < end_a


def find_conflict(room_id: int, starts_at: datetime, ends_at: datetime,
                  exclude_id: int | None = None) -> Booking | None:
    """Return an existing booking that clashes with the proposed range.

    Args:
        room_id: The room being booked.
        starts_at: Proposed start.
        ends_at: Proposed end.
        exclude_id: A booking to ignore, used when editing an existing one.

    Returns:
        Booking | None: The first clashing booking, or ``None``.

    Note:
        The overlap rule is expressed **in SQL** so the database can use the
        index on ``starts_at``. Loading every booking and filtering in Python
        would work fine with ten rows and fail badly with ten million (Day 08).

        ``exclude_id`` matters: without it, editing a booking would find *itself*
        as a conflict and refuse every change. That is a bug worth a test.
    """
    statement = select(Booking).where(
        Booking.room_id == room_id,
        Booking.starts_at < ends_at,
        Booking.ends_at > starts_at,
    )
    if exclude_id is not None:
        statement = statement.where(Booking.id != exclude_id)
    return db.session.execute(statement).scalars().first()


def utcnow() -> datetime:
    """Return the current aware UTC time.

    Returns:
        datetime: Timezone-aware now.

    Note:
        Wrapping the clock in a function is what makes it **mockable**. Code
        that calls ``datetime.now()`` inline can only be tested by waiting, or
        by patching a builtin in someone else's module. See
        ``tests/test_availability.py`` for how this gets frozen.
    """
    return datetime.now(timezone.utc)

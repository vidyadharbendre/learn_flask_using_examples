"""
Day 11 — Models for the bookstore API.

Kept small on purpose: today is about **API design**, not schema design. The
interesting decisions are in :mod:`bookstore.api` and :mod:`bookstore.errors`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .extensions import db


def iso_utc(value: datetime) -> str:
    """Render a datetime as an unambiguous ISO 8601 string in UTC.

    Args:
        value: A timestamp that may be naive or timezone-aware.

    Returns:
        str: e.g. ``"2026-08-20T13:06:30+00:00"``.

    Note:
        Why this helper is necessary: ``DateTime(timezone=True)`` is a *request*
        that the backend may ignore. PostgreSQL stores a real ``timestamptz``
        and hands back an aware datetime; **SQLite has no timezone type at all**
        and returns a naive one, so ``.isoformat()`` produces
        ``2026-08-20T13:06:30`` with no offset.

        A timestamp without an offset is ambiguous — the client cannot tell
        whether it is UTC or Kolkata time, and will guess wrong. Normalising at
        the serialisation boundary means the API contract holds on every
        backend, which is exactly the sort of portability gap you should check
        rather than assume.
    """
    if value.tzinfo is None:
        # Our columns are populated by func.now()/utcnow(), which are UTC.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class Author(db.Model):
    """A book author.

    Attributes:
        id: Surrogate primary key.
        name: Unique display name.
        books: Books written by this author.
    """

    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    books: Mapped[list["Book"]] = relationship(
        back_populates="author", cascade="all, delete-orphan", lazy="selectin"
    )

    def to_dict(self, *, include_books: bool = False) -> dict[str, Any]:
        """Serialise for the API.

        Args:
            include_books: Whether to embed the author's books. Off by default
                — a client fetching 100 authors rarely wants 100 nested book
                lists, and always sending them is how a response balloons to
                megabytes. Let the caller opt in with ``?include=books``.

        Returns:
            dict[str, Any]: JSON-safe representation.
        """
        data: dict[str, Any] = {"id": self.id, "name": self.name,
                                "book_count": len(self.books)}
        if include_books:
            data["books"] = [book.to_dict() for book in self.books]
        return data


class Book(db.Model):
    """A book in the catalogue.

    Attributes:
        id: Surrogate primary key.
        isbn: Unique ISBN-13.
        title: Book title.
        price: Exact decimal price.
        stock: Copies available.
        published_year: Year of publication.
        author_id: Foreign key to :class:`Author`.
        author: The related author.
        created_at / updated_at: Server-side timestamps.
    """

    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True)
    isbn: Mapped[str] = mapped_column(String(13), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    stock: Mapped[int] = mapped_column(nullable=False, default=0)
    published_year: Mapped[int] = mapped_column(nullable=False)

    author_id: Mapped[int] = mapped_column(
        ForeignKey("authors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author: Mapped["Author"] = relationship(back_populates="books")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc), nullable=False,
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API.

        Returns:
            dict[str, Any]: JSON-safe representation.

        Note:
            Three deliberate choices, each of which becomes a support ticket if
            you get it wrong:

            - ``price`` is a **string**, not a float. JSON has one numeric type
              (IEEE 754 double); ``12.10`` can arrive as ``12.099999999999999``.
              Money crosses the wire as a string.
            - Timestamps are **ISO 8601 with an explicit UTC offset**, via
              :func:`iso_utc`. A bare ``.isoformat()`` would emit no offset on
              SQLite, and an offset-less timestamp is ambiguous.
            - The author is **embedded as an object**, not a bare id. Clients
              almost always want the name, and embedding it saves an N+1 of
              HTTP requests — the network version of Day 08's problem.
        """
        return {
            "id": self.id,
            "isbn": self.isbn,
            "title": self.title,
            "price": str(self.price),
            "stock": self.stock,
            "published_year": self.published_year,
            "author": {"id": self.author.id, "name": self.author.name},
            # Normalised to UTC with an explicit offset — see iso_utc().
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }

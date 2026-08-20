"""
Day 12 — Models.

Note how much *smaller* ``to_dict`` gets today: it disappears entirely. Pydantic
schemas serialise these objects, so the model goes back to its real job —
describing storage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .extensions import db


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


class Book(db.Model):
    """A book in the catalogue.

    Attributes:
        id: Surrogate primary key.
        isbn: Unique ISBN-13.
        title: Book title.
        price: Exact decimal price.
        stock: Copies available.
        published_year: Year of publication.
        tags: Comma-separated tags (see :attr:`tag_list`).
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

    # Stored as a comma-separated string to keep the schema single-table, but
    # exposed to the API as a real list. Pydantic's computed fields make that
    # translation a one-liner — see schemas.BookOut.
    tags: Mapped[str] = mapped_column(String(200), nullable=False, default="")

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

    @property
    def tag_list(self) -> list[str]:
        """Return :attr:`tags` as a list.

        Returns:
            list[str]: Individual tags, empty when none are set.
        """
        return [tag for tag in self.tags.split(",") if tag]

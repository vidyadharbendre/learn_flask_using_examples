"""
Day 08 — Models: the database schema, expressed as Python classes.
==================================================================

Why a separate ``models.py``?
-----------------------------
Same reasoning as ``storage.py`` on Day 07: persistence is its own layer. Views
should ask for ``Product.query…``-shaped things, not build SQL. Keeping models
apart also avoids the circular import that bites everyone once (see
:mod:`extensions`).

SQLAlchemy 2.0 style
--------------------
This module uses the **modern** declarative style:

- ``DeclarativeBase`` + ``Mapped[...]`` + ``mapped_column(...)``
- real Python type annotations that ``mypy`` understands
- ``select()`` queries rather than the legacy ``Model.query``

You will still find ``db.Column`` and ``Model.query`` in older tutorials. They
work, but they are legacy: the annotated style gives you type checking, better
editor support, and is what SQLAlchemy 2.x documents.
"""

from __future__ import annotations

from datetime import datetime, timezone
# Decimal is imported at RUNTIME, not under `if TYPE_CHECKING`. SQLAlchemy
# resolves the string inside `Mapped[Decimal]` when it maps the class, so a
# type-checking-only import fails with:
#     ArgumentError: Could not resolve all types within mapped annotation
# Any name used in a Mapped[...] annotation must be importable at runtime.
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from extensions import db


def utcnow() -> datetime:
    """Return the current time as an aware UTC datetime.

    Returns:
        datetime: Timezone-aware "now" in UTC.

    Note:
        Always store **aware UTC** timestamps. ``datetime.utcnow()`` returns a
        *naive* datetime that merely happens to be UTC; the moment it meets an
        aware one you get ``TypeError: can't compare offset-naive and
        offset-aware datetimes``, usually in production, usually at 2am.
        Convert to the user's timezone only when displaying.
    """
    return datetime.now(timezone.utc)


class Category(db.Model):
    """A product category — the "one" side of a one-to-many relationship.

    Attributes:
        id: Surrogate primary key.
        name: Unique, human-readable name.
        slug: Unique, URL-safe identifier used in routes.
        products: All products in this category (see ``back_populates``).
    """

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)

    # `unique=True` creates a UNIQUE INDEX. This is a DATABASE-level guarantee.
    # Checking "does this name already exist?" in Python before inserting is a
    # race condition: two concurrent requests both check, both find nothing,
    # and both insert. Only the database can decide atomically.
    name: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(60), unique=True, nullable=False, index=True)

    # relationship() is ORM-level convenience; it creates NO column. The actual
    # link is the products.category_id FOREIGN KEY on the other side.
    #
    # cascade="all, delete-orphan": deleting a Category deletes its Products.
    # Choose this deliberately — the alternative is to block the delete while
    # products exist. Silently orphaning rows is never the right answer.
    products: Mapped[list["Product"]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
        # lazy="selectin" loads all related products in ONE extra query rather
        # than one query per parent. This is the standard cure for the N+1
        # problem — see the README.
        lazy="selectin",
    )

    def __repr__(self) -> str:
        """Return an unambiguous representation for logs and the shell.

        Returns:
            str: e.g. ``<Category 3 'Laptops'>``.
        """
        return f"<Category {self.id} {self.name!r}>"


class Product(db.Model):
    """A stock item — the "many" side.

    Attributes:
        id: Surrogate primary key.
        sku: Stock-keeping unit; unique business key.
        name: Display name.
        price: Unit price as ``Numeric(10, 2)`` — never a float.
        quantity: Units currently on hand; may not go negative.
        reorder_level: Quantity at which the item counts as low stock.
        category_id: Foreign key to :class:`Category`.
        category: The related category object.
        movements: Audit trail of stock changes.
        created_at / updated_at: Server-side timestamps.
    """

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # -------------------------------------------------------------------------
    # Money, again
    # -------------------------------------------------------------------------
    # Numeric/DECIMAL stores exact decimal values and maps to Python's Decimal.
    # Float/REAL would reintroduce the 0.1 + 0.2 problem from Day 07 at the
    # DATABASE level, where it is far harder to notice and impossible to undo.
    # (SQLite has no true DECIMAL type and stores these as text; Postgres and
    # MySQL implement it natively. Write portable code and let the driver cope.)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    quantity: Mapped[int] = mapped_column(nullable=False, default=0)
    reorder_level: Mapped[int] = mapped_column(nullable=False, default=5)

    # ondelete="CASCADE" is the DATABASE's rule; cascade="all, delete-orphan"
    # on the relationship is the ORM's. Set both: the ORM rule applies when you
    # delete through a session, the database rule applies to everything else
    # (a psql shell, another service, a bulk DELETE).
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped["Category"] = relationship(back_populates="products")

    movements: Mapped[list["StockMovement"]] = relationship(
        back_populates="product", cascade="all, delete-orphan",
        order_by="StockMovement.created_at.desc()",
    )

    # server_default=func.now() lets the DATABASE stamp the row. That keeps
    # timestamps consistent even when rows are inserted by a migration, a
    # bulk load, or another application entirely.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=utcnow, nullable=False,
    )

    __table_args__ = (
        # CHECK constraints are business rules the database itself enforces.
        # Validation in your form (Day 05) protects against honest mistakes;
        # a CHECK protects against every other code path that will ever touch
        # this table — including the buggy script you write next year.
        CheckConstraint("quantity >= 0", name="ck_products_quantity_non_negative"),
        CheckConstraint("price >= 0", name="ck_products_price_non_negative"),
        # A composite index for the "list a category's products by name" query.
        # Indexes make reads fast and writes slightly slower; add them for the
        # queries you actually run, not speculatively.
        Index("ix_products_category_name", "category_id", "name"),
    )

    @property
    def is_low_stock(self) -> bool:
        """Whether this product has fallen to or below its reorder level.

        Returns:
            bool: True when restocking is due.

        Note:
            A Python ``@property`` is computed in your application, so it cannot
            appear in a ``WHERE`` clause. To filter on it, express the same rule
            in SQL: ``select(Product).where(Product.quantity <= Product.reorder_level)``.
            Mixing the two up is a common source of "why is my filter ignored?".
        """
        return self.quantity <= self.reorder_level

    @property
    def stock_value(self) -> "Decimal":
        """Total value of the units on hand.

        Returns:
            Decimal: ``price * quantity``, exact because ``price`` is a Decimal.
        """
        return self.price * self.quantity

    def __repr__(self) -> str:
        """Return an unambiguous representation.

        Returns:
            str: e.g. ``<Product 12 'LAP-001' qty=4>``.
        """
        return f"<Product {self.id} {self.sku!r} qty={self.quantity}>"


class StockMovement(db.Model):
    """An append-only record of every stock change.

    Why keep this at all? Because ``UPDATE products SET quantity = 3`` destroys
    the answer to "who changed it, when, and why". An audit table turns stock
    into a *ledger*: the current quantity is a running total you can always
    reconstruct and reconcile.

    Attributes:
        id: Surrogate primary key.
        product_id: Foreign key to :class:`Product`.
        product: The related product.
        delta: Signed change — positive for received stock, negative for sold.
        reason: Short human explanation.
        created_at: When the movement was recorded.
    """

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product: Mapped["Product"] = relationship(back_populates="movements")

    delta: Mapped[int] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        CheckConstraint("delta <> 0", name="ck_movements_delta_non_zero"),
        # There is deliberately NO unique constraint on (product_id, created_at).
        # An earlier draft of this file had one, and it was wrong: two movements
        # for the same product genuinely can share a timestamp (a bulk import,
        # or simply two requests inside the same second, since server_default
        # timestamps have limited resolution). It failed with
        #     UNIQUE constraint failed: stock_movements.product_id, ...created_at
        # the first time two movements were recorded quickly.
        #
        # The lesson: a constraint encodes a RULE OF THE DOMAIN. Add one only
        # when the business genuinely forbids the duplicate, never because it
        # looks tidy. A wrong constraint is worse than a missing one — it
        # rejects legitimate data in production.
    )

    def __repr__(self) -> str:
        """Return an unambiguous representation.

        Returns:
            str: e.g. ``<StockMovement 5 product=12 delta=-2>``.
        """
        return f"<StockMovement {self.id} product={self.product_id} delta={self.delta}>"

"""
Day 09 — Models: the schema as it stands *after three migrations*.
==================================================================

This file always describes the **current** schema — the destination. The
journey lives in ``migrations/versions/``:

===================  ========================================================
``d3752bd02e9d``     initial: ``categories`` + ``products``
``7ad7e54df8a1``     add ``products.barcode`` and ``products.reorder_level``
``32635f848382``     add ``suppliers`` and ``products.supplier_id``
===================  ========================================================

That split is the whole idea. ``models.py`` answers *"what does the schema look
like?"*; the migration files answer *"how does an existing database get from
where it is to here, without losing data?"*. ``create_all()`` (Day 08) can only
answer the first question, which is why it cannot help you once real data
exists.

Read the two hand-edited migration files. Each one documents a mistake that
Alembic's autogenerate made and a human had to fix.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from extensions import db


class Category(db.Model):
    """A product category.

    Attributes:
        id: Surrogate primary key.
        name: Unique display name.
        slug: Unique URL-safe identifier.
        products: Products in this category.
    """

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(60), unique=True, nullable=False, index=True)
    products: Mapped[list["Product"]] = relationship(
        back_populates="category", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        """Return an unambiguous representation.

        Returns:
            str: e.g. ``<Category 1 'Laptops'>``.
        """
        return f"<Category {self.id} {self.name!r}>"


class Supplier(db.Model):
    """A supplier — **added in migration 32635f848382**.

    Attributes:
        id: Surrogate primary key.
        name: Unique company name.
        email: Purchasing contact address.
        products: Products sourced from this supplier.

    Note:
        Migration 3 also inserts a placeholder row (``Unassigned``) and points
        every pre-existing product at it. A schema change that leaves the
        application rendering blanks is only half a change.
    """

    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    products: Mapped[list["Product"]] = relationship(back_populates="supplier")

    def __repr__(self) -> str:
        """Return an unambiguous representation.

        Returns:
            str: e.g. ``<Supplier 1 'Unassigned'>``.
        """
        return f"<Supplier {self.id} {self.name!r}>"


class Product(db.Model):
    """A stock item.

    Attributes:
        id: Surrogate primary key.
        sku: Unique stock-keeping unit.
        name: Display name.
        price: Unit price as exact ``Numeric(10, 2)``.
        quantity: Units on hand.
        barcode: EAN-13 style code. Added in migration 2 and **backfilled**
            there for existing rows.
        reorder_level: Low-stock threshold. Added in migration 2 as ``NOT NULL``
            with ``server_default="5"`` — without that default, adding a
            non-nullable column to a populated table fails.
        category_id / category: Owning category.
        supplier_id / supplier: Sourcing supplier. **Nullable on purpose** —
            see the migration for the three-deploy sequence required to make a
            foreign key ``NOT NULL`` safely.
        created_at: Server-stamped creation time.
    """

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False, default=0)

    # --- Added in migration 7ad7e54df8a1 -------------------------------------
    barcode: Mapped[str | None] = mapped_column(String(13), unique=True, nullable=True)
    reorder_level: Mapped[int] = mapped_column(
        nullable=False, default=5, server_default="5"
    )

    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped["Category"] = relationship(back_populates="products")

    # --- Added in migration 32635f848382 -------------------------------------
    # NULLABLE on purpose: existing products had no supplier, and a NOT NULL
    # foreign key cannot be added to a populated table in a single step.
    supplier_id: Mapped[int | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    supplier: Mapped["Supplier | None"] = relationship(back_populates="products")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def is_low_stock(self) -> bool:
        """Whether stock has fallen to or below the reorder level.

        Returns:
            bool: True when restocking is due.
        """
        return self.quantity <= self.reorder_level

    def __repr__(self) -> str:
        """Return an unambiguous representation.

        Returns:
            str: e.g. ``<Product 1 'LAP-001'>``.
        """
        return f"<Product {self.id} {self.sku!r}>"

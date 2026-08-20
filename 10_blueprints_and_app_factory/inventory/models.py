"""Day 10 — Models (carried over from Day 09, unchanged in substance)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .extensions import db


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


class Product(db.Model):
    """A stock item.

    Attributes:
        id: Surrogate primary key.
        sku: Unique stock-keeping unit.
        name: Display name.
        price: Exact decimal unit price.
        quantity: Units on hand.
        reorder_level: Low-stock threshold.
        category_id: Owning category.
        category: The related category.
        created_at: Server-stamped creation time.
    """

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False, default=0)
    reorder_level: Mapped[int] = mapped_column(nullable=False, default=5, server_default="5")

    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped["Category"] = relationship(back_populates="products")

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

    def to_dict(self) -> dict[str, object]:
        """Serialise for the JSON API.

        Returns:
            dict[str, object]: A JSON-safe representation. ``Decimal`` becomes
            ``str`` because JSON has no decimal type and floats would
            reintroduce rounding error.

        Note:
            Hand-written serialisers stop scaling around the third model. Day 12
            replaces this with Pydantic schemas.
        """
        return {
            "id": self.id,
            "sku": self.sku,
            "name": self.name,
            "price": str(self.price),
            "quantity": self.quantity,
            "reorder_level": self.reorder_level,
            "is_low_stock": self.is_low_stock,
            "category": self.category.name if self.category else None,
        }

    def __repr__(self) -> str:
        """Return an unambiguous representation.

        Returns:
            str: e.g. ``<Product 1 'LAP-001'>``.
        """
        return f"<Product {self.id} {self.sku!r}>"

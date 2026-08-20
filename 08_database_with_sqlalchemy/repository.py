"""
Day 08 — Repository: the same interface as Day 07, now backed by SQL.
=====================================================================

This module is the payoff for Day 07's layering. Compare the two files:

- ``07_project_expense_tracker/storage.py`` — ``json.load``, list comprehensions
- ``08_database_with_sqlalchemy/repository.py`` — ``select()``, SQL aggregates

The *shape* is the same: domain-named functions that hide the mechanism. Views
call ``list_products(...)`` and never learn which one is underneath.

Queries here use SQLAlchemy 2.0's ``select()`` API rather than the legacy
``Model.query``, which is what current SQLAlchemy documents.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from extensions import db
from models import Category, Product, StockMovement


# -----------------------------------------------------------------------------
# Reads
# -----------------------------------------------------------------------------
def list_products(
    *, query: str = "", category_slug: str = "", low_stock_only: bool = False
) -> Sequence[Product]:
    """Return products matching the given filters.

    Every filter becomes part of the **SQL**, so the database does the work with
    an index instead of Python scanning every row. That is the whole reason to
    move off a JSON file.

    Args:
        query: Case-insensitive substring match on name or SKU.
        category_slug: Restrict to one category.
        low_stock_only: Only items at or below their reorder level.

    Returns:
        Sequence[Product]: Matching products, ordered by name.

    Note:
        ``selectinload(Product.category)`` eager-loads each product's category
        in **one** extra query. Without it, a template that prints
        ``product.category.name`` for 50 products issues 1 + 50 queries — the
        **N+1 problem**, and the most common cause of a slow Flask page.
    """
    statement = select(Product).options(selectinload(Product.category))

    if query:
        # ilike() is case-insensitive LIKE. Note we pass a PARAMETER, never an
        # f-string: `f"WHERE name LIKE '{query}'"` is SQL injection. SQLAlchemy
        # parameterises everything, which is why the ORM is also a security win.
        pattern = f"%{query.strip()}%"
        statement = statement.where(
            Product.name.ilike(pattern) | Product.sku.ilike(pattern)
        )

    if category_slug:
        # A JOIN, expressed through the relationship.
        statement = statement.join(Product.category).where(
            Category.slug == category_slug
        )

    if low_stock_only:
        # Column-to-column comparison, evaluated in SQL. The `is_low_stock`
        # Python property could NOT be used here — see its docstring.
        statement = statement.where(Product.quantity <= Product.reorder_level)

    statement = statement.order_by(Product.name)
    return db.session.execute(statement).scalars().all()


def get_product(product_id: int) -> Product | None:
    """Fetch one product by primary key.

    Args:
        product_id: The primary key.

    Returns:
        Product | None: The product, or ``None`` when it does not exist.

    Note:
        ``db.session.get()`` is the right call for a primary-key lookup: it
        checks the session's identity map first and may avoid a query entirely.
        Use ``select(...).where(id == x)`` only for non-PK lookups.
    """
    return db.session.get(Product, product_id)


def list_categories() -> Sequence[Category]:
    """Return every category with its product count.

    Returns:
        Sequence[Category]: Categories ordered by name, products preloaded.
    """
    statement = (
        select(Category).options(selectinload(Category.products)).order_by(Category.name)
    )
    return db.session.execute(statement).scalars().all()


def inventory_summary() -> dict[str, Any]:
    """Aggregate inventory figures **in the database**.

    Day 07 loaded every record into Python and summed it there. Here the
    database does it: one query, no rows transferred, an index does the scan.
    With a million products the difference is minutes versus milliseconds.

    Returns:
        dict[str, Any]: ``product_count``, ``total_units``, ``total_value``,
        ``low_stock_count`` and ``by_category``.
    """
    totals = db.session.execute(
        select(
            func.count(Product.id),
            func.coalesce(func.sum(Product.quantity), 0),
            # coalesce() turns SUM's NULL (on an empty table) into 0. Without
            # it, an empty inventory returns None and every downstream format
            # call raises TypeError.
            func.coalesce(func.sum(Product.price * Product.quantity), 0),
        )
    ).one()

    low_stock = db.session.execute(
        select(func.count(Product.id)).where(Product.quantity <= Product.reorder_level)
    ).scalar_one()

    by_category = db.session.execute(
        select(
            Category.name,
            func.count(Product.id).label("product_count"),
            func.coalesce(func.sum(Product.price * Product.quantity), 0).label("value"),
        )
        # An OUTER join keeps categories that have no products. An inner join
        # would silently drop them, and "the empty category vanished from the
        # report" is a bug that takes an afternoon to find.
        .outerjoin(Product, Product.category_id == Category.id)
        .group_by(Category.id, Category.name)
        .order_by(Category.name)
    ).all()

    return {
        "product_count": totals[0],
        "total_units": totals[1],
        "total_value": Decimal(totals[2]),
        "low_stock_count": low_stock,
        "by_category": [
            {"name": row.name, "product_count": row.product_count,
             "value": Decimal(row.value)}
            for row in by_category
        ],
    }


# -----------------------------------------------------------------------------
# Writes
# -----------------------------------------------------------------------------
def create_product(
    *, sku: str, name: str, price: Decimal, quantity: int,
    reorder_level: int, category_id: int,
) -> tuple[Product | None, str]:
    """Create a product, reporting a duplicate SKU as a friendly error.

    Args:
        sku: Unique stock-keeping unit.
        name: Display name.
        price: Unit price.
        quantity: Opening stock.
        reorder_level: Low-stock threshold.
        category_id: Owning category.

    Returns:
        tuple[Product | None, str]: The created product and an empty message,
        or ``(None, message)`` on failure.

    Note:
        This is the **EAFP** pattern — try it and handle the failure — rather
        than "SELECT to check, then INSERT". The check-then-act version is a
        race: two concurrent requests both find the SKU free and both insert.
        Only the database's UNIQUE constraint can decide atomically, so let it,
        and catch :class:`~sqlalchemy.exc.IntegrityError`.
    """
    # Opening stock starts at ZERO and arrives as a movement below. Setting
    # `quantity=quantity` here AND recording a +quantity movement would apply
    # the same units twice — a real bug in an earlier draft, where a product
    # created with 1 unit reported 2.
    #
    # The rule this enforces: `quantity` is the running total of the ledger, and
    # the ONLY way it changes is through record_movement(). One writer, one
    # source of truth, no drift.
    product = Product(
        sku=sku.strip().upper(), name=name.strip(), price=price,
        quantity=0, reorder_level=reorder_level, category_id=category_id,
    )
    db.session.add(product)
    try:
        # commit() ends the transaction and makes the change durable. Until it
        # runs, nothing is written — which is what makes the rollback below
        # safe and complete.
        db.session.commit()
    except IntegrityError:
        # ALWAYS roll back after an IntegrityError. The session is left in an
        # unusable state and every later query on it will raise
        # PendingRollbackError — an error message that sends people hunting in
        # entirely the wrong place.
        db.session.rollback()
        return None, f"SKU {sku.upper()!r} already exists."

    if quantity:
        record_movement(product_id=product.id, delta=quantity, reason="Opening stock")
    return product, ""


def record_movement(*, product_id: int, delta: int, reason: str) -> tuple[bool, str]:
    """Adjust stock and append an audit record — **atomically**.

    Two rows change: ``products.quantity`` and a new ``stock_movements`` row.
    They are written inside one transaction, so either both land or neither
    does. A crash between them would otherwise leave stock that no ledger entry
    explains.

    Args:
        product_id: Which product to adjust.
        delta: Signed change; positive receives stock, negative removes it.
        reason: Short explanation stored on the audit row.

    Returns:
        tuple[bool, str]: ``(True, "")`` on success, else ``(False, message)``.
    """
    product = db.session.get(Product, product_id)
    if product is None:
        return False, "No such product."
    if delta == 0:
        return False, "A movement of zero changes nothing."
    if product.quantity + delta < 0:
        # Checked here for a friendly message; the CHECK constraint in
        # models.py enforces it for every other code path.
        return False, (
            f"Cannot remove {abs(delta)} — only {product.quantity} in stock."
        )

    product.quantity += delta
    db.session.add(StockMovement(product_id=product.id, delta=delta, reason=reason.strip()))

    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        return False, f"Could not record movement: {error.orig}"
    return True, ""


def delete_product(product_id: int) -> bool:
    """Delete a product and, by cascade, its movement history.

    Args:
        product_id: The primary key.

    Returns:
        bool: ``True`` when a product was deleted, ``False`` when the id was
        unknown.
    """
    product = db.session.get(Product, product_id)
    if product is None:
        return False
    # Deleting through the SESSION lets the ORM cascade to `movements`.
    # A bulk `delete()` statement would bypass the ORM cascade entirely and
    # rely solely on the database's ON DELETE rule.
    db.session.delete(product)
    db.session.commit()
    return True


def ensure_category(*, name: str, slug: str) -> Category:
    """Return an existing category or create it.

    Args:
        name: Display name.
        slug: URL-safe identifier.

    Returns:
        Category: The existing or newly created row.
    """
    existing = db.session.execute(
        select(Category).where(Category.slug == slug)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    category = Category(name=name, slug=slug)
    db.session.add(category)
    db.session.commit()
    return category

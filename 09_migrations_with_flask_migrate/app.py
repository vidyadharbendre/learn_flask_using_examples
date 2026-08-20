"""
Day 09 — Migrations with Flask-Migrate: evolving a schema that holds data.
==========================================================================

Real-world scenario
-------------------
Day 08's inventory is live and full of real products. Now the business asks for
barcodes, reorder levels, and supplier tracking. You cannot drop the tables and
start again — that is somebody's data.

The problem ``create_all()`` cannot solve
-----------------------------------------
``db.create_all()`` issues ``CREATE TABLE IF NOT EXISTS``. Add a column to a
model, re-run it, and **nothing happens** — the table already exists, so it is
skipped. Your app then fails at runtime with ``no such column: products.barcode``.
``create_all()`` can create a schema; it can never *evolve* one.

**Alembic** solves this by keeping an ordered chain of migration scripts and a
row in your database (``alembic_version``) recording where that database
currently sits in the chain. ``flask db upgrade`` replays whatever is missing.

What you will learn
-------------------
1. ``flask db init / migrate / upgrade / downgrade / current / history``.
2. Why autogenerate is a **draft**, not an author — every migration in this
   folder was hand-corrected, and each file documents what was wrong.
3. **Data migrations**: schema change plus backfill, in one revision.
4. Adding ``NOT NULL`` columns and foreign keys to **populated** tables.
5. ``render_as_batch`` — the reason SQLite migrations work at all.
6. Why migrations must never import your models.
7. Deploying migrations safely.

How to run
----------
From the repository root::

    source .venv/bin/activate
    export FLASK_APP=09_migrations_with_flask_migrate/app.py
    flask db upgrade -d 09_migrations_with_flask_migrate/migrations
    flask --app 09_migrations_with_flask_migrate/app.py seed
    flask --app 09_migrations_with_flask_migrate/app.py run --port 5009 --debug

The migration history in this repository
----------------------------------------
=================  =========================================================
``d3752bd02e9d``   initial schema (``categories``, ``products``)
``7ad7e54df8a1``   + ``barcode``, ``reorder_level`` — **with a backfill**
``32635f848382``   + ``suppliers`` table, ``products.supplier_id``
=================  =========================================================

Open both hand-edited files. The docstrings explain exactly what autogenerate
got wrong and why it mattered.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import click
from flask import Flask, Response, jsonify, render_template
from sqlalchemy import inspect, select

from extensions import db, migrate

app = Flask(__name__)

INSTANCE_DIR = Path(app.instance_path)
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-not-for-production"),
    SQLALCHEMY_DATABASE_URI=os.environ.get(
        "DATABASE_URL", f"sqlite:///{INSTANCE_DIR / 'inventory.db'}"
    ),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
)

db.init_app(app)

# -----------------------------------------------------------------------------
# render_as_batch: the flag that makes SQLite migrations possible
# -----------------------------------------------------------------------------
# SQLite's ALTER TABLE is severely limited — historically no DROP COLUMN, no
# ALTER COLUMN, and no way to add a constraint to an existing table. "Batch
# mode" works around this by generating: create a new table with the target
# shape, copy the rows, drop the old table, rename the new one.
#
# Without this flag your SQLite migrations fail with NotImplementedError the
# first time you try to drop a column. On PostgreSQL and MySQL it is a harmless
# no-op wrapper, so leaving it on is the right default for portable projects.
migrate.init_app(app, db, render_as_batch=True)

# Models MUST be imported for Alembic's autogenerate to see their metadata.
# A model that is never imported is invisible, and `flask db migrate` will
# cheerfully report "No changes in schema detected" while your new table is
# missing. This import looks unused — it is not.
from models import Category, Product, Supplier  # noqa: E402

__all__ = ["app", "Category", "Product", "Supplier"]


@app.template_filter("inr")
def inr(amount: Decimal | int | float | None) -> str:
    """Format a monetary amount as Indian Rupees.

    Args:
        amount: A ``Decimal`` from the database, or ``None``.

    Returns:
        str: e.g. ``"₹89,999.00"``.
    """
    value = Decimal(str(amount or 0))
    whole, fraction = divmod(int(round(abs(value) * 100)), 100)
    digits = str(whole)
    if len(digits) > 3:
        last3, rest = digits[-3:], digits[:-3]
        pairs: list[str] = []
        while len(rest) > 2:
            pairs.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            pairs.insert(0, rest)
        digits = ",".join([*pairs, last3])
    return f"{'-' if value < 0 else ''}₹{digits}.{fraction:02d}"


@app.context_processor
def inject_globals() -> dict[str, Any]:
    """Expose shared values to every template.

    Returns:
        dict[str, Any]: Template globals.
    """
    return {"app_name": "Stockroom — Migrations"}


def _schema_version() -> str:
    """Read the current Alembic revision straight from the database.

    Alembic stores one row in a table called ``alembic_version``. That row is
    the entire mechanism: it records where *this particular database* sits in
    the migration chain, which is how ``upgrade`` knows what still to apply.

    Returns:
        str: The current revision id, or a message when the table is absent
        (meaning no migration has ever been applied here).
    """
    try:
        row = db.session.execute(db.text("SELECT version_num FROM alembic_version")).first()
    except Exception:  # noqa: BLE001 - table absent on a virgin database
        return "none — run `flask db upgrade`"
    return row[0] if row else "empty"


@app.route("/")
def dashboard() -> str:
    """Show products with the columns each migration added.

    Returns:
        str: Rendered ``dashboard.html``.
    """
    products = db.session.execute(
        select(Product).order_by(Product.sku)
    ).scalars().all()
    suppliers = db.session.execute(select(Supplier).order_by(Supplier.name)).scalars().all()

    # inspect() reads the LIVE database structure, not your models. Showing the
    # two side by side makes the point of this whole day concrete: models are
    # what you want, the database is what you have, migrations close the gap.
    inspector = inspect(db.engine)
    live_columns = {
        table: [column["name"] for column in inspector.get_columns(table)]
        for table in sorted(inspector.get_table_names())
    }

    return render_template(
        "dashboard.html",
        products=products,
        suppliers=suppliers,
        revision=_schema_version(),
        live_columns=live_columns,
    )


@app.route("/health")
def health() -> Response:
    """Report service and schema status.

    Returns:
        Response: JSON including the applied migration revision — genuinely
        useful in production for confirming a deploy actually ran its
        migrations.
    """
    return jsonify(status="ok", service="inventory", schema_revision=_schema_version())


@app.cli.command("seed")
def seed_command() -> None:
    """Insert demo rows, skipping any that already exist.

    Note:
        This command does **not** call ``create_all()``. Once a project uses
        migrations, ``create_all()`` must never be used again on a real
        database: it would create tables Alembic does not know about, leaving
        ``alembic_version`` out of step with reality.
    """
    supplier = db.session.execute(
        select(Supplier).where(Supplier.name == "Unassigned")
    ).scalar_one_or_none()
    if supplier is None:
        supplier = Supplier(name="Unassigned", email="purchasing@example.com")
        db.session.add(supplier)
        db.session.flush()

    seed_data = [
        ("Laptops", "laptops", [("LAP-001", "ThinkPad T14", "89999.00", 6),
                                ("LAP-002", "MacBook Air M3", "114900.00", 2)]),
        ("Peripherals", "peripherals", [("PER-001", "Mechanical Keyboard", "4999.00", 24),
                                        ("PER-002", '27" IPS Monitor', "18999.00", 4)]),
        ("Storage", "storage", [("STO-001", "1TB NVMe SSD", "7499.00", 12)]),
    ]

    created = 0
    for name, slug, products in seed_data:
        category = db.session.execute(
            select(Category).where(Category.slug == slug)
        ).scalar_one_or_none()
        if category is None:
            category = Category(name=name, slug=slug)
            db.session.add(category)
            db.session.flush()

        for sku, product_name, price, quantity in products:
            exists = db.session.execute(
                select(Product).where(Product.sku == sku)
            ).scalar_one_or_none()
            if exists is not None:
                continue
            db.session.add(Product(
                sku=sku, name=product_name, price=Decimal(price), quantity=quantity,
                barcode=None, category_id=category.id, supplier_id=supplier.id,
            ))
            created += 1

    db.session.commit()
    click.echo(f"Seeded {created} new product(s).")


@app.cli.command("demo-journey")
def demo_journey_command() -> None:
    """Replay the real-world migration story end to end.

    The point of a data migration is invisible unless rows exist **before** the
    column does. This command stages exactly that situation::

        1. downgrade to base            (empty database)
        2. upgrade to revision 1        (only categories + products exist)
        3. insert products              (rows that predate barcode/supplier)
        4. upgrade to head              (migrations 2 and 3 run AND backfill)

    Afterwards, every product has a barcode that no application code ever
    wrote — migration ``7ad7e54df8a1`` filled it in — and a supplier that
    migration ``32635f848382`` assigned.

    Warning:
        This destroys the current database contents, so it asks first.
    """
    from flask_migrate import downgrade, upgrade

    click.confirm("This rebuilds the database from scratch. Continue?", abort=True)

    directory = str(Path(__file__).parent / "migrations")

    click.echo("\n1. downgrade -> base")
    downgrade(directory=directory, revision="base")

    click.echo("2. upgrade   -> d3752bd02e9d (initial schema only)")
    upgrade(directory=directory, revision="d3752bd02e9d")

    click.echo("3. insert products that PREDATE the barcode column")
    # Raw SQL, because at this revision the Product model has columns the
    # table does not have yet. This is the same reason migrations never
    # import models.
    db.session.execute(db.text(
        "INSERT INTO categories (name, slug) VALUES "
        "('Laptops','laptops'), ('Peripherals','peripherals')"
    ))
    db.session.execute(db.text(
        "INSERT INTO products (sku, name, price, quantity, category_id, created_at) VALUES "
        "('LAP-001','ThinkPad T14',89999.00,6,1,CURRENT_TIMESTAMP),"
        "('LAP-002','MacBook Air M3',114900.00,2,1,CURRENT_TIMESTAMP),"
        "('PER-001','Mechanical Keyboard',4999.00,24,2,CURRENT_TIMESTAMP)"
    ))
    db.session.commit()
    click.echo("   3 products inserted, with NO barcode column in existence")

    click.echo("4. upgrade   -> head (adds columns AND backfills them)")
    upgrade(directory=directory)

    click.echo("\nResult — barcodes written by the migration, not by the app:")
    for row in db.session.execute(db.text(
        "SELECT p.sku, p.barcode, p.reorder_level, s.name "
        "FROM products p LEFT JOIN suppliers s ON s.id = p.supplier_id "
        "ORDER BY p.sku"
    )):
        click.echo(f"   {row[0]:<10} barcode={row[1]}  reorder={row[2]}  supplier={row[3]}")
    click.echo("")


@app.cli.command("schema-report")
def schema_report_command() -> None:
    """Print the live database structure and the current revision.

    Useful for answering "did the migration actually run on this box?" without
    opening a database shell.
    """
    click.echo(f"Revision: {_schema_version()}\n")
    inspector = inspect(db.engine)
    for table in sorted(inspector.get_table_names()):
        columns = ", ".join(c["name"] for c in inspector.get_columns(table))
        click.echo(f"  {table}: {columns}")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5009, debug=True)

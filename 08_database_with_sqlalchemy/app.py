"""
Day 08 — Database with SQLAlchemy: real persistence, at last.
=============================================================

Real-world scenario
-------------------
An inventory manager for a small electronics shop: products, categories, stock
levels, low-stock alerts, and an append-only ledger of every stock movement.

Why today matters
-----------------
Day 07 ended with an honest confession: a JSON file cannot survive two
processes writing at once, cannot query without loading everything into memory,
and has no way to make two related changes atomic. Every one of those is solved
by a database — and the ORM lets you use one without writing SQL by hand.

What you will learn
-------------------
1. **Models** — tables as Python classes (SQLAlchemy 2.0 annotated style).
2. **The session** — a unit of work; ``add`` / ``commit`` / ``rollback``.
3. **Transactions** — two writes that both happen, or neither.
4. **Relationships** — ``ForeignKey`` + ``relationship`` + ``back_populates``.
5. **The N+1 problem** and eager loading with ``selectinload``.
6. **Constraints** — ``UNIQUE`` and ``CHECK`` as guarantees your Python cannot
   provide, plus handling ``IntegrityError``.
7. **Aggregates in SQL** rather than in Python.
8. The **circular-import** trap and the ``extensions.py`` cure.

How to run
----------
From the repository root::

    source .venv/bin/activate
    flask --app 08_database_with_sqlalchemy/app.py init-db
    flask --app 08_database_with_sqlalchemy/app.py seed
    flask --app 08_database_with_sqlalchemy/app.py run --port 5008 --debug

Layering, unchanged from Day 07
-------------------------------
``app.py`` (HTTP) → ``forms.py`` (validation) → ``repository.py`` (persistence)
→ ``models.py`` (schema). Only the bottom two layers are new. The views look
almost identical to Day 07's — which was the entire point of building it that
way.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import click
from flask import (
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask.typing import ResponseReturnValue
from werkzeug.wrappers import Response as WerkzeugResponse

import repository as repo
from extensions import db
from forms import MovementForm, ProductForm
from models import Category, Product

app = Flask(__name__)

# -----------------------------------------------------------------------------
# Database configuration
# -----------------------------------------------------------------------------
# Flask's `instance/` folder is the conventional home for files that are
# environment-specific and must never be committed: the SQLite database, local
# config, uploaded files. Flask creates and ignores it for you.
INSTANCE_DIR = Path(app.instance_path)
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-not-for-production"),

    # The connection URL: dialect+driver://user:pass@host/database
    #   sqlite:///relative/path.db      postgresql+psycopg://user:pw@host/dbname
    # SQLite needs no server, which makes it perfect for learning and for tests.
    # Everything you write today works unchanged against Postgres — that
    # portability is a large part of what an ORM buys you.
    SQLALCHEMY_DATABASE_URI=os.environ.get(
        "DATABASE_URL", f"sqlite:///{INSTANCE_DIR / 'inventory.db'}"
    ),

    # Off by default in modern Flask-SQLAlchemy; set explicitly because the
    # legacy default added real overhead and confused a generation of tutorials.
    SQLALCHEMY_TRACK_MODIFICATIONS=False,

    # Echo every statement to the log. Turn this on once and actually READ the
    # output — it is the fastest way to understand what the ORM is doing, and
    # to see the N+1 problem with your own eyes.
    SQLALCHEMY_ECHO=os.environ.get("SQL_ECHO", "").lower() in {"1", "true", "yes"},
)

# Deferred initialisation. `db` was created in extensions.py with no app; this
# binds it to ours. See extensions.py for why that indirection exists.
db.init_app(app)

# Importing models AFTER init_app is not required, but importing them at all is:
# a model class must be imported before `db.create_all()` can see its table.
# This is why "my table wasn't created" is nearly always an unimported model.
__all__ = ["app", "Category", "Product"]


# -----------------------------------------------------------------------------
# Template helpers
# -----------------------------------------------------------------------------
@app.template_filter("inr")
def inr(amount: Decimal | int | float | None) -> str:
    """Format a monetary amount as Indian Rupees.

    Args:
        amount: A ``Decimal`` from the database, or ``None``.

    Returns:
        str: e.g. ``"₹1,24,999.00"``, or ``"₹0.00"`` for ``None``.
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
    return {"app_name": "Stockroom — Inventory Manager"}


def _category_choices() -> list[tuple[int, str]]:
    """Build ``SelectField`` choices from the database.

    Returns:
        list[tuple[int, str]]: ``(id, name)`` pairs.

    Note:
        Called **per request**, not at import time. Choices baked in at import
        would never show a category added afterwards — the same early-binding
        trap as a mutable default argument.
    """
    return [(c.id, c.name) for c in repo.list_categories()]


# -----------------------------------------------------------------------------
# Views
# -----------------------------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def dashboard() -> ResponseReturnValue:
    """List products, show inventory totals, and handle product creation.

    Returns:
        str | WerkzeugResponse: The rendered dashboard (200, or 422 when the
        form has errors) or a 303 redirect after a successful create.
    """
    form = ProductForm()
    form.category_id.choices = _category_choices()

    if form.validate_on_submit():
        product, error = repo.create_product(
            sku=form.sku.data or "",
            name=form.name.data or "",
            price=Decimal(str(form.price.data or 0)),
            quantity=form.quantity.data or 0,
            reorder_level=form.reorder_level.data or 0,
            category_id=form.category_id.data or 0,
        )
        if product is None:
            # A database-level rejection surfaced as a FIELD error, so it
            # renders next to the offending input like any other validation
            # message. Users should not care which layer said no.
            form.sku.errors = list(form.sku.errors) + [error]
        else:
            flash(f"Added {product.name} ({product.sku}).", "success")
            return redirect(url_for("dashboard"), code=303)

    filters = {
        "q": request.args.get("q", "").strip(),
        "category": request.args.get("category", "").strip(),
        "low": request.args.get("low", "").strip(),
    }
    products = repo.list_products(
        query=filters["q"],
        category_slug=filters["category"],
        low_stock_only=filters["low"] == "1",
    )

    return render_template(
        "dashboard.html",
        form=form,
        products=products,
        categories=repo.list_categories(),
        summary=repo.inventory_summary(),
        filters=filters,
    ), (422 if form.errors else 200)


@app.route("/products/<int:product_id>", methods=["GET", "POST"])
def product_detail(product_id: int) -> ResponseReturnValue:
    """Show one product with its movement ledger, and record new movements.

    Args:
        product_id: Primary key from the URL.

    Returns:
        str | WerkzeugResponse: Rendered detail page or a 303 redirect.

    Raises:
        werkzeug.exceptions.NotFound: when the product does not exist.
    """
    product = repo.get_product(product_id)
    if product is None:
        abort(404, description="No such product.")

    form = MovementForm()
    if form.validate_on_submit():
        ok, error = repo.record_movement(
            product_id=product.id,
            delta=form.delta.data or 0,
            reason=form.reason.data or "",
        )
        if ok:
            flash("Stock updated.", "success")
            return redirect(url_for("product_detail", product_id=product.id), code=303)
        form.delta.errors = list(form.delta.errors) + [error]

    return render_template("product.html", product=product, form=form), (
        422 if form.errors else 200
    )


@app.route("/products/<int:product_id>/delete", methods=["POST"])
def delete_product(product_id: int) -> WerkzeugResponse:
    """Delete a product and its movement history.

    Args:
        product_id: Primary key from the URL.

    Returns:
        WerkzeugResponse: 303 redirect to the dashboard.
    """
    if repo.delete_product(product_id):
        flash("Product deleted, along with its movement history.", "info")
    else:
        flash("That product no longer exists.", "warning")
    return redirect(url_for("dashboard"), code=303)


@app.route("/api/inventory")
def api_inventory() -> Response:
    """Return the inventory summary as JSON.

    Returns:
        Response: Totals and per-category values. ``Decimal`` is converted to
        ``str`` because JSON has no decimal type and floats would reintroduce
        the rounding error the database was chosen to avoid.
    """
    summary = repo.inventory_summary()
    return jsonify({
        "product_count": summary["product_count"],
        "total_units": summary["total_units"],
        "total_value": str(summary["total_value"]),
        "low_stock_count": summary["low_stock_count"],
        "by_category": [
            {"name": row["name"], "product_count": row["product_count"],
             "value": str(row["value"])}
            for row in summary["by_category"]
        ],
    })


@app.route("/health")
def health() -> tuple[Response, int]:
    """Liveness probe that also verifies the database is reachable.

    Returns:
        tuple[Response, int]: JSON status with ``200`` when the database
        answers, ``503`` when it does not.

    Note:
        This one *does* check a dependency, unlike Day 01's. Both styles are
        valid and serve different purposes: a **liveness** probe should stay
        green during a DB blip (restarting the process would not help), while a
        **readiness** probe should go red so traffic is routed elsewhere. Know
        which one you are writing.
    """
    try:
        db.session.execute(db.text("SELECT 1"))
    except Exception as error:  # noqa: BLE001 - report any failure as unhealthy
        return jsonify(status="degraded", database=str(error)), 503
    return jsonify(status="ok", service="inventory", database="reachable"), 200


@app.errorhandler(404)
def not_found(error: Exception) -> tuple[str, int]:
    """Render a friendly 404 page.

    Args:
        error: The ``NotFound`` exception.

    Returns:
        tuple[str, int]: Rendered page and status code.
    """
    return render_template("404.html", error=error), 404


# -----------------------------------------------------------------------------
# CLI commands
# -----------------------------------------------------------------------------
@app.cli.command("init-db")
def init_db_command() -> None:
    """Create every table defined by the models.

    ``create_all()`` issues ``CREATE TABLE IF NOT EXISTS`` for each model it can
    see. It is perfect for getting started and for tests.

    Warning:
        ``create_all()`` **never alters an existing table.** Add a column to a
        model and re-run it, and nothing happens — your app then fails with
        ``no such column``. That limitation is exactly what Day 09's migrations
        exist to solve. Do not use ``create_all()`` on a database that holds
        data you care about.
    """
    db.create_all()
    click.echo(f"Tables created in {app.config['SQLALCHEMY_DATABASE_URI']}")


@app.cli.command("seed")
def seed_command() -> None:
    """Populate the inventory with demo categories and products."""
    db.create_all()

    catalogue = {
        ("Laptops", "laptops"): [
            ("LAP-001", "ThinkPad T14", "89999.00", 6, 3),
            ("LAP-002", "MacBook Air M3", "114900.00", 2, 3),
        ],
        ("Peripherals", "peripherals"): [
            ("PER-001", "Mechanical Keyboard", "4999.00", 24, 10),
            ("PER-002", "27\" IPS Monitor", "18999.00", 4, 5),
            ("PER-003", "USB-C Hub", "2499.00", 40, 15),
        ],
        ("Storage", "storage"): [
            ("STO-001", "1TB NVMe SSD", "7499.00", 12, 6),
            ("STO-002", "4TB External HDD", "9299.00", 1, 4),
        ],
        ("Accessories", "accessories"): [],  # deliberately empty — see the
                                             # outer join in inventory_summary
    }

    created = 0
    for (name, slug), products in catalogue.items():
        category = repo.ensure_category(name=name, slug=slug)
        for sku, product_name, price, quantity, reorder in products:
            product, error = repo.create_product(
                sku=sku, name=product_name, price=Decimal(price),
                quantity=quantity, reorder_level=reorder, category_id=category.id,
            )
            if product is not None:
                created += 1
            elif error:
                click.echo(f"  skipped {sku}: {error}")

    click.echo(f"Seeded {created} product(s) across {len(catalogue)} categories.")


@app.cli.command("drop-db")
def drop_db_command() -> None:
    """Drop every table. Prompts first — this destroys all data."""
    click.confirm("Drop ALL tables and lose all data?", abort=True)
    db.drop_all()
    click.echo("Dropped.")


@app.cli.command("demo-n-plus-one")
def demo_n_plus_one_command() -> None:
    """Demonstrate the N+1 query problem and its fix, side by side.

    Run with ``SQL_ECHO=1`` to watch the statements::

        SQL_ECHO=1 flask --app 08_database_with_sqlalchemy/app.py demo-n-plus-one

    You will see one ``SELECT`` per product in the first section and exactly two
    queries in the second.
    """
    from sqlalchemy import select

    click.echo("\n--- LAZY (N+1): one query for products, then one PER product ---")
    products = db.session.execute(select(Product)).scalars().all()
    db.session.expire_all()  # force fresh loads so the effect is visible
    for product in products:
        _ = product.category.name  # each access fires its own SELECT

    click.echo(f"    touched {len(products)} products -> ~{len(products) + 1} queries")

    click.echo("\n--- EAGER (selectinload): two queries, total ---")
    db.session.expire_all()
    from sqlalchemy.orm import selectinload

    products = db.session.execute(
        select(Product).options(selectinload(Product.category))
    ).scalars().all()
    for product in products:
        _ = product.category.name  # already loaded; no further queries

    click.echo(f"    touched {len(products)} products -> 2 queries\n")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5008, debug=True)

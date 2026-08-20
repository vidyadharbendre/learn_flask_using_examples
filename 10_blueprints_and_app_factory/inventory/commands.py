"""
Day 10 — CLI commands, registered by the factory.

With no module-level ``app``, ``@app.cli.command`` has nothing to hang off. The
pattern is a :class:`click.Group` (or plain functions) registered inside
``create_app`` via ``app.cli.add_command``.
"""

from __future__ import annotations

from decimal import Decimal

import click
from flask import Flask
from flask.cli import with_appcontext
from sqlalchemy import select

from .extensions import db
from .models import Category, Product


@click.command("init-db")
@with_appcontext
def init_db_command() -> None:
    """Create all tables.

    Note:
        ``@with_appcontext`` pushes an application context so ``db`` resolves.
        Without it you get ``RuntimeError: Working outside of application
        context`` — because ``db.session`` needs to know *which* app it belongs
        to, and with a factory there is no single global answer.
    """
    db.create_all()
    click.echo("Tables created.")


@click.command("seed")
@with_appcontext
def seed_command() -> None:
    """Insert demo categories and products, skipping existing SKUs."""
    db.create_all()

    data = [
        ("Laptops", "laptops", [
            ("LAP-001", "ThinkPad T14", "89999.00", 6, 3),
            ("LAP-002", "MacBook Air M3", "114900.00", 2, 3),
            ("LAP-003", "Dell XPS 13", "134999.00", 5, 2),
        ]),
        ("Peripherals", "peripherals", [
            ("PER-001", "Mechanical Keyboard", "4999.00", 24, 10),
            ("PER-002", '27" IPS Monitor', "18999.00", 4, 5),
            ("PER-003", "USB-C Hub", "2499.00", 40, 15),
            ("PER-004", "Wireless Mouse", "1799.00", 33, 12),
        ]),
        ("Storage", "storage", [
            ("STO-001", "1TB NVMe SSD", "7499.00", 12, 6),
            ("STO-002", "4TB External HDD", "9299.00", 1, 4),
        ]),
        ("Networking", "networking", [
            ("NET-001", "Wi-Fi 6 Router", "6499.00", 8, 4),
            ("NET-002", "8-port Switch", "3299.00", 3, 5),
        ]),
    ]

    created = 0
    for name, slug, products in data:
        category = db.session.execute(
            select(Category).where(Category.slug == slug)
        ).scalar_one_or_none()
        if category is None:
            category = Category(name=name, slug=slug)
            db.session.add(category)
            db.session.flush()

        for sku, product_name, price, quantity, reorder in products:
            exists = db.session.execute(
                select(Product).where(Product.sku == sku)
            ).scalar_one_or_none()
            if exists is None:
                db.session.add(Product(
                    sku=sku, name=product_name, price=Decimal(price),
                    quantity=quantity, reorder_level=reorder, category_id=category.id,
                ))
                created += 1

    db.session.commit()
    click.echo(f"Seeded {created} new product(s).")


@click.command("routes-by-blueprint")
@with_appcontext
def routes_by_blueprint_command() -> None:
    """Print the URL map grouped by blueprint.

    A concrete way to see what registration actually did: each endpoint is
    named ``<blueprint>.<view>``, and each rule carries the prefix supplied at
    registration time.
    """
    from flask import current_app

    grouped: dict[str, list[str]] = {}
    for rule in current_app.url_map.iter_rules():
        blueprint = rule.endpoint.split(".")[0] if "." in rule.endpoint else "(app)"
        methods = ",".join(sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}))
        grouped.setdefault(blueprint, []).append(f"{methods:<12} {rule.rule:<34} -> {rule.endpoint}")

    for blueprint in sorted(grouped):
        click.echo(f"\n[{blueprint}]")
        for line in sorted(grouped[blueprint]):
            click.echo(f"  {line}")
    click.echo("")


def register_commands(app: Flask) -> None:
    """Attach every CLI command to the application.

    Args:
        app: The application being built by the factory.
    """
    app.cli.add_command(init_db_command)
    app.cli.add_command(seed_command)
    app.cli.add_command(routes_by_blueprint_command)

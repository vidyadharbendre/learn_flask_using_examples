"""
Day 10 — ``main`` blueprint: the dashboard and site-wide pages.

Mounted at the root (no ``url_prefix``), so its routes are ``/`` and
``/health``.
"""

from __future__ import annotations

from flask import Blueprint, Response, current_app, jsonify, render_template
from sqlalchemy import func, select

from ..extensions import db
from ..models import Category, Product

# The first argument is the blueprint NAME. It becomes the endpoint prefix:
# this module's `dashboard` view is addressed as `main.dashboard`.
#
# __name__ lets the blueprint locate its own folder, which is what makes the
# `template_folder` below resolve relative to this package.
main_bp = Blueprint(
    "main",
    __name__,
    # A blueprint may carry its OWN templates. Flask searches the app's
    # templates/ first, then each blueprint's — so an app-level file can
    # override a blueprint's, which is how you customise a third-party
    # blueprint without forking it.
    template_folder="../templates/main",
)


@main_bp.route("/")
def dashboard() -> str:
    """Show inventory totals and the low-stock list.

    Returns:
        str: Rendered ``main/dashboard.html``.

    Note:
        ``current_app`` is a **proxy** to the application handling this request.
        Because there is no module-level ``app`` any more, this is how you reach
        config from inside a blueprint. It only works inside an application
        context — which every request has automatically.
    """
    totals = db.session.execute(
        select(
            func.count(Product.id),
            func.coalesce(func.sum(Product.quantity), 0),
            func.coalesce(func.sum(Product.price * Product.quantity), 0),
        )
    ).one()

    low_stock = db.session.execute(
        select(Product)
        .where(Product.quantity <= Product.reorder_level)
        .order_by(Product.quantity)
        .limit(current_app.config["ITEMS_PER_PAGE"])
    ).scalars().all()

    categories = db.session.execute(
        select(Category).order_by(Category.name)
    ).scalars().all()

    return render_template(
        "main/dashboard.html",
        product_count=totals[0],
        total_units=totals[1],
        total_value=totals[2],
        low_stock=low_stock,
        categories=categories,
    )


@main_bp.route("/health")
def health() -> tuple[Response, int]:
    """Report service and database health.

    Returns:
        tuple[Response, int]: JSON status, ``200`` or ``503``.
    """
    try:
        db.session.execute(db.text("SELECT 1"))
    except Exception as error:  # noqa: BLE001 - any failure means unhealthy
        return jsonify(status="degraded", database=str(error)), 503
    return jsonify(
        status="ok",
        service="inventory",
        config=current_app.config.__class__.__name__,
        debug=current_app.debug,
    ), 200

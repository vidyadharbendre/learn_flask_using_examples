"""
Day 10 — ``api`` blueprint: JSON endpoints with their own error handling.

This blueprint demonstrates the strongest argument for blueprints beyond tidy
files: **an area of your site can behave differently**. The HTML pages render a
404 page; this section returns a JSON 404. Same application, two conventions,
no ``if request.path.startswith("/api")`` branching anywhere.

Day 11 develops this into a properly designed REST API.
"""

from __future__ import annotations

from flask import Blueprint, Response, current_app, jsonify, request
from sqlalchemy import func, select
from werkzeug.exceptions import HTTPException

from ..extensions import csrf, db
from ..models import Category, Product

api_bp = Blueprint("api", __name__)

# CSRF protection defends browser FORM submissions, where the browser attaches
# cookies automatically. A token-authenticated JSON API (Day 15) does not use
# cookies, so a CSRF token is meaningless there and merely blocks legitimate
# clients. Exempt the blueprint as a whole.
#
# Exempting is only safe because this API will not authenticate via cookies.
# A cookie-authenticated JSON endpoint DOES need CSRF protection.
csrf.exempt(api_bp)


def handle_api_error(error: HTTPException) -> tuple[Response, int]:
    """Return every HTTP error in this blueprint as JSON.

    A blueprint-scoped error handler only fires for requests routed **into that
    blueprint**, which is what lets ``/api/products/999`` answer with JSON while
    ``/products/999`` renders an HTML page.

    Args:
        error: The raised HTTP exception.

    Returns:
        tuple[Response, int]: A JSON error body and the original status code.
    """
    return jsonify(
        error=error.name,
        message=error.description,
        status=error.code,
    ), (error.code or 500)


# -----------------------------------------------------------------------------
# Registering the handler: a subtle Flask resolution rule
# -----------------------------------------------------------------------------
# The obvious version does NOT work:
#
#     @api_bp.errorhandler(HTTPException)     # <- silently loses to the app
#
# Flask resolves handlers by SPECIFIC STATUS CODE FIRST, then by exception
# class, checking the blueprint and then the app at each step:
#
#     for code in (404, None):
#         for scope in (blueprint, app):
#             ...look for a handler...
#
# A generic `HTTPException` handler is stored under code `None`, so an
# app-level `@app.errorhandler(404)` — registered in the factory — is found
# first and renders HTML. The blueprint handler never runs.
#
# The cure is to register the blueprint handler for each specific code as well.
# This bit while writing this example: /api/products/9999 was returning an HTML
# 404 page to an API client.
for _status in (400, 401, 403, 404, 405, 409, 415, 422, 429, 500):
    api_bp.register_error_handler(_status, handle_api_error)

# Keep the class-based registration too, so any HTTP error not listed above
# (418, say) still comes back as JSON rather than HTML.
api_bp.register_error_handler(HTTPException, handle_api_error)

# KNOWN LIMIT, and it is Flask's design rather than a bug:
# a 404 from ROUTING — a URL matching no rule at all, e.g. /api/typo — belongs
# to no blueprint, because Flask never worked out which blueprint you meant.
# `request.blueprint` is None, so this handler cannot fire and the app-level
# HTML 404 answers instead.
#
# If your API must return JSON even for unrouted paths, handle it at the app
# level and branch on the path:
#
#     @app.errorhandler(404)
#     def not_found(error):
#         if request.path.startswith("/api/"):
#             return jsonify(error="Not Found", status=404), 404
#         return render_template("errors/404.html", error=error), 404
#
# Day 11 builds a dedicated API application where this question does not arise.


@api_bp.get("/products")
def list_products() -> Response:
    """List products as JSON, with pagination.

    ``@api_bp.get(...)`` is shorthand for ``@api_bp.route(..., methods=["GET"])``.
    The verb-specific decorators (``.get``, ``.post``, ``.put``, ``.patch``,
    ``.delete``) read better for APIs.

    Returns:
        Response: ``{"items": [...], "page": 1, "pages": 3, "total": 25}``.
    """
    page = request.args.get("page", default=1, type=int)
    statement = select(Product).order_by(Product.sku)

    if category := request.args.get("category", "").strip():
        statement = statement.join(Product.category).where(Category.slug == category)

    pagination = db.paginate(
        statement, page=page,
        per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False,
    )

    return jsonify({
        "items": [product.to_dict() for product in pagination.items],
        "page": pagination.page,
        "pages": pagination.pages,
        "total": pagination.total,
    })


@api_bp.get("/products/<int:product_id>")
def get_product(product_id: int) -> Response:
    """Return one product as JSON.

    Args:
        product_id: Primary key from the URL.

    Returns:
        Response: The serialised product.

    Raises:
        werkzeug.exceptions.NotFound: handled by :func:`handle_api_error`,
        which renders it as JSON rather than HTML.
    """
    product = db.session.get(Product, product_id)
    if product is None:
        from flask import abort

        abort(404, description=f"No product with id {product_id}.")
    return jsonify(product.to_dict())


@api_bp.get("/summary")
def summary() -> Response:
    """Return inventory totals as JSON.

    Returns:
        Response: Counts and values, with ``Decimal`` rendered as ``str``.
    """
    totals = db.session.execute(
        select(
            func.count(Product.id),
            func.coalesce(func.sum(Product.quantity), 0),
            func.coalesce(func.sum(Product.price * Product.quantity), 0),
        )
    ).one()
    return jsonify(
        product_count=totals[0],
        total_units=totals[1],
        total_value=str(totals[2]),
    )

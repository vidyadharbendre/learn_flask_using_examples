"""
Day 10 — ``products`` blueprint: CRUD for stock items.

Registered with ``url_prefix="/products"``, so ``@products_bp.route("/")``
serves ``/products/`` and ``@products_bp.route("/<int:product_id>")`` serves
``/products/42``.
"""

from __future__ import annotations

from decimal import Decimal

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.wrappers import Response

from ..extensions import db
from ..forms import ProductForm
from ..models import Category, Product

products_bp = Blueprint("products", __name__, template_folder="../templates/products")


@products_bp.app_template_filter("qty_class")
def qty_class(product: Product) -> str:
    """Return a CSS class describing a product's stock level.

    ``app_template_filter`` (rather than ``template_filter``) registers this on
    the **whole application**, so other blueprints' templates can use it too.
    A blueprint-scoped filter does not exist — filters are global by nature —
    but the ``app_*`` prefix makes that explicit at the call site.

    Args:
        product: The product to classify.

    Returns:
        str: ``"is-low"`` when restocking is due, otherwise ``""``.
    """
    return "is-low" if product.is_low_stock else ""


def _category_choices() -> list[tuple[int, str]]:
    """Build select choices from the database, per request.

    Returns:
        list[tuple[int, str]]: ``(id, name)`` pairs.
    """
    categories = db.session.execute(select(Category).order_by(Category.name)).scalars().all()
    return [(c.id, c.name) for c in categories]


@products_bp.route("/")
def index() -> str:
    """List products with search, filtering and pagination.

    Returns:
        str: Rendered ``products/index.html``.

    Note:
        The endpoint name is ``products.index``. Two blueprints can each define
        ``index`` without collision — that namespacing is a large part of why
        blueprints exist.
    """
    query = request.args.get("q", "").strip()
    category_slug = request.args.get("category", "").strip()
    page = request.args.get("page", default=1, type=int)

    statement = select(Product).order_by(Product.name)
    if query:
        pattern = f"%{query}%"
        statement = statement.where(
            Product.name.ilike(pattern) | Product.sku.ilike(pattern)
        )
    if category_slug:
        statement = statement.join(Product.category).where(Category.slug == category_slug)

    # db.paginate() runs the query with LIMIT/OFFSET and returns an object
    # carrying items, page, pages, has_next and has_prev — everything the
    # template needs to draw pager links.
    pagination = db.paginate(
        statement,
        page=page,
        per_page=current_app.config["ITEMS_PER_PAGE"],
        error_out=False,  # page=999 yields an empty page, not a 404
    )

    return render_template(
        "products/index.html",
        pagination=pagination,
        products=pagination.items,
        categories=db.session.execute(select(Category).order_by(Category.name)).scalars().all(),
        filters={"q": query, "category": category_slug},
    )


@products_bp.route("/new", methods=["GET", "POST"])
def create() -> str | Response:
    """Create a product.

    Returns:
        str | Response: The rendered form (200/422) or a 303 redirect.
    """
    form = ProductForm()
    form.category_id.choices = _category_choices()

    if form.validate_on_submit():
        product = Product(
            sku=(form.sku.data or "").upper(),
            name=form.name.data or "",
            price=Decimal(str(form.price.data or 0)),
            quantity=form.quantity.data or 0,
            reorder_level=form.reorder_level.data or 0,
            category_id=form.category_id.data or 0,
        )
        db.session.add(product)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            form.sku.errors = list(form.sku.errors) + ["That SKU already exists."]
        else:
            flash(f"Created {product.name}.", "success")
            # Note the BLUEPRINT-QUALIFIED endpoint name. Plain
            # url_for("index") raises BuildError from inside a blueprint.
            return redirect(url_for("products.detail", product_id=product.id), code=303)

    return render_template("products/form.html", form=form, product=None), (
        422 if form.errors else 200
    )


@products_bp.route("/<int:product_id>")
def detail(product_id: int) -> str:
    """Show one product.

    Args:
        product_id: Primary key from the URL.

    Returns:
        str: Rendered ``products/detail.html``.

    Raises:
        werkzeug.exceptions.NotFound: when the product does not exist.
    """
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404, description="No such product.")
    return render_template("products/detail.html", product=product)


@products_bp.route("/<int:product_id>/edit", methods=["GET", "POST"])
def edit(product_id: int) -> str | Response:
    """Edit an existing product.

    Args:
        product_id: Primary key from the URL.

    Returns:
        str | Response: The rendered form (200/422) or a 303 redirect.

    Raises:
        werkzeug.exceptions.NotFound: when the product does not exist.

    Note:
        ``ProductForm(obj=product)`` pre-populates every field whose name
        matches an attribute, and ``form.populate_obj(product)`` writes them
        back. Because the ORM tracks changes, saving is just ``commit()``.
    """
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404, description="No such product.")

    form = ProductForm(obj=product)
    form.category_id.choices = _category_choices()

    if form.validate_on_submit():
        form.populate_obj(product)
        product.sku = product.sku.upper()
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            form.sku.errors = list(form.sku.errors) + ["That SKU already exists."]
        else:
            flash("Product updated.", "success")
            return redirect(url_for("products.detail", product_id=product.id), code=303)

    return render_template("products/form.html", form=form, product=product), (
        422 if form.errors else 200
    )


@products_bp.route("/<int:product_id>/delete", methods=["POST"])
def delete(product_id: int) -> Response:
    """Delete a product.

    Args:
        product_id: Primary key from the URL.

    Returns:
        Response: 303 redirect to the product list.
    """
    product = db.session.get(Product, product_id)
    if product is None:
        flash("That product no longer exists.", "warning")
    else:
        db.session.delete(product)
        db.session.commit()
        flash(f"Deleted {product.name}.", "info")
    return redirect(url_for("products.index"), code=303)

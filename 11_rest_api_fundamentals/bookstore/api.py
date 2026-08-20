"""
Day 11 — The API blueprint: resources, verbs, status codes.
===========================================================

Everything here is a design decision, not a coding one. The code is short; the
reasoning is the lesson.

Resource design
---------------
URLs name **things** (nouns), and the HTTP method says what you are doing to
them. If your URL contains a verb, you are building RPC with extra steps::

    GET    /api/v1/books            list      ❌ /api/v1/getAllBooks
    POST   /api/v1/books            create    ❌ /api/v1/createBook
    GET    /api/v1/books/42         read      ❌ /api/v1/getBook?id=42
    PUT    /api/v1/books/42         replace   ❌ /api/v1/updateBook
    PATCH  /api/v1/books/42         modify
    DELETE /api/v1/books/42         delete    ❌ /api/v1/deleteBook

Plural collections, ids in the path, filters in the query string.

Method semantics you must respect
---------------------------------
============  ======  ==========  ==================================
Method        Safe?   Idempotent  Meaning
============  ======  ==========  ==================================
``GET``       yes     yes         read; must never change state
``POST``      no      **no**      create; twice creates two things
``PUT``       no      yes         replace whole; twice = same result
``PATCH``     no      no*         modify some fields
``DELETE``    no      yes         remove; twice = still removed
============  ======  ==========  ==================================

*Safe* means "changes nothing". *Idempotent* means "doing it twice leaves the
same state as doing it once" — which is what lets a client safely retry after a
timeout. Break these and you break every HTTP cache, proxy and retry policy
between you and your users.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request, url_for
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .errors import APIError
from .extensions import db
from .models import Author, Book

# Versioning from day one. A URL is a contract; the moment a third party
# depends on it you cannot make a breaking change. `/v1` gives you somewhere to
# put `/v2` later while `/v1` keeps working for existing clients.
#
# URL versioning is not the only option (header-based versioning exists), but it
# is the most visible, the easiest to route, and the easiest to debug in a log.
api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20

# Sorting is an allow-list, never free text. `?sort=<user input>` interpolated
# into a query is how SQL injection happens; mapping to real column objects also
# means an unknown field is a clean 400 rather than a 500.
SORTABLE: dict[str, Any] = {
    "title": Book.title,
    "price": Book.price,
    "stock": Book.stock,
    "published_year": Book.published_year,
    "created_at": Book.created_at,
}


# -----------------------------------------------------------------------------
# Request parsing helpers
# -----------------------------------------------------------------------------
def _json_body() -> dict[str, Any]:
    """Return the parsed JSON body, or raise a well-shaped error.

    Returns:
        dict[str, Any]: The decoded object.

    Raises:
        APIError: ``415`` when the client did not send JSON, ``400`` when the
            body is malformed or is not a JSON object.

    Note:
        The distinction matters. **415 Unsupported Media Type** means "I do not
        speak your format" — the client sent form data or forgot the header.
        **400 Bad Request** means "I speak JSON and yours is broken". Collapsing
        both into 400 makes a common client mistake much harder to diagnose.
    """
    if not request.is_json:
        raise APIError(
            415, "unsupported_media_type",
            "Send Content-Type: application/json.",
        )

    body = request.get_json(silent=True)
    if body is None:
        raise APIError(400, "malformed_json", "The request body is not valid JSON.")
    if not isinstance(body, dict):
        raise APIError(400, "malformed_json", "The request body must be a JSON object.")
    return body


def _validate_book(data: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """Validate and coerce a book payload.

    Args:
        data: The raw decoded JSON body.
        partial: When True, missing fields are allowed (``PATCH`` semantics);
            when False, every required field must be present (``POST``/``PUT``).

    Returns:
        dict[str, Any]: Cleaned values, ready to assign to a model.

    Raises:
        APIError: ``422`` with a ``details`` map of field → message.

    Note:
        Every error is collected before raising, so the client learns about all
        its mistakes in one round trip. This is the Day 04 lesson, applied to
        an API — and it is exactly the hand-rolled validation that Day 12
        replaces with Pydantic.
    """
    errors: dict[str, str] = {}
    cleaned: dict[str, Any] = {}

    def missing(field: str) -> bool:
        """Report whether a required field is absent.

        Args:
            field: Field name.

        Returns:
            bool: True when the field must be present but is not.
        """
        return field not in data and not partial

    # --- title ---------------------------------------------------------------
    if missing("title"):
        errors["title"] = "This field is required."
    elif "title" in data:
        title = data["title"]
        if not isinstance(title, str) or not title.strip():
            errors["title"] = "Must be a non-empty string."
        elif len(title) > 200:
            errors["title"] = "Must be 200 characters or fewer."
        else:
            cleaned["title"] = title.strip()

    # --- isbn ----------------------------------------------------------------
    if missing("isbn"):
        errors["isbn"] = "This field is required."
    elif "isbn" in data:
        isbn = str(data["isbn"]).replace("-", "").strip()
        if not isbn.isdigit() or len(isbn) != 13:
            errors["isbn"] = "Must be 13 digits (hyphens allowed)."
        else:
            cleaned["isbn"] = isbn

    # --- price ---------------------------------------------------------------
    if missing("price"):
        errors["price"] = "This field is required."
    elif "price" in data:
        try:
            # Decimal(str(...)) — never Decimal(float). Decimal(0.1) preserves
            # the float's error; Decimal("0.1") is exactly one tenth.
            price = Decimal(str(data["price"]))
        except (InvalidOperation, TypeError):
            errors["price"] = "Must be a decimal number."
        else:
            if price < 0:
                errors["price"] = "Must not be negative."
            elif price > Decimal("100000"):
                errors["price"] = "Must be 100000 or less."
            else:
                cleaned["price"] = price

    # --- stock ---------------------------------------------------------------
    if "stock" in data:
        stock = data["stock"]
        # `isinstance(True, int)` is True in Python, so booleans must be
        # excluded explicitly or `{"stock": true}` silently becomes 1.
        if not isinstance(stock, int) or isinstance(stock, bool) or stock < 0:
            errors["stock"] = "Must be a non-negative integer."
        else:
            cleaned["stock"] = stock
    elif not partial:
        cleaned["stock"] = 0  # a sensible default, not an error

    # --- published_year ------------------------------------------------------
    if missing("published_year"):
        errors["published_year"] = "This field is required."
    elif "published_year" in data:
        year = data["published_year"]
        if not isinstance(year, int) or isinstance(year, bool):
            errors["published_year"] = "Must be an integer."
        elif not 1450 <= year <= 2100:
            errors["published_year"] = "Must be between 1450 and 2100."
        else:
            cleaned["published_year"] = year

    # --- author_id -----------------------------------------------------------
    if missing("author_id"):
        errors["author_id"] = "This field is required."
    elif "author_id" in data:
        author_id = data["author_id"]
        if not isinstance(author_id, int) or isinstance(author_id, bool):
            errors["author_id"] = "Must be an integer."
        elif db.session.get(Author, author_id) is None:
            # 422, not 404: the *request body* is wrong. A 404 here would
            # wrongly suggest that /api/v1/books itself does not exist.
            errors["author_id"] = f"No author with id {author_id}."
        else:
            cleaned["author_id"] = author_id

    if errors:
        raise APIError(
            422, "validation_error",
            "The request body failed validation.", details=errors,
        )
    return cleaned


def _paginate(statement: Any, endpoint: str, **view_args: Any) -> dict[str, Any]:
    """Run a paginated query and build the standard collection envelope.

    Args:
        statement: The SQLAlchemy ``select()`` to paginate.
        endpoint: Endpoint name used to build ``next``/``prev`` links.
        **view_args: Extra URL arguments for those links.

    Returns:
        dict[str, Any]: ``{"data": [...], "meta": {...}, "links": {...}}``.

    Note:
        **Always paginate a collection.** An endpoint that returns every row
        works beautifully with your 50 seeded books and takes the site down when
        a customer has 500,000. Cap the page size too — otherwise
        ``?per_page=1000000`` is a denial-of-service request that you invited.

        Returning ``links.next`` means clients do not have to construct URLs, so
        you can change the pagination scheme later without breaking them.
    """
    page = request.args.get("page", default=1, type=int)
    per_page = request.args.get("per_page", default=DEFAULT_PAGE_SIZE, type=int)

    page = max(1, page)
    per_page = max(1, min(per_page, MAX_PAGE_SIZE))

    pagination = db.paginate(statement, page=page, per_page=per_page, error_out=False)

    # Preserve every filter in the next/prev links, or paging silently drops
    # the user's search — the API version of Day 10's pager bug.
    query_args = {k: v for k, v in request.args.items() if k != "page"}

    def link(target_page: int | None) -> str | None:
        """Build a page link, or ``None`` when that page does not exist.

        Args:
            target_page: Page number, or ``None``.

        Returns:
            str | None: Absolute URL, or ``None``.
        """
        if target_page is None:
            return None
        return url_for(endpoint, page=target_page, **query_args, **view_args,
                       _external=True)

    return {
        "data": [item.to_dict() for item in pagination.items],
        "meta": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        },
        "links": {
            "self": link(pagination.page),
            "next": link(pagination.next_num) if pagination.has_next else None,
            "prev": link(pagination.prev_num) if pagination.has_prev else None,
        },
    }


# -----------------------------------------------------------------------------
# Books
# -----------------------------------------------------------------------------
@api_bp.get("/books")
def list_books() -> Response:
    """List books with filtering, sorting and pagination.

    Query parameters:
        ``q``: substring match on title or ISBN.
        ``author_id``: restrict to one author.
        ``min_price`` / ``max_price``: inclusive price bounds.
        ``sort``: one of :data:`SORTABLE`; prefix with ``-`` for descending.
        ``page`` / ``per_page``: pagination (``per_page`` capped at 100).

    Returns:
        Response: ``200`` with the standard collection envelope.

    Raises:
        APIError: ``400`` for an unknown sort field or a non-numeric price.
    """
    statement = select(Book).join(Book.author)

    if q := request.args.get("q", "").strip():
        pattern = f"%{q}%"
        statement = statement.where(Book.title.ilike(pattern) | Book.isbn.ilike(pattern))

    if author_id := request.args.get("author_id", type=int):
        statement = statement.where(Book.author_id == author_id)

    for param, comparison in (("min_price", "ge"), ("max_price", "le")):
        if raw := request.args.get(param):
            try:
                value = Decimal(raw)
            except InvalidOperation:
                raise APIError(400, "bad_request", f"{param} must be a number.") from None
            statement = statement.where(
                Book.price >= value if comparison == "ge" else Book.price <= value
            )

    # Sorting: "-price" means descending. Validate against the allow-list.
    sort = request.args.get("sort", "title").strip()
    descending = sort.startswith("-")
    field = sort.lstrip("-")
    if field not in SORTABLE:
        raise APIError(
            400, "bad_request",
            f"Cannot sort by {field!r}.",
            details={"allowed": sorted(SORTABLE)},
        )
    column = SORTABLE[field]
    statement = statement.order_by(column.desc() if descending else column.asc())

    return jsonify(_paginate(statement, "api.list_books"))


@api_bp.get("/books/<int:book_id>")
def get_book(book_id: int) -> Response:
    """Return one book.

    Args:
        book_id: Primary key from the URL.

    Returns:
        Response: ``200`` with the book object.

    Raises:
        APIError: ``404`` when no such book exists.
    """
    book = db.session.get(Book, book_id)
    if book is None:
        raise APIError(404, "not_found", f"No book with id {book_id}.")
    return jsonify(book.to_dict())


@api_bp.post("/books")
def create_book() -> tuple[Response, int, dict[str, str]]:
    """Create a book.

    Returns:
        tuple[Response, int, dict[str, str]]: ``201`` with the created object
        and a ``Location`` header pointing at it.

    Raises:
        APIError: ``415``/``400`` for a bad body, ``422`` for invalid fields,
            ``409`` when the ISBN already exists.

    Note:
        Three things make this a correct ``POST``:

        1. **201 Created**, not 200. The status tells the client a resource came
           into existence.
        2. A **``Location`` header** with the new resource's URL — the client
           should never have to guess it.
        3. The **created object in the body**, including server-assigned fields
           (``id``, ``created_at``) that the client could not know.

        ``POST`` is deliberately **not idempotent**: sending it twice creates
        two books. If a client needs safe retries, it should use ``PUT`` with a
        client-chosen id, or send an idempotency key (see the exercises).
    """
    data = _json_body()
    cleaned = _validate_book(data)

    book = Book(**cleaned)
    db.session.add(book)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # 409 Conflict is the right status for "this violates the current state
        # of the resource", which is exactly what a duplicate unique key is.
        # 400 would say "your request is malformed" — it is not.
        raise APIError(
            409, "conflict", f"A book with ISBN {cleaned['isbn']} already exists."
        ) from None

    return (
        jsonify(book.to_dict()),
        201,
        {"Location": url_for("api.get_book", book_id=book.id, _external=True)},
    )


@api_bp.put("/books/<int:book_id>")
def replace_book(book_id: int) -> Response:
    """Replace a book **entirely**.

    Args:
        book_id: Primary key from the URL.

    Returns:
        Response: ``200`` with the updated object.

    Raises:
        APIError: ``404`` when absent, ``422`` when invalid, ``409`` on a
            duplicate ISBN.

    Note:
        ``PUT`` means *replace*: the body must contain every field, and anything
        omitted is reset to its default. That is what makes it **idempotent** —
        sending the same body twice leaves exactly the same state, which lets a
        client retry safely after a network timeout.

        If you want "update only what I sent", that is ``PATCH``.
    """
    book = db.session.get(Book, book_id)
    if book is None:
        raise APIError(404, "not_found", f"No book with id {book_id}.")

    cleaned = _validate_book(_json_body(), partial=False)
    for field, value in cleaned.items():
        setattr(book, field, value)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise APIError(409, "conflict", "That ISBN belongs to another book.") from None

    return jsonify(book.to_dict())


@api_bp.patch("/books/<int:book_id>")
def update_book(book_id: int) -> Response:
    """Update selected fields of a book.

    Args:
        book_id: Primary key from the URL.

    Returns:
        Response: ``200`` with the updated object.

    Raises:
        APIError: ``404`` when absent, ``422`` when invalid or when the body is
            empty, ``409`` on a duplicate ISBN.

    Note:
        ``PATCH`` applies a partial update: only the fields present in the body
        change. An empty body is rejected rather than silently accepted —
        answering ``200`` to a request that did nothing hides client bugs.
    """
    book = db.session.get(Book, book_id)
    if book is None:
        raise APIError(404, "not_found", f"No book with id {book_id}.")

    data = _json_body()
    if not data:
        raise APIError(422, "validation_error", "Provide at least one field to update.")

    cleaned = _validate_book(data, partial=True)
    for field, value in cleaned.items():
        setattr(book, field, value)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise APIError(409, "conflict", "That ISBN belongs to another book.") from None

    return jsonify(book.to_dict())


@api_bp.delete("/books/<int:book_id>")
def delete_book(book_id: int) -> tuple[str, int]:
    """Delete a book.

    Args:
        book_id: Primary key from the URL.

    Returns:
        tuple[str, int]: ``204 No Content`` with an empty body.

    Raises:
        APIError: ``404`` when no such book exists.

    Note:
        **204 No Content** is the conventional answer to a successful delete:
        it succeeded, and there is nothing left to return. Returning ``200``
        with ``{"deleted": true}`` is not wrong, merely noisier.

        A 204 response **must** have an empty body — some clients and proxies
        will choke if you send one.

        Returning 404 for an already-deleted book is a defensible choice, and so
        is 204. ``DELETE`` is *idempotent* in the sense that the end state is
        the same either way; only the status differs. Pick one and document it.
    """
    book = db.session.get(Book, book_id)
    if book is None:
        raise APIError(404, "not_found", f"No book with id {book_id}.")

    db.session.delete(book)
    db.session.commit()
    return "", 204


# -----------------------------------------------------------------------------
# Authors — nested collections
# -----------------------------------------------------------------------------
@api_bp.get("/authors")
def list_authors() -> Response:
    """List authors.

    Query parameters:
        ``include=books``: embed each author's books.

    Returns:
        Response: ``200`` with the standard collection envelope.
    """
    include_books = "books" in request.args.get("include", "").split(",")
    statement = select(Author).order_by(Author.name)
    page = request.args.get("page", default=1, type=int)
    per_page = min(request.args.get("per_page", default=DEFAULT_PAGE_SIZE, type=int),
                   MAX_PAGE_SIZE)
    pagination = db.paginate(statement, page=max(1, page), per_page=max(1, per_page),
                             error_out=False)

    return jsonify({
        "data": [a.to_dict(include_books=include_books) for a in pagination.items],
        "meta": {"page": pagination.page, "per_page": pagination.per_page,
                 "total": pagination.total, "pages": pagination.pages},
    })


@api_bp.get("/authors/<int:author_id>/books")
def list_author_books(author_id: int) -> Response:
    """List one author's books — a **nested collection**.

    Args:
        author_id: Primary key from the URL.

    Returns:
        Response: ``200`` with the standard collection envelope.

    Raises:
        APIError: ``404`` when the author does not exist.

    Note:
        ``/authors/5/books`` and ``/books?author_id=5`` return the same rows,
        and both are legitimate. The nested form expresses ownership and gives a
        natural 404 when the author does not exist; the filtered form composes
        with other filters. Offering both is common — just keep the response
        shape identical.
    """
    if db.session.get(Author, author_id) is None:
        raise APIError(404, "not_found", f"No author with id {author_id}.")

    statement = select(Book).where(Book.author_id == author_id).order_by(Book.title)
    return jsonify(_paginate(statement, "api.list_author_books", author_id=author_id))


# -----------------------------------------------------------------------------
# Service endpoints
# -----------------------------------------------------------------------------
@api_bp.get("/stats")
def stats() -> Response:
    """Return catalogue statistics.

    Returns:
        Response: Counts and totals, with money as strings.
    """
    totals = db.session.execute(
        select(
            func.count(Book.id),
            func.coalesce(func.sum(Book.stock), 0),
            func.coalesce(func.sum(Book.price * Book.stock), 0),
        )
    ).one()
    author_count = db.session.execute(select(func.count(Author.id))).scalar_one()

    return jsonify({
        "books": totals[0],
        "authors": author_count,
        "copies_in_stock": totals[1],
        "stock_value": str(totals[2]),
    })


@api_bp.get("/")
def index() -> Response:
    """Return a discoverable index of the API.

    Returns:
        Response: A map of relation names to URLs.

    Note:
        A root document that lists the available endpoints costs nothing and
        makes an API explorable — a developer can start with ``curl`` on the
        base URL instead of hunting for documentation. It is also the first
        step towards HATEOAS, if you ever want it.
    """
    return jsonify({
        "version": "v1",
        "endpoints": {
            "books": url_for("api.list_books", _external=True),
            "authors": url_for("api.list_authors", _external=True),
            "stats": url_for("api.stats", _external=True),
        },
        "docs": "See 11_rest_api_fundamentals/README.md",
        "max_page_size": MAX_PAGE_SIZE,
    })

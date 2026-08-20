"""
Day 12 — The API, with validation reduced to a decorator.
=========================================================

Compare a create handler with Day 11's::

    # Day 11
    data = _json_body()
    cleaned = _validate_book(data)      # ~90 lines of isinstance checks
    book = Book(**cleaned)

    # Day 12
    @validate_body(BookCreate)
    def create_book(payload: BookCreate):
        book = Book(**payload.model_dump(exclude={"tags"}), tags=",".join(payload.tags))

The validation logic did not disappear — it moved into :mod:`catalogue.schemas`
where it is **declarative, reusable, and self-documenting**. The view is left
with what it is actually for: talking to the database and choosing a status code.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from flask import Blueprint, Response, jsonify, request, url_for
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .errors import APIError
from .extensions import db
from .models import Author, Book
from .schemas import (
    AuthorOut,
    BookCreate,
    BookListOut,
    BookOut,
    BookUpdate,
    Page,
    format_validation_error,
)

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20

ModelT = TypeVar("ModelT", bound=BaseModel)


def validate_body(schema: type[ModelT]) -> Callable[..., Any]:
    """Parse and validate the JSON body against ``schema``.

    On success the validated model is passed to the view as its first
    positional argument. On failure a ``422`` is raised in the standard
    envelope, with per-field details.

    Args:
        schema: The Pydantic model describing an acceptable body.

    Returns:
        Callable: A decorator wrapping a view function.

    Raises:
        APIError: ``415`` when the request is not JSON, ``400`` when the body
            is malformed, ``422`` when validation fails.

    Example:
        >>> @api_bp.post("/books")                  # doctest: +SKIP
        ... @validate_body(BookCreate)
        ... def create_book(payload: BookCreate): ...

    Note:
        Writing this once removes the *"did I remember to validate this
        endpoint?"* question entirely. A view either declares a schema or takes
        no body — there is no third state where validation was simply
        forgotten. That is the real value: not fewer lines, but no gaps.
    """

    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        # functools.wraps copies __name__ and __doc__ onto the wrapper. Without
        # it EVERY decorated view would be registered under the endpoint name
        # "wrapper", and Flask would raise on the second one. This is the most
        # common decorator bug in Flask codebases.
        @wraps(view)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not request.is_json:
                raise APIError(415, "unsupported_media_type",
                               "Send Content-Type: application/json.")

            raw = request.get_json(silent=True)
            if raw is None or not isinstance(raw, dict):
                raise APIError(400, "malformed_json",
                               "The request body must be a JSON object.")

            try:
                payload = schema.model_validate(raw)
            except ValidationError as error:
                raise APIError(
                    422, "validation_error",
                    "The request body failed validation.",
                    details=format_validation_error(error),
                ) from error

            return view(payload, *args, **kwargs)

        return wrapper

    return decorator


def _book_to_orm_fields(payload: BookCreate | BookUpdate) -> dict[str, Any]:
    """Translate schema fields into model column values.

    Args:
        payload: A validated create or update payload.

    Returns:
        dict[str, Any]: Values suitable for assigning to :class:`Book`.

    Note:
        The API exposes ``tags`` as a list; the table stores a comma-separated
        string. This function is the single place that translation happens —
        the schema does not know about storage, and the model does not know
        about JSON. Keeping that seam explicit is what lets either side change
        without the other noticing.
    """
    if isinstance(payload, BookUpdate):
        values = payload.changes()
    else:
        values = payload.model_dump()

    if "tags" in values and values["tags"] is not None:
        values["tags"] = ",".join(values["tags"])
    return values


# -----------------------------------------------------------------------------
# Books
# -----------------------------------------------------------------------------
@api_bp.get("/books")
def list_books() -> Response:
    """List books with filtering and pagination.

    Returns:
        Response: ``200`` with a :class:`~catalogue.schemas.BookListOut` body.

    Note:
        ``BookOut.model_validate(book)`` reads the SQLAlchemy object directly,
        thanks to ``from_attributes=True``. There is no ``to_dict`` anywhere in
        this file — the schema owns serialisation, including money as a string,
        UTC timestamps, and the ``in_stock`` computed field.
    """
    statement = select(Book).join(Book.author).order_by(Book.title)

    if q := request.args.get("q", "").strip():
        statement = statement.where(Book.title.ilike(f"%{q}%"))
    if tag := request.args.get("tag", "").strip().lower():
        statement = statement.where(Book.tags.ilike(f"%{tag}%"))

    page = max(1, request.args.get("page", default=1, type=int))
    per_page = max(1, min(request.args.get("per_page", default=DEFAULT_PAGE_SIZE,
                                           type=int), MAX_PAGE_SIZE))
    pagination = db.paginate(statement, page=page, per_page=per_page, error_out=False)

    body = BookListOut(
        data=[BookOut.model_validate(book) for book in pagination.items],
        meta=Page(page=pagination.page, per_page=pagination.per_page,
                  total=pagination.total or 0, pages=pagination.pages or 0),
    )
    # model_dump(mode="json") converts Decimal, datetime and every other
    # non-JSON type using the field serialisers. Plain model_dump() would leave
    # real Decimal objects in the dict and jsonify would then fail.
    return jsonify(body.model_dump(mode="json"))


@api_bp.get("/books/<int:book_id>")
def get_book(book_id: int) -> Response:
    """Return one book.

    Args:
        book_id: Primary key from the URL.

    Returns:
        Response: ``200`` with the serialised book.

    Raises:
        APIError: ``404`` when no such book exists.
    """
    book = db.session.get(Book, book_id)
    if book is None:
        raise APIError(404, "not_found", f"No book with id {book_id}.")
    return jsonify(BookOut.model_validate(book).model_dump(mode="json"))


@api_bp.post("/books")
@validate_body(BookCreate)
def create_book(payload: BookCreate) -> tuple[Response, int, dict[str, str]]:
    """Create a book from a validated payload.

    Args:
        payload: The validated request body, injected by :func:`validate_body`.

    Returns:
        tuple[Response, int, dict[str, str]]: ``201``, the created object, and
        a ``Location`` header.

    Raises:
        APIError: ``422`` when the author does not exist, ``409`` on a
            duplicate ISBN.

    Note:
        The author check stays here rather than in the schema. A schema should
        validate the *shape* of data; "does this row exist?" is a **database**
        question, and putting a query inside a Pydantic validator couples your
        schemas to a live session — which then breaks the moment you want to
        validate a payload in a test, a CLI script, or a queue consumer.
    """
    if db.session.get(Author, payload.author_id) is None:
        raise APIError(
            422, "validation_error", "The request body failed validation.",
            details={"author_id": [f"No author with id {payload.author_id}."]},
        )

    book = Book(**_book_to_orm_fields(payload))
    db.session.add(book)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise APIError(409, "conflict",
                       f"A book with ISBN {payload.isbn} already exists.") from None

    return (
        jsonify(BookOut.model_validate(book).model_dump(mode="json")),
        201,
        {"Location": url_for("api.get_book", book_id=book.id, _external=True)},
    )


@api_bp.patch("/books/<int:book_id>")
@validate_body(BookUpdate)
def update_book(payload: BookUpdate, book_id: int) -> Response:
    """Apply a partial update.

    Args:
        payload: The validated body; only fields the client sent are applied.
        book_id: Primary key from the URL.

    Returns:
        Response: ``200`` with the updated book.

    Raises:
        APIError: ``404`` when absent, ``422`` for an unknown author,
            ``409`` on a duplicate ISBN.

    Note:
        ``payload.changes()`` uses ``exclude_unset=True``, which is what makes
        ``PATCH`` correct: it distinguishes "the client omitted ``stock``" from
        "the client explicitly sent ``stock: 0``". Without it, every unmentioned
        field would be reset to its default — i.e. ``PUT`` behaviour under a
        ``PATCH`` label, quietly destroying data.
    """
    book = db.session.get(Book, book_id)
    if book is None:
        raise APIError(404, "not_found", f"No book with id {book_id}.")

    if payload.author_id is not None and db.session.get(Author, payload.author_id) is None:
        raise APIError(
            422, "validation_error", "The request body failed validation.",
            details={"author_id": [f"No author with id {payload.author_id}."]},
        )

    for field, value in _book_to_orm_fields(payload).items():
        setattr(book, field, value)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise APIError(409, "conflict", "That ISBN belongs to another book.") from None

    return jsonify(BookOut.model_validate(book).model_dump(mode="json"))


@api_bp.delete("/books/<int:book_id>")
def delete_book(book_id: int) -> tuple[str, int]:
    """Delete a book.

    Args:
        book_id: Primary key from the URL.

    Returns:
        tuple[str, int]: ``204`` with an empty body.

    Raises:
        APIError: ``404`` when no such book exists.
    """
    book = db.session.get(Book, book_id)
    if book is None:
        raise APIError(404, "not_found", f"No book with id {book_id}.")
    db.session.delete(book)
    db.session.commit()
    return "", 204


@api_bp.get("/authors")
def list_authors() -> Response:
    """List authors.

    Returns:
        Response: ``200`` with the serialised authors.
    """
    authors = db.session.execute(select(Author).order_by(Author.name)).scalars().all()
    return jsonify({
        "data": [AuthorOut.model_validate(a).model_dump(mode="json") for a in authors]
    })


# -----------------------------------------------------------------------------
# Self-documentation
# -----------------------------------------------------------------------------
@api_bp.get("/schema")
def json_schema() -> Response:
    """Publish the JSON Schema for every request and response shape.

    Returns:
        Response: A map of schema name to JSON Schema document.

    Note:
        This is generated from the same classes that do the validating, so it
        **cannot drift** from the implementation — the perennial failure of
        hand-written API documentation.

        Note the two modes. ``mode="validation"`` describes what the server
        *accepts*; ``mode="serialization"`` describes what it *returns*, and is
        the only one that includes computed fields such as ``in_stock``. They
        answer different questions, and a complete OpenAPI document needs both.
    """
    return jsonify({
        "BookCreate": BookCreate.model_json_schema(),
        "BookUpdate": BookUpdate.model_json_schema(),
        "BookOut": BookOut.model_json_schema(mode="serialization"),
        "BookListOut": BookListOut.model_json_schema(mode="serialization"),
    })


@api_bp.get("/")
def index() -> Response:
    """Return a discoverable index of the API.

    Returns:
        Response: A map of relation names to URLs.
    """
    return jsonify({
        "version": "v1",
        "endpoints": {
            "books": url_for("api.list_books", _external=True),
            "authors": url_for("api.list_authors", _external=True),
            "schema": url_for("api.json_schema", _external=True),
        },
    })

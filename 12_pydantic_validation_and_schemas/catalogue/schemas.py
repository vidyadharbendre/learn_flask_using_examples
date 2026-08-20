"""
Day 12 — Pydantic schemas: the typed boundary of your application.
==================================================================

What this file replaces
-----------------------
Day 11's ``_validate_book()`` was ~90 lines of hand-written type checks:
``isinstance`` calls, range comparisons, an errors dict, and a special case
because ``isinstance(True, int)`` is ``True`` in Python. It worked, and it would
have to be rewritten for every new resource.

Pydantic replaces all of it with **declarations**. You describe the shape you
accept; Pydantic parses, coerces, validates, and reports every failure in a
structured form. It also generates a JSON Schema, which is most of an OpenAPI
document for free.

The idea worth taking away
--------------------------
**Validation is a boundary, not a sprinkling.** Untrusted data enters at exactly
one place, becomes a typed object there, and everything downstream can assume it
is correct. Compare with checking ``if not title`` in three different functions
because nobody is sure who validated what.

Why several schemas per resource
--------------------------------
It is tempting to write one ``Book`` model and use it everywhere. Don't — the
shapes genuinely differ:

===============  ===========================================================
``BookCreate``   what a client may **send** to create: no ``id``, no
                 timestamps (the server owns those), every field required
``BookUpdate``   what a client may send to **modify**: everything optional
``BookOut``      what the server **returns**: includes ``id``, timestamps and
                 computed fields the client can never set
===============  ===========================================================

Using one model for all three is how clients end up able to set ``id`` or
``created_at`` — a genuine security bug known as **mass assignment**.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    computed_field,
    field_serializer,
    field_validator,
    model_validator,
)

# -----------------------------------------------------------------------------
# Reusable annotated types
# -----------------------------------------------------------------------------
# `Annotated[type, Field(...)]` attaches constraints to a TYPE, so the same rule
# can be reused across schemas. Define your domain's vocabulary once.
ISBN = Annotated[str, Field(min_length=10, max_length=17,
                            description="ISBN-13; hyphens are allowed.")]
Title = Annotated[str, Field(min_length=1, max_length=200)]
Price = Annotated[Decimal, Field(ge=0, le=Decimal("100000"), decimal_places=2)]

# StrictInt, not int. Pydantic's default "lax" mode is helpfully permissive: it
# coerces "5" -> 5 and, because `bool` is a subclass of `int` in Python,
# True -> 1. So `{"stock": true}` would silently store one copy.
#
# This is the SAME trap Day 11 handled with an explicit
# `isinstance(x, bool)` check. StrictInt states the intent in the type instead:
# accept an integer, and nothing that merely resembles one.
#
# Verified: without StrictInt, `stock=True` passed validation as 1.
Stock = Annotated[StrictInt, Field(ge=0, le=1_000_000)]
Year = Annotated[StrictInt, Field(ge=1450, le=2100)]


class BookBase(BaseModel):
    """Fields shared by the create and update schemas.

    Attributes:
        isbn: ISBN-13, normalised to digits only.
        title: Book title, whitespace-trimmed.
        price: Exact decimal price.
        stock: Copies in stock.
        published_year: Year of publication.
        author_id: Owning author.
        tags: Free-form tags, lower-cased and de-duplicated.
    """

    model_config = ConfigDict(
        # Reject unknown keys instead of silently ignoring them. A client that
        # sends {"titel": "..."} should be told about the typo, not have its
        # data quietly dropped and wonder why the title never changed.
        extra="forbid",
        # Strip surrounding whitespace on every str field.
        str_strip_whitespace=True,
        # Validate again when a field is ASSIGNED, not only at construction.
        # Without this, `book.price = -5` would sail past your constraints.
        validate_assignment=True,
    )

    isbn: ISBN
    title: Title
    price: Price
    stock: Stock = 0
    published_year: Year
    author_id: StrictInt = Field(gt=0)
    tags: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("isbn")
    @classmethod
    def normalise_isbn(cls, value: str) -> str:
        """Strip hyphens and spaces, then check the result is 13 digits.

        A ``field_validator`` runs **after** the type coercion and the
        ``Field`` constraints for that field. It is the right place for rules
        that need real logic rather than a bound.

        Args:
            value: The raw ISBN as supplied.

        Returns:
            str: 13 digits, no separators.

        Raises:
            ValueError: when the cleaned value is not exactly 13 digits.

        Note:
            Raise plain ``ValueError`` — Pydantic catches it and folds it into
            the structured error report with the correct field location. You
            never raise ``ValidationError`` yourself.
        """
        cleaned = value.replace("-", "").replace(" ", "")
        if not cleaned.isdigit() or len(cleaned) != 13:
            raise ValueError("must be 13 digits (hyphens and spaces allowed)")
        return cleaned

    @field_validator("tags")
    @classmethod
    def normalise_tags(cls, value: list[str]) -> list[str]:
        """Lower-case, trim, drop blanks, and de-duplicate while keeping order.

        Args:
            value: Raw tag list.

        Returns:
            list[str]: Cleaned tags.

        Note:
            Normalising at the boundary is what stops ``"Fiction"``,
            ``"fiction"`` and ``" fiction "`` becoming three different tags in
            your database. Day 07 made the same point about email addresses.
        """
        seen: dict[str, None] = {}
        for tag in value:
            cleaned = tag.strip().lower()
            if cleaned:
                seen.setdefault(cleaned, None)
        return list(seen)


class BookCreate(BookBase):
    """Payload accepted by ``POST /books``.

    Every field of :class:`BookBase` is required except those with defaults.
    Notably absent: ``id``, ``created_at``, ``updated_at`` — the server owns
    those, and ``extra="forbid"`` means a client attempting to set them gets a
    422 rather than a silent surprise.
    """

    @model_validator(mode="after")
    def check_stock_for_old_books(self) -> "BookCreate":
        """Cross-field rule: pre-1900 titles cannot be freshly stocked.

        A ``model_validator(mode="after")`` runs once **every field has already
        validated**, so ``self`` is fully typed — this is where cross-field
        rules belong. (``mode="before"`` sees the raw input instead, useful for
        reshaping a payload.)

        Returns:
            BookCreate: The validated model.

        Raises:
            ValueError: when an implausible combination is supplied.
        """
        if self.published_year < 1900 and self.stock > 100:
            raise ValueError(
                "a pre-1900 title with more than 100 copies is probably a typo"
            )
        return self


class BookUpdate(BaseModel):
    """Payload accepted by ``PATCH /books/<id>`` — every field optional.

    Attributes:
        isbn / title / price / stock / published_year / author_id / tags:
            All optional; only the fields present are applied.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    isbn: ISBN | None = None
    title: Title | None = None
    price: Price | None = None
    stock: Stock | None = None
    published_year: Year | None = None
    author_id: StrictInt | None = Field(default=None, gt=0)
    tags: list[str] | None = Field(default=None, max_length=10)

    # Reuse the validators rather than copy them. The `check_fields=False`
    # argument is required because these field names are optional here, and
    # Pydantic otherwise complains that it cannot verify they exist.
    _normalise_isbn = field_validator("isbn")(BookBase.normalise_isbn.__func__)  # type: ignore[attr-defined]
    _normalise_tags = field_validator("tags")(BookBase.normalise_tags.__func__)  # type: ignore[attr-defined]

    @model_validator(mode="after")
    def at_least_one_field(self) -> "BookUpdate":
        """Reject an empty PATCH body.

        Returns:
            BookUpdate: The validated model.

        Raises:
            ValueError: when nothing was supplied.

        Note:
            Answering ``200 OK`` to a request that changed nothing hides client
            bugs. Say so explicitly.
        """
        if not self.model_fields_set:
            raise ValueError("provide at least one field to update")
        return self

    def changes(self) -> dict[str, Any]:
        """Return only the fields the client actually sent.

        Returns:
            dict[str, Any]: Field name → new value.

        Note:
            ``exclude_unset=True`` is the heart of correct ``PATCH`` semantics.
            Without it you cannot distinguish *"the client omitted stock"* from
            *"the client explicitly sent stock: 0"* — and you would reset every
            unmentioned field to its default, which is ``PUT`` behaviour.
        """
        return self.model_dump(exclude_unset=True)


class AuthorOut(BaseModel):
    """Author as returned by the API.

    Attributes:
        id: Primary key.
        name: Display name.
    """

    # from_attributes lets Pydantic read a SQLAlchemy object's ATTRIBUTES
    # rather than dict keys, so `AuthorOut.model_validate(author_orm_object)`
    # just works. Without it you would hand-build a dict first — which is
    # exactly the to_dict() boilerplate this replaces.
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class BookOut(BaseModel):
    """Book as returned by the API.

    Attributes:
        id: Primary key.
        isbn / title / price / stock / published_year: Stored values.
        author: The embedded author object.
        tags: Tags, exposed as a real list.
        created_at / updated_at: Timestamps, always serialised in UTC.
        in_stock: Derived convenience flag.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    isbn: str
    title: str
    price: Decimal
    stock: int
    published_year: int
    author: AuthorOut

    # `validation_alias` reads from a DIFFERENT source attribute than the field
    # name. The model stores a comma-separated string in `tags` and exposes a
    # list via the `tag_list` property; this maps one to the other with no
    # manual conversion in the view.
    tags: list[str] = Field(default_factory=list, validation_alias="tag_list")

    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def in_stock(self) -> bool:
        """Whether any copies remain.

        Returns:
            bool: True when ``stock`` is greater than zero.

        Note:
            A ``computed_field`` appears in the serialised output but is never
            accepted as input — exactly right for a derived value.

            It shows up in ``model_json_schema(mode="serialization")`` and
            **not** in the default validation-mode schema, because those two
            schemas answer different questions: "what may I send?" versus "what
            will I receive?". Generating OpenAPI means emitting both.
        """
        return self.stock > 0

    @field_serializer("price")
    def serialise_price(self, value: Decimal) -> str:
        """Emit money as a string.

        Args:
            value: The exact decimal price.

        Returns:
            str: e.g. ``"499.00"``.

        Note:
            Day 11's rule, now declared once on the schema instead of repeated
            in every ``to_dict``. JSON has a single numeric type (IEEE 754
            double), so ``12.10`` can reach a client as ``12.099999999999999``.
        """
        return f"{value:.2f}"

    @field_serializer("created_at", "updated_at")
    def serialise_timestamp(self, value: datetime) -> str:
        """Emit an ISO 8601 timestamp with an explicit UTC offset.

        Args:
            value: A naive or aware datetime.

        Returns:
            str: e.g. ``"2026-08-20T13:06:30+00:00"``.

        Note:
            SQLite has no timezone type, so values come back naive even with
            ``DateTime(timezone=True)``. Normalising here keeps the API contract
            identical on SQLite and PostgreSQL — see Day 11 §11.
        """
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()


class Page(BaseModel):
    """Pagination metadata.

    Attributes:
        page: Current page number.
        per_page: Items per page.
        total: Total matching items.
        pages: Total number of pages.
    """

    page: int
    per_page: int
    total: int
    pages: int


class BookListOut(BaseModel):
    """The collection envelope for ``GET /books``.

    Attributes:
        data: The page of books.
        meta: Pagination metadata.

    Note:
        Describing the *envelope* as a schema too means the whole response —
        not just the items — appears in your generated OpenAPI document.
    """

    data: list[BookOut]
    meta: Page


# -----------------------------------------------------------------------------
# Turning Pydantic errors into our API envelope
# -----------------------------------------------------------------------------
def format_validation_error(error: ValidationError) -> dict[str, list[str]]:
    """Convert a :class:`~pydantic.ValidationError` into ``{field: [messages]}``.

    Pydantic's raw ``errors()`` output is precise but noisy: each entry carries
    a ``loc`` **tuple** (because errors can be nested arbitrarily deep), a
    ``type``, a ``msg``, and the offending input. Clients want something flat.

    Args:
        error: The exception raised by ``model_validate``.

    Returns:
        dict[str, list[str]]: Field path → list of messages. Nested locations
        are joined with dots (``"items.0.price"``); errors that belong to the
        whole model land under ``"_root"``.

    Example:
        >>> # {"title": ["String should have at least 1 character"],
        >>> #  "price": ["Input should be greater than or equal to 0"]}
    """
    details: dict[str, list[str]] = {}
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "_root"
        details.setdefault(location, []).append(item["msg"])
    return details

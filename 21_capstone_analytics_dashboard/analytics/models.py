"""
Day 21 — Models: users, surveys, responses.
===========================================

Ownership flows ``User → Survey → Response``, so authorisation must walk the
chain (Day 14). Every decision here is one made earlier in the course:

- money-style exactness is irrelevant, but **scores are integers** with a
  ``CHECK`` constraint (Day 08)
- **aware UTC** timestamps, normalised on serialisation (Day 08/11)
- ``str``-backed enums stored as constrained strings (Day 14)
- ``ondelete`` chosen deliberately per relationship (Day 14)
"""

from __future__ import annotations

import enum
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from flask_login import UserMixin

from .extensions import db


def utcnow() -> datetime:
    """Return the current aware UTC time.

    Returns:
        datetime: Timezone-aware now.
    """
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    """Render a datetime as ISO 8601 in UTC with an explicit offset.

    Args:
        value: A naive or aware datetime, or ``None``.

    Returns:
        str | None: The formatted timestamp, or ``None``.

    Note:
        SQLite has no timezone type, so values come back naive even from a
        ``DateTime(timezone=True)`` column. Normalising here keeps the API
        contract identical across backends (Day 11 §11).
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class SurveyStatus(str, enum.Enum):
    """Whether a survey is accepting responses."""

    DRAFT = "draft"
    OPEN = "open"
    CLOSED = "closed"

    @property
    def label(self) -> str:
        """Human-readable name.

        Returns:
            str: e.g. ``"Open"``.
        """
        return self.value.title()


class User(UserMixin, db.Model):
    """An account that owns surveys.

    Attributes:
        id: Surrogate primary key.
        email: Unique login identifier.
        display_name: Name shown in the UI.
        password_hash: scrypt verifier (Day 13).
        api_token: Bearer token for the JSON API.
        active: Whether the account may sign in.
        created_at: Registration timestamp.
        surveys: Surveys owned by this user.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # A simple bearer token for the API. Day 15 used JWTs; an opaque random
    # token is the other reasonable design, and it has one clear advantage:
    # revocation is a DELETE, with no blocklist and no token_version needed.
    # The trade-off is a database lookup on every API request.
    api_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    surveys: Mapped[list["Survey"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan",
        order_by="Survey.created_at.desc()", lazy="selectin",
    )

    def set_password(self, password: str) -> None:
        """Hash and store a password.

        Args:
            password: The plaintext password; used and discarded.
        """
        from flask import current_app

        method = current_app.config.get("PASSWORD_HASH_METHOD")
        self.password_hash = (
            generate_password_hash(password, method=method) if method
            else generate_password_hash(password)
        )

    def check_password(self, password: str) -> bool:
        """Verify a password guess in constant time.

        Args:
            password: The plaintext guess.

        Returns:
            bool: True when it matches.
        """
        return check_password_hash(self.password_hash, password)

    def rotate_token(self) -> str:
        """Issue a new API token, invalidating the old one.

        Returns:
            str: The new token.
        """
        self.api_token = secrets.token_urlsafe(32)
        return self.api_token

    @property
    def is_active(self) -> bool:  # type: ignore[override]
        """Whether this account may sign in.

        Returns:
            bool: The ``active`` column.
        """
        return self.active

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API.

        Returns:
            dict[str, Any]: Identity only — never the hash or the token.
        """
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "survey_count": len(self.surveys),
        }

    def __repr__(self) -> str:
        """Return a representation containing no secret material.

        Returns:
            str: e.g. ``<User 1 'ana@example.com'>``.
        """
        return f"<User {self.id} {self.email!r}>"


class Survey(db.Model):
    """A feedback survey owned by one user.

    Attributes:
        id: Surrogate primary key.
        slug: Unguessable public identifier.
        title: Survey title.
        question: The question asked.
        status: Whether it accepts responses.
        owner_id: The owning user.
        owner: The related user.
        responses: Submitted responses.
        created_at: Creation timestamp.
    """

    __tablename__ = "surveys"

    id: Mapped[int] = mapped_column(primary_key=True)

    # A RANDOM public slug, not the sequential id. The public URL is shared with
    # respondents, and a sequential id invites walking /s/1, /s/2, /s/3 to
    # enumerate every survey in the system (Day 13's IDOR lesson, applied to a
    # deliberately public resource).
    slug: Mapped[str] = mapped_column(String(24), unique=True, nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(140), nullable=False)
    question: Mapped[str] = mapped_column(String(280), nullable=False)
    status: Mapped[SurveyStatus] = mapped_column(
        db.Enum(SurveyStatus, native_enum=False, length=20),
        nullable=False, default=SurveyStatus.DRAFT, index=True,
    )

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner: Mapped["User"] = relationship(back_populates="surveys")

    responses: Mapped[list["Response"]] = relationship(
        back_populates="survey", cascade="all, delete-orphan",
        order_by="Response.created_at.desc()",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @staticmethod
    def new_slug() -> str:
        """Generate an unguessable public slug.

        Returns:
            str: A URL-safe random string.
        """
        return secrets.token_urlsafe(12)[:16]

    @property
    def is_open(self) -> bool:
        """Whether this survey accepts responses.

        Returns:
            bool: True when open.
        """
        return self.status == SurveyStatus.OPEN

    def to_dict(self, *, include_stats: bool = False) -> dict[str, Any]:
        """Serialise for the API.

        Args:
            include_stats: Whether to compute and embed aggregate figures.

        Returns:
            dict[str, Any]: JSON-safe representation.
        """
        data: dict[str, Any] = {
            "id": self.id,
            "slug": self.slug,
            "title": self.title,
            "question": self.question,
            "status": self.status.value,
            "response_count": len(self.responses),
            "created_at": iso_utc(self.created_at),
        }
        if include_stats:
            data["stats"] = summarise(self.responses)
        return data

    def __repr__(self) -> str:
        """Return an unambiguous representation.

        Returns:
            str: e.g. ``<Survey 1 'Onboarding'>``.
        """
        return f"<Survey {self.id} {self.title!r}>"


class Response(db.Model):
    """One submitted answer.

    Attributes:
        id: Surrogate primary key.
        survey_id: The survey answered.
        survey: The related survey.
        score: A 0-10 rating.
        comment: Optional free text.
        respondent_hash: A pseudonymous identifier, never a raw IP.
        created_at: Submission timestamp.
    """

    __tablename__ = "responses"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(
        ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    survey: Mapped["Survey"] = relationship(back_populates="responses")

    score: Mapped[int] = mapped_column(nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # A one-way hash of the respondent's IP, never the address itself. It gives
    # coarse duplicate detection without storing personal data — the same
    # instinct as Day 18's log redaction, applied to the database.
    respondent_hash: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        # The database enforces the range whatever code path writes the row:
        # the web form, the API, a CSV import, or a script written next year
        # (Day 08 §9).
        CheckConstraint("score >= 0 AND score <= 10", name="ck_responses_score_range"),
        Index("ix_responses_survey_created", "survey_id", "created_at"),
    )

    @property
    def category(self) -> str:
        """The NPS bucket this score falls into.

        Returns:
            str: ``"promoter"``, ``"passive"`` or ``"detractor"``.

        Note:
            The standard Net Promoter Score bands. Encoding them once here
            means the dashboard, the API and the CSV export cannot disagree.
        """
        if self.score >= 9:
            return "promoter"
        if self.score >= 7:
            return "passive"
        return "detractor"

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API.

        Returns:
            dict[str, Any]: JSON-safe representation, with no respondent data.
        """
        return {
            "id": self.id,
            "score": self.score,
            "category": self.category,
            "comment": self.comment,
            "created_at": iso_utc(self.created_at),
        }


def summarise(responses: list[Response]) -> dict[str, Any]:
    """Aggregate responses into dashboard figures.

    Args:
        responses: The responses to summarise.

    Returns:
        dict[str, Any]: Counts, average score, NPS and a score histogram.

    Note:
        A **pure function** over a list — no database, no request, no clock.
        That is what makes it exhaustively testable in microseconds (Day 17),
        and it is why the NPS calculation has its own unit tests rather than
        being verified through an HTTP client.

        Guard the divisions: an empty survey must render, not raise
        ``ZeroDivisionError`` (Day 07 §10).
    """
    total = len(responses)
    if total == 0:
        return {"total": 0, "average": 0.0, "nps": 0,
                "promoters": 0, "passives": 0, "detractors": 0,
                "histogram": {str(n): 0 for n in range(11)}}

    promoters = sum(1 for r in responses if r.score >= 9)
    passives = sum(1 for r in responses if 7 <= r.score <= 8)
    detractors = total - promoters - passives

    histogram = {str(n): 0 for n in range(11)}
    for response in responses:
        histogram[str(response.score)] += 1

    return {
        "total": total,
        "average": round(sum(r.score for r in responses) / total, 2),
        # NPS is defined as %promoters − %detractors, rounded to a whole number.
        "nps": round((promoters - detractors) / total * 100),
        "promoters": promoters,
        "passives": passives,
        "detractors": detractors,
        "histogram": histogram,
    }

"""Day 21 — Pydantic schemas for the API boundary (Day 12)."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator

Score = Annotated[StrictInt, Field(ge=0, le=10,
                                   description="0 (worst) to 10 (best).")]


class ResponseCreate(BaseModel):
    """A submitted survey response.

    Attributes:
        score: The 0-10 rating.
        comment: Optional free text.

    Note:
        ``StrictInt`` rather than ``int``: in lax mode Pydantic coerces ``True``
        to ``1``, because ``bool`` subclasses ``int`` — so ``{"score": true}``
        would silently store a detractor (Day 12 §9).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    score: Score
    comment: str = Field(default="", max_length=1000)


class SurveyCreate(BaseModel):
    """A new survey.

    Attributes:
        title: Survey title.
        question: The question asked.
        status: Initial status.

    Note:
        ``extra="forbid"`` closes the mass-assignment hole: a client cannot set
        ``id``, ``slug`` or ``owner_id``, because the schema has no such fields
        and unknown keys are rejected (Day 12 §6).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=3, max_length=140)
    question: str = Field(min_length=5, max_length=280)
    status: str = Field(default="draft")

    @field_validator("status")
    @classmethod
    def known_status(cls, value: str) -> str:
        """Restrict the status to the allow-list.

        Args:
            value: The submitted status.

        Returns:
            str: The validated status.

        Raises:
            ValueError: for an unknown status.
        """
        allowed = {"draft", "open", "closed"}
        if value not in allowed:
            raise ValueError(f"must be one of {sorted(allowed)}")
        return value


class SurveyUpdate(BaseModel):
    """A partial survey update.

    Attributes:
        title / question / status: All optional.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=3, max_length=140)
    question: str | None = Field(default=None, min_length=5, max_length=280)
    status: str | None = None

    _known_status = field_validator("status")(SurveyCreate.known_status.__func__)  # type: ignore[attr-defined]

    def changes(self) -> dict[str, Any]:
        """Return only the fields the client actually sent.

        Returns:
            dict[str, Any]: Field name → new value.

        Note:
            ``exclude_unset=True`` is what makes ``PATCH`` correct: without it
            every unmentioned field is reset to its default, which is ``PUT``
            behaviour wearing a ``PATCH`` label (Day 12 §8).
        """
        return self.model_dump(exclude_unset=True)


def format_errors(error: ValidationError) -> dict[str, list[str]]:
    """Flatten a Pydantic error into ``{field: [messages]}``.

    Args:
        error: The raised validation error.

    Returns:
        dict[str, list[str]]: Field path → messages.
    """
    details: dict[str, list[str]] = {}
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "_root"
        details.setdefault(location, []).append(item["msg"])
    return details

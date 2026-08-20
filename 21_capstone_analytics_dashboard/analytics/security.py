"""
Day 21 — Authorisation, in one place (Day 14).

Ownership flows ``User → Survey → Response``. Centralising the checks means
every view calls the same function, and a reviewer can audit authorisation by
reading one file.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import abort, g, jsonify, request
from sqlalchemy import select

from flask_login import current_user

from .extensions import db
from .models import Survey, User


def owned_survey_or_404(survey_id: int, user: User | None = None) -> Survey:
    """Return a survey owned by ``user``, or abort with 404.

    Args:
        survey_id: Primary key from the URL.
        user: The owner to check against; defaults to ``current_user``.

    Returns:
        Survey: The survey, guaranteed to belong to the user.

    Raises:
        werkzeug.exceptions.NotFound: when it does not exist **or** belongs to
            someone else.

    Note:
        **404, not 403.** A 403 would confirm that survey 7 exists and belongs
        to another account — an information leak that helps an attacker map
        your data (Day 13 §10).
    """
    owner = user or current_user
    survey = db.session.get(Survey, survey_id)
    if survey is None or survey.owner_id != getattr(owner, "id", None):
        abort(404, description="No such survey.")
    return survey


def token_required(view: Callable[..., Any]) -> Callable[..., Any]:
    """Authenticate an API request with a bearer token.

    Args:
        view: The view to protect.

    Returns:
        Callable: The wrapped view, with ``g.api_user`` populated.

    Note:
        The token arrives in the ``Authorization`` header, **not a cookie**.
        That is why this API needs no CSRF protection: the whole CSRF attack
        depends on the browser attaching credentials automatically, and it never
        attaches an Authorization header (Day 15 §9).

        ``@wraps`` is mandatory — without it every protected view registers
        under the endpoint name ``"wrapper"`` and Flask raises on the second one
        (Day 12 §12).
    """

    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            # `status` is part of the envelope EVERY error uses. Omitting it
            # here was caught by test_api_errors_share_one_envelope — which is
            # exactly what that test is for: one inconsistent shape means
            # clients need a second parser (Day 11 §8).
            return jsonify(error={
                "status": 401,
                "code": "authorization_required",
                "message": "Send: Authorization: Bearer <api_token>",
            }), 401

        token = header.removeprefix("Bearer ").strip()
        user = db.session.execute(
            select(User).where(User.api_token == token)
        ).scalar_one_or_none()

        if user is None or not user.active:
            # One message for both failures — an unknown token and a suspended
            # account must be indistinguishable (Day 13 §9).
            return jsonify(error={
                "status": 401, "code": "invalid_token",
                "message": "Invalid or revoked token.",
            }), 401

        g.api_user = user
        return view(*args, **kwargs)

    return wrapper

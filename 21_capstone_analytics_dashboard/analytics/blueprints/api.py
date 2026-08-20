"""
Day 21 — The JSON API (Days 11, 12, 15).

Token-authenticated, versioned, Pydantic-validated, with one error envelope.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify, request, url_for
from flask.typing import ResponseReturnValue
from pydantic import ValidationError
from sqlalchemy import select
from werkzeug.exceptions import HTTPException

from ..extensions import csrf, db
from ..models import Response as SurveyResponse
from ..models import Survey, SurveyStatus, summarise
from ..schemas import ResponseCreate, SurveyCreate, SurveyUpdate, format_errors
from ..security import owned_survey_or_404, token_required

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

# Exempt from CSRF because this API authenticates with a HEADER, not a cookie.
# CSRF exists because browsers attach cookies automatically; they never attach
# an Authorization header (Day 15 §9).
csrf.exempt(api_bp)


def error(status: int, code: str, message: str, **extra: Any) -> ResponseReturnValue:
    """Build the standard error envelope (Day 11 §8).

    Args:
        status: HTTP status code.
        code: Stable machine-readable identifier.
        message: Human-readable explanation.
        **extra: Additional structured detail.

    Returns:
        ResponseReturnValue: The JSON error and status code.
    """
    body: dict[str, Any] = {"status": status, "code": code, "message": message}
    body.update(extra)
    return jsonify(error=body), status


@api_bp.errorhandler(HTTPException)
def http_error(exc: HTTPException) -> ResponseReturnValue:
    """Render HTTP errors from this blueprint as JSON.

    Args:
        exc: The raised exception.

    Returns:
        ResponseReturnValue: The JSON envelope.
    """
    return error(exc.code or 500, "http_error", exc.description or exc.name)


# Register for specific codes too: a generic HTTPException handler loses to an
# app-level coded handler, because Flask resolves by status code first
# (Day 10 §9).
for _status in (400, 401, 403, 404, 405, 409, 415, 422, 429):
    api_bp.register_error_handler(_status, http_error)


def _json_body() -> dict[str, Any] | None:
    """Return the parsed JSON object body.

    Returns:
        dict[str, Any] | None: The body, or ``None`` when absent/not an object.
    """
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


@api_bp.get("/")
@token_required
def index() -> ResponseReturnValue:
    """Return a discoverable index of the API.

    Returns:
        ResponseReturnValue: Endpoint map and the caller's identity.
    """
    return jsonify({
        "version": "v1",
        "authenticated_as": g.api_user.to_dict(),
        "endpoints": {
            "surveys": url_for("api.list_surveys", _external=True),
            "stats": "/api/v1/surveys/<id>/stats",
            "responses": "/api/v1/surveys/<id>/responses",
        },
    })


@api_bp.get("/surveys")
@token_required
def list_surveys() -> ResponseReturnValue:
    """List the caller's surveys, paginated.

    Returns:
        ResponseReturnValue: ``200`` with ``data`` and ``meta``.
    """
    statement = (
        select(Survey).where(Survey.owner_id == g.api_user.id)
        .order_by(Survey.created_at.desc())
    )
    if (status := request.args.get("status", "")) in {s.value for s in SurveyStatus}:
        statement = statement.where(Survey.status == SurveyStatus(status))

    page = max(1, request.args.get("page", default=1, type=int))
    # Cap the page size: an uncapped one is a denial-of-service request you
    # invited (Day 11 §9).
    per_page = max(1, min(request.args.get("per_page", default=20, type=int), 100))
    pagination = db.paginate(statement, page=page, per_page=per_page, error_out=False)

    return jsonify(
        data=[survey.to_dict() for survey in pagination.items],
        meta={"page": pagination.page, "per_page": pagination.per_page,
              "total": pagination.total, "pages": pagination.pages},
    )


@api_bp.post("/surveys")
@token_required
def create_survey() -> ResponseReturnValue:
    """Create a survey.

    Returns:
        ResponseReturnValue: ``201`` with the survey and a ``Location`` header.
    """
    body = _json_body()
    if body is None:
        return error(415, "unsupported_media_type", "Send a JSON object body.")

    try:
        payload = SurveyCreate.model_validate(body)
    except ValidationError as exc:
        return error(422, "validation_error", "The request body failed validation.",
                     details=format_errors(exc))

    survey = Survey(
        slug=Survey.new_slug(), title=payload.title, question=payload.question,
        status=SurveyStatus(payload.status), owner_id=g.api_user.id,
    )
    db.session.add(survey)
    db.session.commit()

    return jsonify(survey.to_dict()), 201, {
        "Location": url_for("api.get_survey", survey_id=survey.id, _external=True)
    }


@api_bp.get("/surveys/<int:survey_id>")
@token_required
def get_survey(survey_id: int) -> ResponseReturnValue:
    """Return one survey with its statistics.

    Args:
        survey_id: Primary key from the URL.

    Returns:
        ResponseReturnValue: ``200`` with the survey, or ``404``.
    """
    survey = owned_survey_or_404(survey_id, g.api_user)
    return jsonify(survey.to_dict(include_stats=True))


@api_bp.patch("/surveys/<int:survey_id>")
@token_required
def update_survey(survey_id: int) -> ResponseReturnValue:
    """Apply a partial update.

    Args:
        survey_id: Primary key from the URL.

    Returns:
        ResponseReturnValue: ``200`` with the updated survey.
    """
    survey = owned_survey_or_404(survey_id, g.api_user)

    body = _json_body()
    if body is None:
        return error(415, "unsupported_media_type", "Send a JSON object body.")

    try:
        payload = SurveyUpdate.model_validate(body)
    except ValidationError as exc:
        return error(422, "validation_error", "The request body failed validation.",
                     details=format_errors(exc))

    changes = payload.changes()
    if not changes:
        return error(422, "validation_error", "Provide at least one field to update.")

    for field, value in changes.items():
        setattr(survey, field, SurveyStatus(value) if field == "status" else value)
    db.session.commit()

    from .surveys import public_survey_payload
    from ..extensions import cache
    cache.delete_memoized(public_survey_payload, survey.slug)

    return jsonify(survey.to_dict())


@api_bp.delete("/surveys/<int:survey_id>")
@token_required
def delete_survey(survey_id: int) -> ResponseReturnValue:
    """Delete a survey.

    Args:
        survey_id: Primary key from the URL.

    Returns:
        ResponseReturnValue: ``204`` with an empty body.
    """
    survey = owned_survey_or_404(survey_id, g.api_user)
    db.session.delete(survey)
    db.session.commit()
    return "", 204


@api_bp.get("/surveys/<int:survey_id>/responses")
@token_required
def list_responses(survey_id: int) -> ResponseReturnValue:
    """List a survey's responses.

    Args:
        survey_id: Primary key from the URL.

    Returns:
        ResponseReturnValue: ``200`` with ``data`` and ``meta``.
    """
    survey = owned_survey_or_404(survey_id, g.api_user)

    page = max(1, request.args.get("page", default=1, type=int))
    per_page = max(1, min(request.args.get("per_page", default=50, type=int), 200))
    pagination = db.paginate(
        select(SurveyResponse).where(SurveyResponse.survey_id == survey.id)
        .order_by(SurveyResponse.created_at.desc()),
        page=page, per_page=per_page, error_out=False,
    )

    return jsonify(
        data=[r.to_dict() for r in pagination.items],
        meta={"page": pagination.page, "total": pagination.total,
              "pages": pagination.pages},
    )


@api_bp.post("/surveys/<int:survey_id>/responses")
@token_required
def create_response(survey_id: int) -> ResponseReturnValue:
    """Submit a response through the API.

    Args:
        survey_id: Primary key from the URL.

    Returns:
        ResponseReturnValue: ``201`` with the created response.
    """
    survey = owned_survey_or_404(survey_id, g.api_user)
    if not survey.is_open:
        # 409: this conflicts with the current STATE of the resource, rather
        # than the request being malformed (Day 11 §7).
        return error(409, "survey_closed", "That survey is not accepting responses.")

    body = _json_body()
    if body is None:
        return error(415, "unsupported_media_type", "Send a JSON object body.")

    try:
        payload = ResponseCreate.model_validate(body)
    except ValidationError as exc:
        return error(422, "validation_error", "The request body failed validation.",
                     details=format_errors(exc))

    response = SurveyResponse(
        survey_id=survey.id, score=payload.score, comment=payload.comment,
    )
    db.session.add(response)
    db.session.commit()
    return jsonify(response.to_dict()), 201


@api_bp.get("/surveys/<int:survey_id>/stats")
@token_required
def survey_stats(survey_id: int) -> ResponseReturnValue:
    """Return aggregate statistics for a survey.

    Args:
        survey_id: Primary key from the URL.

    Returns:
        ResponseReturnValue: ``200`` with counts, average, NPS and histogram.
    """
    survey = owned_survey_or_404(survey_id, g.api_user)
    return jsonify(survey_id=survey.id, slug=survey.slug,
                   **summarise(survey.responses))

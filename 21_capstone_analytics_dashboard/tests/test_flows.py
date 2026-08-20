"""Day 21 — End-to-end flows and API contract."""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from analytics.extensions import db
from analytics.models import Response, Survey, SurveyStatus, User


@pytest.mark.integration
def test_full_lifecycle(client: FlaskClient, user: User, login) -> None:
    """Create a survey, collect a public response, read the stats."""
    login()

    created = client.post("/surveys/", data={
        "title": "New survey", "question": "How are we doing today?", "status": "open",
    })
    assert created.status_code == 303

    survey = db.session.execute(
        db.select(Survey).where(Survey.title == "New survey")
    ).scalar_one()

    # A stranger responds through the public link — no login required.
    public = client.post(f"/s/{survey.slug}", data={"score": "9", "comment": "Great"})
    assert public.status_code == 303

    detail = client.get(f"/surveys/{survey.id}").get_data(as_text=True)
    assert "Great" in detail

    db.session.refresh(survey)
    assert len(survey.responses) == 1
    assert survey.responses[0].category == "promoter"
    # A hash is stored, never the address itself.
    assert survey.responses[0].respondent_hash
    assert "127.0.0.1" not in survey.responses[0].respondent_hash


def test_draft_survey_is_not_publicly_reachable(
    client: FlaskClient, user: User
) -> None:
    """A draft returns 404, not 403 — the slug is the only credential."""
    draft = Survey(slug="draft-slug", title="Draft", question="Not open yet",
                   status=SurveyStatus.DRAFT, owner_id=user.id)
    db.session.add(draft)
    db.session.commit()

    assert client.get("/s/draft-slug").status_code == 404
    assert client.post("/s/draft-slug", data={"score": "9"}).status_code == 404


def test_unknown_slug_is_404(client: FlaskClient) -> None:
    """An unguessable slug that does not exist reveals nothing."""
    assert client.get("/s/does-not-exist").status_code == 404


def test_score_out_of_range_is_rejected(
    client: FlaskClient, user: User, survey: Survey
) -> None:
    """The form rejects it, and so would the database CHECK constraint."""
    response = client.post(f"/s/{survey.slug}", data={"score": "11"})
    assert response.status_code == 422


def test_csv_export_headers_and_rows(
    client: FlaskClient, user: User, survey: Survey, login
) -> None:
    """The export is a download, and contains one row per response."""
    login()
    response = client.get(f"/surveys/{survey.id}/export.csv")

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "attachment" in response.headers["Content-Disposition"]

    lines = response.get_data(as_text=True).strip().splitlines()
    assert len(lines) == 4                    # header + 3 responses
    assert "Score" in lines[0]


# -----------------------------------------------------------------------------
# API contract
# -----------------------------------------------------------------------------
def test_api_create_returns_201_with_location(
    client: FlaskClient, user: User, auth_headers: dict[str, str]
) -> None:
    """A correct POST: 201, a Location header, and the created object."""
    response = client.post("/api/v1/surveys", headers=auth_headers, json={
        "title": "From the API", "question": "Does this work well?", "status": "open",
    })
    assert response.status_code == 201
    assert "Location" in response.headers
    assert response.get_json()["title"] == "From the API"


def test_api_rejects_mass_assignment(
    client: FlaskClient, user: User, other_user: User, auth_headers: dict[str, str]
) -> None:
    """A client cannot set server-owned fields.

    `extra="forbid"` on the schema turns an attempt to set `owner_id` or `slug`
    into a 422 rather than a silent privilege change (Day 12 §6).
    """
    response = client.post("/api/v1/surveys", headers=auth_headers, json={
        "title": "Sneaky", "question": "Can I set the owner?",
        "owner_id": other_user.id, "slug": "chosen-by-me", "id": 999,
    })
    assert response.status_code == 422
    details = response.get_json()["error"]["details"]
    assert {"owner_id", "slug", "id"} <= set(details)


def test_api_rejects_boolean_score(
    client: FlaskClient, user: User, survey: Survey, auth_headers: dict[str, str]
) -> None:
    """`{"score": true}` must not become a 1.

    `bool` subclasses `int` in Python, so lax validation would accept it.
    StrictInt does not (Day 12 §9).
    """
    response = client.post(f"/api/v1/surveys/{survey.id}/responses",
                           headers=auth_headers, json={"score": True})
    assert response.status_code == 422


@pytest.mark.parametrize("score", [-1, 11, 100])
def test_api_rejects_out_of_range_scores(
    client: FlaskClient, user: User, survey: Survey,
    auth_headers: dict[str, str], score: int
) -> None:
    """The 0-10 range is enforced at the schema.

    Args:
        score: An out-of-range value.
    """
    response = client.post(f"/api/v1/surveys/{survey.id}/responses",
                           headers=auth_headers, json={"score": score})
    assert response.status_code == 422


def test_api_patch_only_changes_what_was_sent(
    client: FlaskClient, user: User, survey: Survey, auth_headers: dict[str, str]
) -> None:
    """PATCH semantics: exclude_unset means untouched fields survive."""
    original_question = survey.question

    response = client.patch(f"/api/v1/surveys/{survey.id}", headers=auth_headers,
                            json={"title": "Renamed"})
    assert response.status_code == 200

    body = response.get_json()
    assert body["title"] == "Renamed"
    assert body["question"] == original_question       # NOT reset to a default


def test_api_empty_patch_is_422(
    client: FlaskClient, user: User, survey: Survey, auth_headers: dict[str, str]
) -> None:
    """Answering 200 to a request that changed nothing hides client bugs."""
    assert client.patch(f"/api/v1/surveys/{survey.id}",
                        headers=auth_headers, json={}).status_code == 422


def test_api_closed_survey_conflict(
    client: FlaskClient, user: User, survey: Survey, auth_headers: dict[str, str]
) -> None:
    """409 for a state conflict, not 422 — the payload was fine."""
    survey.status = SurveyStatus.CLOSED
    db.session.commit()

    response = client.post(f"/api/v1/surveys/{survey.id}/responses",
                           headers=auth_headers, json={"score": 9})
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "survey_closed"


def test_api_stats_match_the_pure_function(
    client: FlaskClient, user: User, survey: Survey, auth_headers: dict[str, str]
) -> None:
    """The API reports exactly what summarise() computes."""
    stats = client.get(f"/api/v1/surveys/{survey.id}/stats", headers=auth_headers).get_json()
    assert stats["total"] == 3
    assert stats["promoters"] == 1        # 10
    assert stats["passives"] == 1         # 8
    assert stats["detractors"] == 1       # 3
    assert stats["nps"] == 0


def test_api_errors_share_one_envelope(
    client: FlaskClient, user: User, auth_headers: dict[str, str]
) -> None:
    """Every failure has the same shape, so clients need one parser."""
    for response in [
        client.get("/api/v1/surveys/9999", headers=auth_headers),
        client.post("/api/v1/surveys", headers=auth_headers, json={"title": "x"}),
        client.get("/api/v1/surveys"),
    ]:
        body = response.get_json()
        assert "error" in body
        assert {"status", "code", "message"} <= set(body["error"])


def test_api_per_page_is_capped(
    client: FlaskClient, user: User, survey: Survey, auth_headers: dict[str, str]
) -> None:
    """An uncapped page size is a denial-of-service request you invited."""
    meta = client.get("/api/v1/surveys?per_page=999999",
                      headers=auth_headers).get_json()["meta"]
    assert meta["per_page"] == 100


def test_health_and_readiness(client: FlaskClient) -> None:
    """Two probes, two different questions."""
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_security_headers_and_request_id(client: FlaskClient) -> None:
    """Every response carries the standard headers and a correlation id."""
    response = client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Request-ID"]


def test_inbound_request_id_is_reused(client: FlaskClient) -> None:
    """A correlation id from upstream is preserved across the request."""
    response = client.get("/", headers={"X-Request-ID": "upstream-abc123"})
    assert response.headers["X-Request-ID"] == "upstream-abc123"

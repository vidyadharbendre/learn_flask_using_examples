"""
Day 21 — Authorisation: the tests that matter most (Days 13, 14).

Every test here would pass trivially if ``@login_required`` were the only check,
and every one would then represent a data breach.
"""

from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

from analytics.blueprints.auth import is_safe_redirect_url
from analytics.extensions import db
from analytics.models import Survey, SurveyStatus, User


def _survey_for(owner: User, title: str = "Private") -> Survey:
    """Create a survey owned by ``owner``.

    Args:
        owner: The owning user.
        title: The survey title.

    Returns:
        Survey: The persisted survey.
    """
    survey = Survey(slug=f"slug-{owner.id}-{title}", title=title,
                    question="Secret question here",
                    status=SurveyStatus.OPEN, owner_id=owner.id)
    db.session.add(survey)
    db.session.commit()
    return survey


def test_cannot_view_another_users_survey(
    client: FlaskClient, user: User, other_user: User, login
) -> None:
    """Someone else's survey is a 404, not a 403.

    403 would confirm it exists and belongs to another account.
    """
    theirs = _survey_for(other_user)
    login()
    assert client.get(f"/surveys/{theirs.id}").status_code == 404


def test_cannot_edit_or_delete_another_users_survey(
    client: FlaskClient, user: User, other_user: User, login
) -> None:
    """Writes are authorised as carefully as reads — and must not take effect."""
    theirs = _survey_for(other_user)
    survey_id = theirs.id
    login()

    assert client.get(f"/surveys/{survey_id}/edit").status_code == 404
    assert client.post(f"/surveys/{survey_id}/delete").status_code == 404
    assert db.session.get(Survey, survey_id) is not None


def test_cannot_export_another_users_data(
    client: FlaskClient, user: User, other_user: User, login
) -> None:
    """Export is a read of everything — it must be authorised too."""
    theirs = _survey_for(other_user)
    login()
    assert client.get(f"/surveys/{theirs.id}/export.csv").status_code == 404


def test_dashboard_lists_only_own_surveys(
    client: FlaskClient, user: User, other_user: User, survey: Survey, login
) -> None:
    """The list query is scoped by owner, not filtered in the template."""
    _survey_for(other_user, title="TheirSecret")
    login()

    body = client.get("/surveys/").get_data(as_text=True)
    assert "Onboarding experience" in body
    assert "TheirSecret" not in body


def test_api_token_scopes_to_its_owner(
    client: FlaskClient, user: User, other_user: User, survey: Survey
) -> None:
    """The API applies the same ownership rules as the HTML views."""
    theirs = _survey_for(other_user)
    headers = {"Authorization": f"Bearer {user.api_token}"}

    titles = {row["title"] for row in client.get("/api/v1/surveys", headers=headers).get_json()["data"]}
    assert "Onboarding experience" in titles
    assert "Private" not in titles

    assert client.get(f"/api/v1/surveys/{theirs.id}", headers=headers).status_code == 404


def test_api_rejects_missing_and_invalid_tokens(client: FlaskClient, user: User) -> None:
    """Both failures are 401, and indistinguishable."""
    assert client.get("/api/v1/surveys").status_code == 401
    bad = client.get("/api/v1/surveys", headers={"Authorization": "Bearer nonsense"})
    assert bad.status_code == 401
    assert bad.get_json()["error"]["code"] == "invalid_token"


def test_rotating_the_token_revokes_the_old_one(
    client: FlaskClient, user: User, login
) -> None:
    """Rotation IS revocation for an opaque token."""
    old_token = user.api_token
    assert client.get("/api/v1/surveys",
                      headers={"Authorization": f"Bearer {old_token}"}).status_code == 200

    login()
    assert client.post("/auth/token").status_code == 303

    assert client.get("/api/v1/surveys",
                      headers={"Authorization": f"Bearer {old_token}"}).status_code == 401


def test_open_redirect_is_blocked(client: FlaskClient, user: User) -> None:
    """?next= must only ever be a relative path on this host."""
    response = client.post("/auth/login?next=https://evil.example/phish",
                           data={"email": "ana@example.com",
                                 "password": "CorrectHorseBattery1"})
    assert response.headers["Location"] == "/surveys/"


def test_redirect_validator_rejects_hostile_targets() -> None:
    """Unit-test the validator directly — no app or client needed."""
    assert is_safe_redirect_url("/surveys/")
    for hostile in ("https://evil.example", "//evil.example", "/\\evil.example",
                    "javascript:alert(1)", "", None):
        assert not is_safe_redirect_url(hostile), hostile


def test_anonymous_is_redirected_and_api_gets_401(client: FlaskClient) -> None:
    """Each audience gets the answer it can use."""
    page = client.get("/surveys/")
    assert page.status_code == 302 and "/auth/login" in page.headers["Location"]
    assert client.get("/api/v1/surveys").status_code == 401

"""Day 14 — Authentication tests."""

from __future__ import annotations

from conftest import AuthActions
from flask import Flask
from flask.testing import FlaskClient

from taskman.blueprints.auth import is_safe_redirect_url
from taskman.extensions import db
from taskman.models import User


def test_password_is_hashed_not_stored(app: Flask, user: User) -> None:
    """The plaintext password must never appear in the database."""
    assert user.password_hash != "CorrectHorseBattery1"
    assert "CorrectHorseBattery1" not in user.password_hash
    assert user.check_password("CorrectHorseBattery1")
    assert not user.check_password("wrong")


def test_same_password_gets_different_hashes(app: Flask) -> None:
    """Salting means two users with one password get two hashes."""
    first, second = User(email="a@x.com", display_name="A"), User(email="b@x.com", display_name="B")
    first.set_password("identical")
    second.set_password("identical")
    assert first.password_hash != second.password_hash
    assert first.check_password("identical") and second.check_password("identical")


def test_login_succeeds(client: FlaskClient, user: User, auth: AuthActions) -> None:
    """Valid credentials redirect to the project list."""
    response = auth.login()
    assert response.status_code == 303
    assert response.headers["Location"] == "/projects/"


def test_login_failures_are_indistinguishable(client: FlaskClient, user: User, auth: AuthActions) -> None:
    """An unknown email and a wrong password give the same answer."""
    unknown = auth.login(email="nobody@example.com", password="whatever")
    wrong = auth.login(password="wrongpassword")

    assert unknown.status_code == wrong.status_code == 401
    assert b"Invalid email or password" in unknown.data
    assert b"Invalid email or password" in wrong.data


def test_inactive_user_cannot_log_in(client: FlaskClient, user: User, auth: AuthActions) -> None:
    """A suspended account is refused."""
    user.active = False
    db.session.commit()
    assert auth.login().status_code == 401


def test_anonymous_is_redirected_to_login(client: FlaskClient) -> None:
    """A protected page bounces anonymous visitors, with ?next= preserved."""
    response = client.get("/tasks/")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]
    assert "next=" in response.headers["Location"]


def test_api_gets_401_json_not_a_redirect(client: FlaskClient) -> None:
    """API clients need a status code, not an HTML login page."""
    response = client.get("/api/v1/tasks")
    assert response.status_code == 401
    assert response.get_json()["error"]["status"] == 401


def test_logout_requires_post(client: FlaskClient, user: User, auth: AuthActions) -> None:
    """A GET logout would be CSRF-able and prefetchable."""
    auth.login()
    assert client.get("/auth/logout").status_code == 405
    assert auth.logout().status_code == 303


def test_open_redirect_is_blocked(client: FlaskClient, user: User) -> None:
    """?next= must only ever be a relative path on this host."""
    response = client.post(
        "/auth/login?next=https://evil.example/phish",
        data={"email": "ana@example.com", "password": "CorrectHorseBattery1"},
    )
    # The hostile target is discarded; we land on the safe default.
    assert response.headers["Location"] == "/projects/"


def test_safe_redirect_is_honoured(client: FlaskClient, user: User) -> None:
    """A relative path is accepted."""
    response = client.post(
        "/auth/login?next=/tasks/",
        data={"email": "ana@example.com", "password": "CorrectHorseBattery1"},
    )
    assert response.headers["Location"] == "/tasks/"


def test_redirect_validator_rejects_hostile_targets() -> None:
    """Unit-test the validator directly — no app or client needed."""
    assert is_safe_redirect_url("/tasks/")
    assert is_safe_redirect_url("/projects/1?x=2")
    for hostile in ("https://evil.example", "//evil.example", "http://evil.example",
                    "/\\evil.example", "javascript:alert(1)", "", None):
        assert not is_safe_redirect_url(hostile), hostile

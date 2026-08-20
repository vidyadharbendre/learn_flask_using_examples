"""Day 21 — Fixtures (Day 17)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from analytics import create_app
from analytics.extensions import db as _db
from analytics.models import Response, Survey, SurveyStatus, User
from analytics.settings import Settings


@pytest.fixture
def app() -> Iterator[Flask]:
    """Provide a fresh app with an in-memory database.

    Yields:
        Flask: A configured testing application.
    """
    settings = Settings(env="testing", database_url="sqlite:///:memory:",
                        secret_key="testing-key-that-is-long-enough")
    application = create_app(settings)
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    """Provide a test client.

    Args:
        app: The application fixture.

    Returns:
        FlaskClient: A client requiring no network.
    """
    return app.test_client()


@pytest.fixture
def user(app: Flask) -> User:
    """Create a signed-up user.

    Args:
        app: The application fixture.

    Returns:
        User: A persisted user with a known password and token.
    """
    account = User(email="ana@example.com", display_name="Ana Rao")
    account.set_password("CorrectHorseBattery1")
    account.rotate_token()
    _db.session.add(account)
    _db.session.commit()
    return account


@pytest.fixture
def other_user(app: Flask) -> User:
    """Create a second user, for authorisation tests.

    Args:
        app: The application fixture.

    Returns:
        User: A different persisted user.
    """
    account = User(email="vik@example.com", display_name="Vikram Shah")
    account.set_password("CorrectHorseBattery2")
    account.rotate_token()
    _db.session.add(account)
    _db.session.commit()
    return account


@pytest.fixture
def survey(user: User) -> Survey:
    """Create an open survey with three responses.

    Args:
        user: The owning user.

    Returns:
        Survey: The persisted survey.
    """
    item = Survey(slug="test-slug-1234", title="Onboarding experience",
                  question="How likely are you to recommend us?",
                  status=SurveyStatus.OPEN, owner_id=user.id)
    _db.session.add(item)
    _db.session.flush()
    for score in (10, 8, 3):
        _db.session.add(Response(survey_id=item.id, score=score, comment=""))
    _db.session.commit()
    return item


@pytest.fixture
def auth_headers(user: User) -> dict[str, str]:
    """Return API headers for ``user``.

    Args:
        user: The authenticating user.

    Returns:
        dict[str, str]: An Authorization header.
    """
    return {"Authorization": f"Bearer {user.api_token}"}


@pytest.fixture
def login(client: FlaskClient) -> "LoginHelper":
    """Provide a sign-in helper.

    Args:
        client: The test client.

    Returns:
        LoginHelper: Callable that signs a user in.
    """
    return LoginHelper(client)


class LoginHelper:
    """Signs users in during tests."""

    def __init__(self, client: FlaskClient) -> None:
        """Store the client.

        Args:
            client: The test client.
        """
        self._client = client

    def __call__(self, email: str = "ana@example.com",
                 password: str = "CorrectHorseBattery1") -> object:
        """Sign in.

        Args:
            email: The account email.
            password: The account password.

        Returns:
            object: The response.
        """
        return self._client.post("/auth/login",
                                 data={"email": email, "password": password})

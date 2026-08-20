"""
Day 14 — Shared pytest fixtures.
================================

This file is why the application factory (Day 10) exists. Because
``create_app("testing")`` builds a *fresh* app with an in-memory database,
every test gets a clean world — no ordering dependencies, no leftover rows, no
"works alone, fails in the suite" flakes.

``conftest.py`` is discovered automatically by pytest; fixtures defined here are
available to every test module without importing anything.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from taskman import create_app
from taskman.extensions import db as _db
from taskman.models import Project, Task, TaskPriority, TaskStatus, User


@pytest.fixture
def app() -> Iterator[Flask]:
    """Provide a fresh application backed by an in-memory database.

    Yields:
        Flask: A configured testing application.

    Note:
        ``function`` scope (the default) means a brand-new app and database per
        test. That is slower than a shared one and worth every millisecond: a
        suite where tests can affect each other produces failures that depend on
        execution order, which are miserable to debug.
    """
    application = create_app("testing")
    with application.app_context():
        _db.create_all()
        yield application
        # Explicit teardown. The in-memory database vanishes with the
        # connection anyway, but being explicit keeps the fixture correct if
        # someone later points it at a file.
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    """Provide a test client for ``app``.

    Args:
        app: The application fixture.

    Returns:
        FlaskClient: A client that issues requests without a network or server.
    """
    return app.test_client()


@pytest.fixture
def user(app: Flask) -> User:
    """Create and return a signed-up user.

    Args:
        app: The application fixture (ensures an app context).

    Returns:
        User: A persisted user with a known password.
    """
    account = User(email="ana@example.com", display_name="Ana Rao")
    account.set_password("CorrectHorseBattery1")
    _db.session.add(account)
    _db.session.commit()
    return account


@pytest.fixture
def other_user(app: Flask) -> User:
    """Create a second user, used for authorisation tests.

    Args:
        app: The application fixture.

    Returns:
        User: A different persisted user.
    """
    account = User(email="vik@example.com", display_name="Vikram Shah")
    account.set_password("CorrectHorseBattery2")
    _db.session.add(account)
    _db.session.commit()
    return account


@pytest.fixture
def project(user: User) -> Project:
    """Create a project owned by ``user``.

    Args:
        user: The owning user.

    Returns:
        Project: A persisted project with one task.
    """
    item = Project(name="Website relaunch", description="Ship it.", owner_id=user.id)
    _db.session.add(item)
    _db.session.flush()

    task = Task(
        title="Write copy", project_id=item.id,
        priority=TaskPriority.HIGH, due_on=date.today() + timedelta(days=3),
    )
    task.mark(TaskStatus.TODO)
    _db.session.add(task)
    _db.session.commit()
    return item


@pytest.fixture
def auth(client: FlaskClient) -> "AuthActions":
    """Provide helpers for signing in and out.

    Args:
        client: The test client.

    Returns:
        AuthActions: A small helper object.

    Note:
        Wrapping login in a fixture keeps the tests about *behaviour* rather
        than about form mechanics. CSRF is disabled in ``TestingConfig``, which
        is why no token is scraped here.
    """
    return AuthActions(client)


class AuthActions:
    """Convenience wrapper for authentication in tests."""

    def __init__(self, client: FlaskClient) -> None:
        """Store the client.

        Args:
            client: The test client to act on.
        """
        self._client = client

    def login(self, email: str = "ana@example.com",
              password: str = "CorrectHorseBattery1") -> TestResponse:
        """Sign in.

        Args:
            email: The account email.
            password: The account password.

        Returns:
            TestResponse: The response, so callers can assert on status and body.
        """
        return self._client.post(
            "/auth/login", data={"email": email, "password": password}
        )

    def logout(self) -> TestResponse:
        """Sign out.

        Returns:
            TestResponse: The response.
        """
        return self._client.post("/auth/logout")

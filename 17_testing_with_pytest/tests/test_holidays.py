"""
Day 17 — Mocking an external service.
=====================================

The rule: **a test must never make a real network call.** Such a test is slow,
fails when you are offline, fails when the third party has an incident, may cost
money, and tells you nothing about *your* code.

``monkeypatch`` is pytest's built-in tool for this. It replaces an attribute for
the duration of one test and restores it afterwards — automatically, even if the
test fails. That automatic restoration is why you should prefer it to assigning
over a module attribute by hand.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests

from bookings.services import fetch_public_holidays


class FakeResponse:
    """A stand-in for ``requests.Response``.

    Only the three members the code under test actually uses are implemented.
    A fake should be as small as the contract it is standing in for — anything
    more is maintenance for no benefit.
    """

    def __init__(self, payload: Any, status_code: int = 200) -> None:
        """Initialise the fake.

        Args:
            payload: What ``.json()`` should return.
            status_code: The HTTP status to report.
        """
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        """Return the canned payload.

        Returns:
            Any: The payload supplied at construction.
        """
        return self._payload

    def raise_for_status(self) -> None:
        """Raise for 4xx/5xx, like the real thing.

        Raises:
            requests.HTTPError: when the status is an error.
        """
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


def test_returns_dates_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful response is parsed into a list of dates."""
    payload = [
        {"date": "2026-01-26", "name": "Republic Day"},
        {"date": "2026-08-15", "name": "Independence Day"},
    ]

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        """Stand in for ``requests.get``.

        Args:
            url: The requested URL.
            **kwargs: Everything else the caller passed.

        Returns:
            FakeResponse: The canned response.
        """
        return FakeResponse(payload)

    # Patch the name IN THE MODULE UNDER TEST'S namespace, not the library's.
    # `bookings.services` did `import requests`, so it looks the function up as
    # requests.get at call time — patching `requests.get` works here. Had it
    # done `from requests import get`, you would have to patch
    # `bookings.services.get` instead. Patch where the name is LOOKED UP.
    monkeypatch.setattr(requests, "get", fake_get)

    assert fetch_public_holidays(2026) == ["2026-01-26", "2026-08-15"]


def test_a_timeout_is_always_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The call must specify a timeout.

    `requests` has NO default timeout. A hung third party will hang your worker
    until something kills it — one of the most common causes of an outage that
    has nothing to do with your own code.

    This test asserts on **how** the collaborator was called, which is exactly
    when checking an interaction (rather than a return value) is justified.
    """
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        """Record the call and return an empty payload.

        Args:
            url: The requested URL.
            **kwargs: Keyword arguments, captured for assertion.

        Returns:
            FakeResponse: An empty successful response.
        """
        captured.update(url=url, **kwargs)
        return FakeResponse([])

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_public_holidays(2026)

    assert "timeout" in captured, "requests.get was called without a timeout"
    assert 0 < captured["timeout"] <= 10


@pytest.mark.parametrize("failure", [
    pytest.param(requests.ConnectionError("network down"), id="connection_error"),
    pytest.param(requests.Timeout("too slow"), id="timeout"),
    pytest.param(ValueError("not json"), id="malformed_json"),
])
def test_degrades_gracefully(monkeypatch: pytest.MonkeyPatch, failure: Exception) -> None:
    """Any third-party failure yields an empty list, never an exception.

    Testing the **failure paths** of an external dependency is the whole reason
    to mock it. In production these are the paths that actually run, and they
    are the ones you can never exercise by calling the real service.
    """
    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        """Always fail.

        Args:
            url: The requested URL.
            **kwargs: Ignored.

        Returns:
            FakeResponse: Never — this always raises.

        Raises:
            Exception: The parametrised failure.
        """
        raise failure

    monkeypatch.setattr(requests, "get", fake_get)
    assert fetch_public_holidays(2026) == []


def test_http_error_status_yields_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 500 from the third party is handled, not propagated."""
    monkeypatch.setattr(requests, "get",
                        lambda url, **kwargs: FakeResponse(None, status_code=500))
    assert fetch_public_holidays(2026) == []


def test_endpoint_survives_a_dead_dependency(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API answers 200 even when the holiday service is down.

    This is the assertion that matters to users: a nicety being unavailable must
    not take the endpoint with it.
    """
    monkeypatch.setattr(requests, "get",
                        lambda url, **kwargs: (_ for _ in ()).throw(requests.Timeout()))

    response = client.get("/api/holidays/2026")
    assert response.status_code == 200
    assert response.get_json()["dates"] == []

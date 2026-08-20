"""
Day 17 — Testing the HTTP layer.

The **top** of the test pyramid: slowest, fewest, and focused on what only an
HTTP test can prove — status codes, JSON shape, and that the wiring is correct.

The *rules* are already covered by ``test_booking_rules.py``. Re-testing every
rule through the client would be slower and would tell you nothing new.
"""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from bookings.models import Room
from conftest import TimeHelper


def test_health(client: FlaskClient) -> None:
    """The liveness probe answers."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_list_rooms(client: FlaskClient, rooms: list[Room]) -> None:
    """Rooms are listed alphabetically."""
    response = client.get("/api/rooms")
    assert response.status_code == 200

    names = [row["name"] for row in response.get_json()["data"]]
    assert names == ["Alpha", "Beta", "Gamma"]


@pytest.mark.integration
def test_create_booking_returns_201(
    client: FlaskClient, room: Room, at: TimeHelper
) -> None:
    """A valid booking is created and echoed back.

    Note:
        This test cannot use ``now=FROZEN_NOW`` — the view calls the real clock.
        So it books a time in the **future relative to now**, which is stable
        whenever the suite runs. When a value cannot be injected, choose inputs
        that do not depend on it.
    """
    from datetime import timedelta, timezone
    from datetime import datetime as real_datetime

    start = real_datetime.now(timezone.utc) + timedelta(days=1)
    end = start + timedelta(hours=1)

    response = client.post("/api/bookings", json={
        "room_id": room.id, "booked_by": "Ana Rao", "attendees": 2,
        "starts_at": start.isoformat(), "ends_at": end.isoformat(),
    })

    assert response.status_code == 201
    body = response.get_json()
    assert body["booked_by"] == "Ana Rao"
    assert body["room"] == "Alpha"
    assert "id" in body


@pytest.mark.integration
def test_conflict_returns_409_not_422(
    client: FlaskClient, room: Room, at: TimeHelper
) -> None:
    """A clash is 409; other rule failures are 422.

    This is precisely what an HTTP test is *for*: the rule itself is already
    tested at the service layer, but only this can verify the view maps that
    outcome to the right status code.
    """
    from datetime import timedelta, timezone
    from datetime import datetime as real_datetime

    start = real_datetime.now(timezone.utc) + timedelta(days=2)
    payload = {
        "room_id": room.id, "booked_by": "First", "attendees": 1,
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=1)).isoformat(),
    }

    assert client.post("/api/bookings", json=payload).status_code == 201
    assert client.post("/api/bookings", json={**payload, "booked_by": "Second"}).status_code == 409

    # A different failure mode gets a different code.
    over_capacity = {**payload, "attendees": 99,
                     "starts_at": (start + timedelta(days=1)).isoformat(),
                     "ends_at": (start + timedelta(days=1, hours=1)).isoformat()}
    assert client.post("/api/bookings", json=over_capacity).status_code == 422


def test_malformed_dates_return_422(client: FlaskClient, room: Room) -> None:
    """Unparseable input is rejected, not raised as a 500."""
    response = client.post("/api/bookings", json={
        "room_id": room.id, "booked_by": "Ana", "attendees": 1,
        "starts_at": "not-a-date", "ends_at": "also-not-a-date",
    })
    assert response.status_code == 422
    assert "ISO 8601" in response.get_json()["error"]


def test_empty_body_does_not_crash(client: FlaskClient) -> None:
    """A missing body is a 422, never a 500.

    Robustness tests like this are cheap and catch the crashes that show up in
    your error tracker on day one of production.
    """
    assert client.post("/api/bookings", json={}).status_code == 422


def test_bookings_can_be_filtered_by_room(
    client: FlaskClient, booked_room: Room, rooms: list[Room]
) -> None:
    """The room filter narrows the result set."""
    assert len(client.get("/api/bookings").get_json()["data"]) == 1
    assert len(client.get(f"/api/bookings?room_id={booked_room.id}").get_json()["data"]) == 1
    assert len(client.get(f"/api/bookings?room_id={rooms[2].id}").get_json()["data"]) == 0

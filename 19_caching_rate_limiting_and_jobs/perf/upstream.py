"""
Day 19 — A deliberately slow, deliberately unreliable upstream service.
=======================================================================

Every performance lesson needs something slow to make fast. This module stands
in for a third-party API — a weather provider, a payment gateway, a partner
feed — and it behaves the way real ones do: it takes hundreds of milliseconds,
it occasionally fails, and it counts how often you call it.

The call counter is the point. "Is my cache working?" is not a matter of
opinion; it is a number.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

# Simulated network latency. Real third-party APIs are rarely faster than this.
UPSTREAM_LATENCY_S = 0.35


@dataclass
class CallCounter:
    """Thread-safe counter of upstream calls.

    Attributes:
        counts: Calls per city.
        failures: Simulated failures per city.

    Note:
        The lock matters. A Flask dev server with threads enabled — and any
        production WSGI server — handles requests concurrently, so ``+= 1``
        from several threads can lose updates. This is the same class of
        problem as Day 07's concurrent file writes, in miniature.
    """

    counts: dict[str, int] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, key: str, failed: bool = False) -> None:
        """Record one call.

        Args:
            key: The city requested.
            failed: Whether the call failed.
        """
        with self._lock:
            self.counts[key] = self.counts.get(key, 0) + 1
            if failed:
                self.failures[key] = self.failures.get(key, 0) + 1

    def total(self) -> int:
        """Return the total number of calls.

        Returns:
            int: Sum across all cities.
        """
        with self._lock:
            return sum(self.counts.values())

    def reset(self) -> None:
        """Clear all counts."""
        with self._lock:
            self.counts.clear()
            self.failures.clear()


calls = CallCounter()


class UpstreamError(Exception):
    """Raised when the simulated upstream service fails."""


def fetch_weather(city: str, *, fail_rate: float = 0.0) -> dict[str, Any]:
    """Fetch weather for ``city`` — slowly, and sometimes unsuccessfully.

    Args:
        city: The city name.
        fail_rate: Probability of a simulated failure, 0.0-1.0.

    Returns:
        dict[str, Any]: Weather data.

    Raises:
        UpstreamError: on a simulated failure.

    Note:
        The ``time.sleep`` is standing in for a network round trip. In a real
        application this is where you would call ``requests.get(..., timeout=…)``
        — and the timeout is not optional (Day 17 §7).
    """
    time.sleep(UPSTREAM_LATENCY_S)

    if fail_rate and random.random() < fail_rate:
        calls.record(city, failed=True)
        raise UpstreamError(f"Upstream failed for {city}.")

    calls.record(city)

    # Deterministic pseudo-data, so repeated calls are comparable.
    seed = sum(ord(character) for character in city.lower())
    return {
        "city": city.title(),
        "temperature_c": 18 + (seed % 15),
        "humidity": 40 + (seed % 45),
        "condition": ["Clear", "Cloudy", "Rain", "Haze"][seed % 4],
        "fetched_at": time.time(),
    }

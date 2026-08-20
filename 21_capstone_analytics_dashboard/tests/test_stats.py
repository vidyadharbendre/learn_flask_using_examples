"""
Day 21 — The pure function, tested exhaustively (Day 17 §4).

``summarise`` needs no app, no database and no request. These are the fastest
and most valuable tests in the suite, and the NPS formula is exactly the kind of
arithmetic that is easy to get subtly wrong and hard to notice.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from analytics.models import summarise


@dataclass
class FakeResponse:
    """A minimal stand-in for a Response row.

    ``summarise`` only reads ``.score``, so the fake implements only that. A
    fake should be as small as the contract it stands in for.
    """

    score: int


def responses(*scores: int) -> list[FakeResponse]:
    """Build fake responses from scores.

    Args:
        *scores: The scores to wrap.

    Returns:
        list[FakeResponse]: The fakes.
    """
    return [FakeResponse(score) for score in scores]


def test_empty_survey_does_not_divide_by_zero() -> None:
    """An empty survey must render, not raise."""
    stats = summarise([])
    assert stats["total"] == 0
    assert stats["average"] == 0.0
    assert stats["nps"] == 0
    assert stats["histogram"]["7"] == 0


@pytest.mark.parametrize(
    ("scores", "promoters", "passives", "detractors", "nps"),
    [
        # The NPS bands: 9-10 promoter, 7-8 passive, 0-6 detractor.
        pytest.param((10, 9), 2, 0, 0, 100, id="all_promoters"),
        pytest.param((0, 1, 6), 0, 0, 3, -100, id="all_detractors"),
        pytest.param((7, 8), 0, 2, 0, 0, id="all_passive_is_zero"),
        pytest.param((10, 8, 3), 1, 1, 1, 0, id="one_of_each"),
        pytest.param((10, 10, 10, 0), 3, 0, 1, 50, id="three_to_one"),
        # THE BOUNDARIES — where band bugs live.
        pytest.param((9,), 1, 0, 0, 100, id="nine_is_promoter"),
        pytest.param((8,), 0, 1, 0, 0, id="eight_is_passive"),
        pytest.param((7,), 0, 1, 0, 0, id="seven_is_passive"),
        pytest.param((6,), 0, 0, 1, -100, id="six_is_detractor"),
    ],
)
def test_nps_bands(scores: tuple[int, ...], promoters: int, passives: int,
                   detractors: int, nps: int) -> None:
    """Each score falls in the correct NPS band.

    The single-score cases pin the boundaries. A ``>=`` slipping to ``>`` would
    move the whole promoter band by one and silently change every reported
    figure — a bug nobody notices until a customer queries the number.
    """
    stats = summarise(responses(*scores))
    assert stats["promoters"] == promoters
    assert stats["passives"] == passives
    assert stats["detractors"] == detractors
    assert stats["nps"] == nps


def test_counts_always_sum_to_total() -> None:
    """A property that must hold for every possible input."""
    for scores in [(0,), (5, 5), (0, 7, 9, 10), tuple(range(11))]:
        stats = summarise(responses(*scores))
        assert stats["promoters"] + stats["passives"] + stats["detractors"] == stats["total"]


def test_histogram_covers_every_score() -> None:
    """All eleven buckets exist, even when unused."""
    stats = summarise(responses(5, 5, 10))
    assert set(stats["histogram"]) == {str(n) for n in range(11)}
    assert stats["histogram"]["5"] == 2
    assert stats["histogram"]["10"] == 1
    assert stats["histogram"]["3"] == 0


def test_average_is_rounded_to_two_places() -> None:
    """The average is a display value, rounded once, in Python."""
    assert summarise(responses(10, 9, 8))["average"] == 9.0
    assert summarise(responses(10, 9))["average"] == 9.5
    assert summarise(responses(1, 2))["average"] == 1.5

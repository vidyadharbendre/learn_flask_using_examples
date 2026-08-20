"""
Day 17 — Testing a pure function.
=================================

``overlaps()`` needs no app, no database, no request and no clock. That makes
these the **fastest and most valuable tests in the suite**: they run in
microseconds, they never flake, and they pin down the one piece of genuinely
tricky logic in the whole system.

**Test pure logic first.** If a rule is hard to test, that is usually a signal
that it is tangled with I/O — and extracting it improves the code as well as
the tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bookings.models import overlaps

BASE = datetime(2026, 6, 15, tzinfo=timezone.utc)


def at(hour: float) -> datetime:
    """Return a time on the base day.

    Args:
        hour: Hours past midnight; may be fractional.

    Returns:
        datetime: An aware UTC datetime.
    """
    return BASE + timedelta(hours=hour)


# -----------------------------------------------------------------------------
# parametrize: one test function, a table of cases
# -----------------------------------------------------------------------------
# Each row becomes a SEPARATE test with its own pass/fail. That matters:
# a loop inside one test stops at the first failure and reports one result,
# whereas this reports "3 failed, 9 passed" and names exactly which cases broke.
#
# The `id=` strings turn `pytest -v` output into readable documentation, and let
# you re-run one case with `pytest -k "back_to_back"`.
@pytest.mark.parametrize(
    ("a_start", "a_end", "b_start", "b_end", "expected"),
    [
        # --- the obvious cases ---
        pytest.param(9, 10, 11, 12, False, id="entirely_before"),
        pytest.param(11, 12, 9, 10, False, id="entirely_after"),
        pytest.param(9, 12, 10, 11, True, id="b_inside_a"),
        pytest.param(10, 11, 9, 12, True, id="a_inside_b"),
        pytest.param(9, 11, 10, 12, True, id="partial_overlap_left"),
        pytest.param(10, 12, 9, 11, True, id="partial_overlap_right"),
        pytest.param(9, 11, 9, 11, True, id="identical"),

        # --- THE BOUNDARIES: where bugs actually live ---
        # Half-open intervals [start, end): back-to-back meetings do NOT clash.
        pytest.param(9, 10, 10, 11, False, id="back_to_back_no_clash"),
        pytest.param(10, 11, 9, 10, False, id="back_to_back_reversed"),
        # One minute of genuine overlap DOES clash.
        pytest.param(9, 10.5, 10, 11, True, id="one_minute_overlap"),
        # Shared start, or shared end.
        pytest.param(9, 11, 9, 10, True, id="same_start"),
        pytest.param(9, 11, 10, 11, True, id="same_end"),
    ],
)
def test_overlaps(a_start: float, a_end: float, b_start: float,
                  b_end: float, expected: bool) -> None:
    """Overlap detection across every interval arrangement.

    The boundary cases are the point. ``9-10`` versus ``10-11`` is exactly where
    a ``<=`` instead of a ``<`` would silently block every back-to-back meeting
    in the building — a bug that looks like nothing in code review.
    """
    assert overlaps(at(a_start), at(a_end), at(b_start), at(b_end)) is expected


def test_overlaps_is_symmetric() -> None:
    """Argument order must not matter.

    A **property** test rather than an example test: instead of listing cases,
    it asserts a rule that must hold for all of them. Properties often catch
    what examples miss.
    """
    cases = [(9, 10, 11, 12), (9, 12, 10, 11), (9, 10, 10, 11), (9, 11, 10, 12)]
    for a_start, a_end, b_start, b_end in cases:
        forward = overlaps(at(a_start), at(a_end), at(b_start), at(b_end))
        backward = overlaps(at(b_start), at(b_end), at(a_start), at(a_end))
        assert forward == backward, f"asymmetric for {(a_start, a_end, b_start, b_end)}"

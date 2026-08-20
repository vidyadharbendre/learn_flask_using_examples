"""
Day 07 — Storage layer: a JSON-file repository.
===============================================

Why a separate module?
----------------------
This is the **repository pattern**. Every function that touches persistence
lives here, behind an interface expressed in terms of the domain
(``add_expense``, ``list_expenses``) rather than the storage mechanism
(``open``, ``json.dump``).

The payoff arrives on Day 08. When JSON is swapped for SQLAlchemy, *only this
file changes*: ``app.py`` keeps calling ``list_expenses()`` and never learns
that a database appeared. Code that scatters ``json.load`` calls through its
views cannot make that switch without a rewrite.

Concurrency honesty
-------------------
A JSON file is genuinely fine for a single-process learning app and genuinely
wrong for production. Two gunicorn workers writing at once will interleave and
corrupt the file. The atomic-write helper below narrows the window but does not
close it — only a real database gives you transactions. That is precisely the
motivation for Day 08.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final, TypedDict, cast

# The data file sits beside this module, so the app works no matter which
# directory you launch it from. `Path(__file__).parent` is the reliable way to
# say "next to this file"; a relative path like "data/expenses.json" resolves
# against the CURRENT WORKING DIRECTORY and breaks the moment someone cds.
DATA_DIR: Final[Path] = Path(__file__).parent / "data"
DATA_FILE: Final[Path] = DATA_DIR / "expenses.json"

CATEGORIES: Final[tuple[str, ...]] = (
    "groceries", "transport", "utilities", "rent",
    "eating-out", "health", "education", "other",
)

PAYMENT_METHODS: Final[tuple[str, ...]] = ("upi", "card", "cash", "netbanking")


class Expense(TypedDict):
    """One recorded expense.

    Attributes:
        id: UUID4 string assigned on creation.
        spent_on: ISO date string (``YYYY-MM-DD``). Stored as text because JSON
            has no date type; converted back to :class:`datetime.date` on read.
        description: What the money was for.
        category: One of :data:`CATEGORIES`.
        amount_paise: Amount in **paise** (integer), never rupees as a float.
        payment_method: One of :data:`PAYMENT_METHODS`.
        note: Optional free text.
        created_at: ISO timestamp of when the record was written.
    """

    id: str
    spent_on: str
    description: str
    category: str
    amount_paise: int
    payment_method: str
    note: str
    created_at: str


# -----------------------------------------------------------------------------
# Money: the one rule that saves you from rounding bugs
# -----------------------------------------------------------------------------
# NEVER store money as a float. 0.1 + 0.2 == 0.30000000000000004, and those
# fractions of a paisa accumulate until your totals disagree with reality.
# Store the smallest currency unit as an INTEGER and divide only when
# displaying. Databases call this DECIMAL/NUMERIC; in Python, int paise.
def rupees_to_paise(rupees: float | int | str) -> int:
    """Convert a rupee amount to integer paise.

    Args:
        rupees: A value such as ``249.50``, ``250`` or ``"249.5"``.

    Returns:
        int: The amount in paise, e.g. ``24950``.

    Example:
        >>> rupees_to_paise("249.50")
        24950
        >>> rupees_to_paise(0.1) + rupees_to_paise(0.2) == rupees_to_paise(0.3)
        True
    """
    return int(round(float(rupees) * 100))


def paise_to_rupees(paise: int) -> float:
    """Convert integer paise back to rupees for display.

    Args:
        paise: Amount in paise.

    Returns:
        float: Amount in rupees, e.g. ``249.5``.
    """
    return paise / 100


# -----------------------------------------------------------------------------
# File access
# -----------------------------------------------------------------------------
def _read_all() -> list[Expense]:
    """Load every expense from disk.

    Returns:
        list[Expense]: All stored records, or ``[]`` when the file is missing or
        unreadable.

    Note:
        A missing file is a normal first-run state, not an error — do not make
        the user create an empty file by hand. A *corrupt* file is different:
        we return empty rather than crashing every page, but we do not silently
        delete the user's data either.
    """
    if not DATA_FILE.exists():
        return []
    try:
        with DATA_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _write_all(expenses: list[Expense]) -> None:
    """Write every expense to disk **atomically**.

    Writing straight into ``expenses.json`` means that a crash mid-write leaves
    a truncated file and the user loses everything. Instead we write to a
    temporary file in the same directory and then :func:`os.replace` it, which
    is atomic on POSIX and Windows: readers see either the whole old file or
    the whole new one, never a half-written one.

    Args:
        expenses: The complete list to persist.

    Warning:
        Atomic *replacement* is not the same as a *transaction*. Two processes
        writing concurrently still lose one of the updates — last writer wins.
        Day 08's database solves this properly.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Same directory as the target: os.replace is only atomic within one
    # filesystem, and /tmp is often a different mount.
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=DATA_DIR, delete=False, suffix=".tmp"
    )
    try:
        with handle:
            json.dump(expenses, handle, indent=2, ensure_ascii=False)
        os.replace(handle.name, DATA_FILE)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


# -----------------------------------------------------------------------------
# Public API — the interface app.py depends on
# -----------------------------------------------------------------------------
def add_expense(
    *,
    spent_on: date,
    description: str,
    category: str,
    amount_paise: int,
    payment_method: str,
    note: str = "",
) -> Expense:
    """Persist a new expense and return it.

    Keyword-only arguments (note the bare ``*``) prevent the classic
    ``add_expense(d, "lunch", 30000, "eating-out")`` argument-order bug: with
    four strings and an int, a transposition would be silently accepted.

    Args:
        spent_on: The date the money was spent.
        description: What it was for.
        category: One of :data:`CATEGORIES`.
        amount_paise: Amount in paise, already converted.
        payment_method: One of :data:`PAYMENT_METHODS`.
        note: Optional free text.

    Returns:
        Expense: The stored record, including its generated ``id``.
    """
    expense: Expense = {
        "id": str(uuid.uuid4()),
        "spent_on": spent_on.isoformat(),
        "description": description.strip(),
        "category": category,
        "amount_paise": amount_paise,
        "payment_method": payment_method,
        "note": note.strip(),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    expenses = _read_all()
    expenses.append(expense)
    _write_all(expenses)
    return expense


def list_expenses(
    *, category: str = "", month: str = "", query: str = ""
) -> list[Expense]:
    """Return expenses matching the given filters, newest first.

    Args:
        category: Restrict to one category. Empty means all.
        month: Restrict to one month as ``YYYY-MM``. Empty means all.
        query: Case-insensitive substring match on description and note.

    Returns:
        list[Expense]: Matching records sorted by ``spent_on`` descending.

    Note:
        Filtering in Python is fine for hundreds of rows and hopeless for
        millions — every record is read into memory on every request. Day 08
        pushes this into SQL, where an index does the work.
    """
    results = _read_all()

    if category:
        results = [e for e in results if e["category"] == category]
    if month:
        # ISO dates sort and prefix-match correctly as strings, which is the
        # main reason to store YYYY-MM-DD rather than DD/MM/YYYY.
        results = [e for e in results if e["spent_on"].startswith(month)]
    if query:
        needle = query.strip().lower()
        results = [
            e for e in results
            if needle in e["description"].lower() or needle in e["note"].lower()
        ]

    return sorted(results, key=lambda e: (e["spent_on"], e["created_at"]), reverse=True)


def get_expense(expense_id: str) -> Expense | None:
    """Fetch one expense by id.

    Args:
        expense_id: The UUID string.

    Returns:
        Expense | None: The record, or ``None`` when it does not exist. Return
        ``None`` rather than raising — the *caller* decides whether a missing
        record is a 404 or a no-op.
    """
    return next((e for e in _read_all() if e["id"] == expense_id), None)


def delete_expense(expense_id: str) -> bool:
    """Delete one expense.

    Args:
        expense_id: The UUID string.

    Returns:
        bool: ``True`` when a record was removed, ``False`` when the id was
        unknown — which lets the view flash an accurate message.
    """
    expenses = _read_all()
    remaining = [e for e in expenses if e["id"] != expense_id]
    if len(remaining) == len(expenses):
        return False
    _write_all(remaining)
    return True


def summarise(expenses: list[Expense]) -> dict[str, Any]:
    """Aggregate a list of expenses into dashboard figures.

    Aggregation lives here, not in the template. Templates should *display*
    numbers, never compute them — logic in Jinja cannot be unit-tested, and
    Day 17 will thank you.

    Args:
        expenses: The (already filtered) records to summarise.

    Returns:
        dict[str, Any]: ``total_paise``, ``count``, ``by_category`` (sorted,
        descending, with percentages), ``by_month`` (chronological) and
        ``largest``.
    """
    total = sum(e["amount_paise"] for e in expenses)

    per_category: defaultdict[str, int] = defaultdict(int)
    per_month: defaultdict[str, int] = defaultdict(int)
    for expense in expenses:
        per_category[expense["category"]] += expense["amount_paise"]
        per_month[expense["spent_on"][:7]] += expense["amount_paise"]

    by_category = sorted(
        (
            {
                "category": name,
                "total_paise": amount,
                # Guard the division: an empty list must not raise
                # ZeroDivisionError and 500 the dashboard.
                "percent": round(amount / total * 100, 1) if total else 0.0,
            }
            for name, amount in per_category.items()
        ),
        # cast() tells mypy the sort key is orderable. The values really are
        # ints; the dict is only typed dict[str, object] because it mixes types.
        key=lambda row: cast(int, row["total_paise"]),
        reverse=True,
    )

    return {
        "total_paise": total,
        "count": len(expenses),
        "by_category": by_category,
        "by_month": sorted(
            ({"month": m, "total_paise": t} for m, t in per_month.items()),
            key=lambda row: row["month"],
        ),
        "largest": max(expenses, key=lambda e: e["amount_paise"], default=None),
    }

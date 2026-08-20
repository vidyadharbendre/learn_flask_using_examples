"""
Day 07 — Week 1 Project: a personal expense tracker.
=====================================================

Real-world scenario
-------------------
Track what you spend, categorise it, see where the money goes, and export it to
CSV for your accountant. No database yet — persistence is a JSON file behind a
repository module, which is the last stop before Day 08 makes it real.

This is a **consolidation day**. Every technique comes from Days 01-06:

===========  ==================================================================
Day 01       app object, ``/health``, ``if __name__ == "__main__"``
Day 02       routes, converters, ``url_for``, ``abort(404)``, error handlers
Day 03       template inheritance, macros, custom filters, context processors
Day 04       POST/Redirect/GET, 303/422, server-side validation, ``MAX_CONTENT_LENGTH``
Day 05       Flask-WTF forms, custom validators, CSRF, GET filter form
Day 06       session for UI preferences, flash messages
===========  ==================================================================

New today
---------
1. **Layered structure**: ``storage.py`` (persistence) / ``forms.py``
   (validation) / ``app.py`` (HTTP). Each layer is replaceable.
2. **Money as integer paise** — never floats.
3. **File downloads**: building a CSV with the right headers.
4. **CLI commands** registered with ``@app.cli.command``.

How to run
----------
From the repository root::

    source .venv/bin/activate
    flask --app 07_project_expense_tracker/app.py seed        # optional demo data
    flask --app 07_project_expense_tracker/app.py run --port 5007 --debug

Architecture in one line
------------------------
``app.py`` knows about HTTP. ``storage.py`` knows about persistence. Neither
knows the other's internals — which is why Day 08 can replace the entire storage
layer without touching a single view.
"""

from __future__ import annotations

import csv
import io
import os
import random
from datetime import date, datetime, timedelta
from typing import Any

import click
from flask import (
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue
from werkzeug.wrappers import Response as WerkzeugResponse

import storage
from forms import CATEGORY_CHOICES, ExpenseForm, FilterForm

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-not-for-production"),
    MAX_CONTENT_LENGTH=128 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


# -----------------------------------------------------------------------------
# Template filters (Day 03)
# -----------------------------------------------------------------------------
@app.template_filter("inr")
def inr(paise: int) -> str:
    """Format integer paise as Indian Rupees with lakh/crore grouping.

    Args:
        paise: Amount in paise, e.g. ``24950``.

    Returns:
        str: e.g. ``"₹249.50"`` or ``"₹1,24,999.00"``.
    """
    rupees = storage.paise_to_rupees(paise)
    whole, fraction = divmod(round(abs(rupees) * 100), 100)
    digits = str(whole)

    if len(digits) > 3:
        last3, rest = digits[-3:], digits[:-3]
        pairs: list[str] = []
        while len(rest) > 2:
            pairs.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            pairs.insert(0, rest)
        digits = ",".join([*pairs, last3])

    sign = "-" if rupees < 0 else ""
    return f"{sign}₹{digits}.{fraction:02d}"


@app.template_filter("nice_date")
def nice_date(iso_date: str) -> str:
    """Render an ISO date string as ``15 Aug 2026``.

    Args:
        iso_date: A ``YYYY-MM-DD`` string as stored in JSON.

    Returns:
        str: Human-readable date, or the input unchanged if unparseable —
        a display filter must never crash the page over one bad row.
    """
    try:
        return date.fromisoformat(iso_date).strftime("%d %b %Y")
    except (ValueError, TypeError):
        return str(iso_date)


@app.template_filter("nice_month")
def nice_month(iso_month: str) -> str:
    """Render ``2026-08`` as ``Aug 2026``.

    Args:
        iso_month: A ``YYYY-MM`` string.

    Returns:
        str: Human-readable month, or the input unchanged if unparseable.
    """
    try:
        return datetime.strptime(iso_month, "%Y-%m").strftime("%b %Y")
    except (ValueError, TypeError):
        return str(iso_month)


@app.context_processor
def inject_globals() -> dict[str, Any]:
    """Expose shared values to every template (Day 03).

    Returns:
        dict[str, Any]: Template globals, including the session-stored
        ``compact`` display preference from Day 06.
    """
    return {
        "app_name": "Paisa — Expense Tracker",
        "categories": dict(CATEGORY_CHOICES),
        "compact": session.get("compact", False),
        "today": date.today(),
    }


# -----------------------------------------------------------------------------
# Views
# -----------------------------------------------------------------------------
def _current_filters() -> dict[str, str]:
    """Read the active filters from the query string.

    Returns:
        dict[str, str]: ``q``, ``category`` and ``month``, each defaulting to "".

    Note:
        Filters live in the **URL**, not the session. That makes a filtered view
        shareable, bookmarkable and back-button friendly — three things a
        session-stored filter silently breaks.
    """
    return {
        "q": request.args.get("q", "").strip(),
        "category": request.args.get("category", "").strip(),
        "month": request.args.get("month", "").strip(),
    }


@app.route("/", methods=["GET", "POST"])
def dashboard() -> ResponseReturnValue:
    """Show the dashboard and handle new-expense submissions.

    Combines the Day 04 POST/Redirect/GET pattern with the Day 05 form class and
    the Day 03 presentation layer.

    Returns:
        str | WerkzeugResponse: Rendered dashboard (200, or 422 when the form
        has errors) or a 303 redirect after a successful add.
    """
    form = ExpenseForm()

    if form.validate_on_submit():
        expense = storage.add_expense(
            spent_on=form.spent_on.data or date.today(),
            description=form.description.data or "",
            category=form.category.data or "other",
            amount_paise=form.amount_paise(),
            payment_method=form.payment_method.data or "upi",
            note=form.note.data or "",
        )
        flash(f"Added {inr(expense['amount_paise'])} — {expense['description']}.",
              "success")
        # Redirect preserving the active filters, so the user stays where they
        # were instead of being bounced back to an unfiltered list.
        # url_for's stub types **values narrowly; the runtime accepts any
        # value that can be rendered into a query string.
        return redirect(
            url_for("dashboard", **_current_filters()), code=303  # type: ignore[arg-type]
        )

    filters = _current_filters()
    expenses = storage.list_expenses(
        category=filters["category"], month=filters["month"], query=filters["q"]
    )

    return render_template(
        "dashboard.html",
        form=form,
        filter_form=FilterForm(request.args, meta={"csrf": False}),
        expenses=expenses,
        summary=storage.summarise(expenses),
        filters=filters,
        filtered=any(filters.values()),
    ), (422 if form.errors else 200)


@app.route("/expenses/<expense_id>")
def expense_detail(expense_id: str) -> str:
    """Show one expense.

    Args:
        expense_id: UUID string from the URL.

    Returns:
        str: Rendered ``detail.html``.

    Raises:
        werkzeug.exceptions.NotFound: when no expense has that id.
    """
    expense = storage.get_expense(expense_id)
    if expense is None:
        abort(404, description="That expense does not exist or was deleted.")
    return render_template("detail.html", expense=expense)


@app.route("/expenses/<expense_id>/delete", methods=["POST"])
def delete_expense(expense_id: str) -> WerkzeugResponse:
    """Delete an expense.

    POST-only. A delete *link* would be followed by crawlers and prefetching
    browsers — the classic way a "helpful" extension wipes someone's data.

    Args:
        expense_id: UUID string from the URL.

    Returns:
        WerkzeugResponse: 303 redirect back to the dashboard.
    """
    if storage.delete_expense(expense_id):
        flash("Expense deleted.", "info")
    else:
        flash("That expense was already gone.", "warning")
    return redirect(url_for("dashboard"), code=303)


@app.route("/export.csv")
def export_csv() -> Response:
    """Download the currently filtered expenses as a CSV file.

    Three details make this a *download* rather than a page:

    1. ``Content-Type: text/csv`` tells the browser what it is.
    2. ``Content-Disposition: attachment; filename=…`` tells it to save rather
       than display, and supplies the filename.
    3. The body is built with :mod:`csv`, which handles quoting and escaping.
       Never build CSV with ``",".join(...)`` — one description containing a
       comma and your accountant's spreadsheet is silently wrong.

    Returns:
        Response: A CSV attachment reflecting the active filters.
    """
    filters = _current_filters()
    expenses = storage.list_expenses(
        category=filters["category"], month=filters["month"], query=filters["q"]
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Date", "Description", "Category", "Amount (INR)",
                     "Payment method", "Note"])
    for expense in expenses:
        writer.writerow([
            expense["spent_on"],
            expense["description"],
            expense["category"],
            # Plain decimal for spreadsheets — no ₹ symbol or thousands
            # separators, which Excel would import as text.
            f"{storage.paise_to_rupees(expense['amount_paise']):.2f}",
            expense["payment_method"],
            expense["note"],
        ])

    filename = f"expenses-{date.today().isoformat()}.csv"
    return Response(
        # utf-8-sig writes a BOM so Excel opens ₹ and other non-ASCII correctly.
        buffer.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/preferences/compact", methods=["POST"])
def toggle_compact() -> WerkzeugResponse:
    """Toggle the compact table layout, remembered in the session (Day 06).

    A display preference is exactly what a session is for: small, non-secret,
    and per-browser.

    Returns:
        WerkzeugResponse: 303 redirect back to the dashboard.
    """
    session["compact"] = not session.get("compact", False)
    return redirect(url_for("dashboard"), code=303)


@app.route("/api/summary")
def api_summary() -> Response:
    """Return the summary as JSON — a preview of Day 11.

    Returns:
        Response: Totals in both paise (exact) and rupees (convenient).
    """
    filters = _current_filters()
    expenses = storage.list_expenses(
        category=filters["category"], month=filters["month"], query=filters["q"]
    )
    summary = storage.summarise(expenses)
    return jsonify({
        "count": summary["count"],
        "total_paise": summary["total_paise"],
        "total_inr": storage.paise_to_rupees(summary["total_paise"]),
        "by_category": summary["by_category"],
        "by_month": summary["by_month"],
        "filters": filters,
    })


@app.route("/health")
def health() -> Response:
    """Liveness probe (Day 01), now also reporting storage reachability.

    Returns:
        Response: JSON status. Still 200 when the data file is missing, because
        "no expenses yet" is a healthy first run, not an outage.
    """
    return jsonify(
        status="ok",
        service="expense-tracker",
        storage_file=str(storage.DATA_FILE),
        storage_exists=storage.DATA_FILE.exists(),
        expense_count=len(storage.list_expenses()),
    )


@app.errorhandler(404)
def not_found(error: Exception) -> tuple[str, int]:
    """Render a friendly 404 page (Day 02).

    Args:
        error: The ``NotFound`` exception.

    Returns:
        tuple[str, int]: Rendered page and status code.
    """
    return render_template("404.html", error=error), 404


# -----------------------------------------------------------------------------
# CLI commands
# -----------------------------------------------------------------------------
# @app.cli.command registers a subcommand of the `flask` CLI. This is the
# idiomatic home for admin chores — seeding, imports, cleanups — because the
# command runs inside a real application context with your config loaded,
# unlike a standalone script that has to reconstruct all of it.
@app.cli.command("seed")
@click.option("--count", default=25, show_default=True, help="How many expenses.")
def seed_command(count: int) -> None:
    """Populate the tracker with realistic demo data.

    Run with::

        flask --app 07_project_expense_tracker/app.py seed --count 40

    Args:
        count: Number of random expenses to create across the last 90 days.
    """
    samples = [
        ("Weekly groceries", "groceries", 120000, 350000),
        ("Metro card top-up", "transport", 20000, 60000),
        ("Electricity bill", "utilities", 90000, 260000),
        ("Monthly rent", "rent", 1500000, 3200000),
        ("Team lunch", "eating-out", 40000, 180000),
        ("Pharmacy", "health", 15000, 90000),
        ("Online course", "education", 49900, 299900),
        ("Misc", "other", 10000, 80000),
    ]
    for _ in range(count):
        description, category, low, high = random.choice(samples)
        storage.add_expense(
            spent_on=date.today() - timedelta(days=random.randint(0, 90)),
            description=description,
            category=category,
            amount_paise=random.randrange(low, high, 100),
            payment_method=random.choice(list(storage.PAYMENT_METHODS)),
            note="",
        )
    click.echo(f"Seeded {count} expenses into {storage.DATA_FILE}")


@app.cli.command("wipe")
def wipe_command() -> None:
    """Delete every stored expense.

    Prompts before destroying data — a destructive command that runs silently is
    a command that will one day run on the wrong machine.
    """
    if not storage.DATA_FILE.exists():
        click.echo("Nothing to wipe.")
        return
    click.confirm(f"Delete ALL expenses in {storage.DATA_FILE}?", abort=True)
    storage.DATA_FILE.unlink()
    click.echo("Wiped.")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5007, debug=True)

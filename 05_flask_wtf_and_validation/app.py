"""
Day 05 — Flask-WTF: declarative forms, real validators, automatic CSRF.
========================================================================

Real-world scenario
-------------------
A job-application portal. Candidates submit a long form with text, numbers,
dates, radios and checkboxes; recruiters filter the results. This is the kind of
form where hand-rolled validation (Day 04) stops scaling — thirteen fields would
mean roughly a hundred lines of ``if`` statements.

What you will learn
-------------------
1. Declaring a form as a class (:mod:`forms`) instead of parsing dicts.
2. ``form.validate_on_submit()`` — the POST + valid check in one call.
3. Built-in validators, **custom validator functions**, and **inline
   ``validate_<field>`` methods** for cross-field rules.
4. ``DataRequired`` vs ``InputRequired`` — the ``0`` and unticked-checkbox trap.
5. ``Optional()`` ordering, and ``filters`` for normalising input.
6. App-wide CSRF with :class:`~flask_wtf.CSRFProtect`, and why a GET filter form
   opts out.
7. Rendering fields DRY-ly with a Jinja macro.

How to run
----------
From the repository root::

    source .venv/bin/activate
    flask --app 05_flask_wtf_and_validation/app.py run --port 5005 --debug

Day 04 versus Day 05
--------------------
Day 04 read ``request.form``, stripped values, checked each rule, built an
``errors`` dict, re-rendered with sticky values, and compared a CSRF token by
hand. Today all of that is::

    form = ApplicationForm()
    if form.validate_on_submit():
        ...

The lesson is not "libraries are shorter". It is that you now know exactly which
of yesterday's steps each part of that snippet replaces.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, timezone
from typing import Any

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from werkzeug.wrappers import Response

from forms import ROLES, ApplicationForm, SearchForm

app = Flask(__name__)

# Flask-WTF signs its CSRF tokens with SECRET_KEY, so this is mandatory.
# Reading from the environment with a dev fallback is the pattern you will
# formalise on Day 18. The fallback exists so `flask run` works out of the box;
# in production the env var must be set, and Day 18 makes that a hard failure.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-not-for-production")

# How long a CSRF token stays valid (seconds). The default is 3600. A long form
# left open over lunch will otherwise fail with "CSRF token expired" — a real
# support ticket you can avoid by choosing a sensible value.
app.config["WTF_CSRF_TIME_LIMIT"] = 7200

# -----------------------------------------------------------------------------
# CSRFProtect: app-wide protection
# -----------------------------------------------------------------------------
# A FlaskForm already validates its own token. CSRFProtect goes further and
# rejects EVERY unsafe request (POST/PUT/PATCH/DELETE) that lacks a valid token,
# including endpoints that do not use a form at all. That closes the gap where
# someone adds a POST route and forgets protection.
csrf = CSRFProtect(app)


@dataclass
class Application:
    """A stored job application.

    A dataclass instead of a dict gives attribute access in templates
    (``app.full_name``), a free ``__repr__``, and type checking. Day 08 turns
    this into a SQLAlchemy model with almost no change to the call sites.

    Attributes:
        full_name: Applicant's name.
        email: Normalised (lower-cased) work email.
        role: Role slug from the allow-list.
        years_experience: Whole years, ``0`` permitted.
        expected_salary: Annual figure in INR.
        work_mode: ``onsite`` / ``hybrid`` / ``remote``.
        available_from: Earliest start date.
        portfolio: Optional URL.
        cover_letter: Free text.
        relocate: Whether the candidate will relocate.
        submitted_at: UTC timestamp, defaulted at construction.
    """

    full_name: str
    email: str
    role: str
    years_experience: int
    expected_salary: int
    work_mode: str
    available_from: date
    portfolio: str
    cover_letter: str
    relocate: bool
    submitted_at: datetime = dc_field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def role_label(self) -> str:
        """Human-readable role name for display.

        Returns:
            str: The label matching :data:`forms.ROLES`, or the raw slug.
        """
        return dict(ROLES).get(self.role, self.role)


APPLICATIONS: list[Application] = []


@app.route("/", methods=["GET", "POST"])
def apply() -> str | Response:
    """Render and process the application form.

    ``ApplicationForm()`` binds itself to ``request.form`` automatically when
    the request is a POST — you never pass the data in.

    ``form.validate_on_submit()`` is shorthand for *"this is a POST (or PUT /
    PATCH / DELETE) **and** every validator passed, including CSRF"*. It returns
    ``False`` on a GET, which is why one ``if`` handles both verbs.

    Returns:
        str | Response: The rendered form (200 on GET, 422 when invalid) or a
        303 redirect after a successful submission.

    Note:
        On failure we simply re-render ``form``. WTForms has already re-bound
        every submitted value and attached the messages to ``field.errors``, so
        sticky fields and per-field errors come for free — that is roughly forty
        lines of Day 04 code deleted.
    """
    form = ApplicationForm()

    if form.validate_on_submit():
        application = Application(
            full_name=form.full_name.data or "",
            email=form.email.data or "",
            role=form.role.data or "",
            years_experience=form.years_experience.data or 0,
            expected_salary=form.expected_salary.data or 0,
            work_mode=form.work_mode.data or "hybrid",
            available_from=form.available_from.data or date.today(),
            portfolio=form.portfolio.data or "",
            cover_letter=form.cover_letter.data or "",
            relocate=bool(form.relocate.data),
        )
        APPLICATIONS.append(application)
        flash(f"Thanks {application.full_name} — application received.", "success")
        # POST/Redirect/GET, exactly as on Day 04. The library does not change
        # the pattern; it only removes the validation boilerplate.
        return redirect(url_for("applications"), code=303)

    # Distinguish "first visit" from "you made mistakes" so we return an honest
    # status code. form.errors is {} on a GET.
    status = 422 if form.errors else 200
    return render_template("apply.html", form=form), status


@app.route("/applications")
def applications() -> str:
    """List submitted applications, filtered by a GET form.

    ``SearchForm(request.args, meta={"csrf": False})`` binds to the **query
    string** rather than the body. Note that a WTForms form bound to
    ``request.args`` still gives you coercion and validation — filters are input
    too, and unvalidated filters are how SQL injection starts (Day 08).

    Returns:
        str: Rendered ``applications.html``.
    """
    form = SearchForm(request.args, meta={"csrf": False})
    results = list(APPLICATIONS)

    # form.validate() (not validate_on_submit) because this is a GET.
    if form.validate():
        if form.q.data:
            needle = form.q.data.strip().lower()
            results = [
                a for a in results
                if needle in a.full_name.lower() or needle in a.email.lower()
            ]
        if form.role.data:
            results = [a for a in results if a.role == form.role.data]

    return render_template("applications.html", form=form, applications=results)


@app.errorhandler(CSRFError)
def handle_csrf_error(error: CSRFError) -> tuple[str, int]:
    """Turn a CSRF failure into a friendly page instead of a bare 400.

    Users hit this legitimately: they left a tab open until the token expired,
    or they submitted after their session was cleared. Telling them "your
    session expired, please try again" prevents a support ticket.

    Args:
        error: The raised :class:`~flask_wtf.csrf.CSRFError`, whose
            ``description`` explains the specific failure.

    Returns:
        tuple[str, int]: Rendered page and a 400 status code.
    """
    return render_template("csrf_error.html", reason=error.description), 400


@app.context_processor
def inject_globals() -> dict[str, Any]:
    """Expose shared values to every template.

    Returns:
        dict[str, Any]: Template globals.
    """
    return {"company": "Reinforcement Analytics", "roles": dict(ROLES)}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5005, debug=True)

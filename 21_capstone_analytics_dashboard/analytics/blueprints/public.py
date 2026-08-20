"""Day 21 — The public, unauthenticated response form."""

from __future__ import annotations

import hashlib

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request, url_for,
)
from flask.typing import ResponseReturnValue

from ..extensions import db, limiter
from ..forms import PublicResponseForm
from ..models import Response as SurveyResponse
from ..models import Survey

public_bp = Blueprint("public", __name__, template_folder="../templates/public")


def _respondent_hash() -> str:
    """Return a pseudonymous identifier for the current visitor.

    Returns:
        str: A truncated salted hash of the client address.

    Note:
        A **one-way hash**, never the address itself. It supports coarse
        duplicate detection without storing personal data.

        The application's ``SECRET_KEY`` acts as the salt. Without it, an
        unsalted hash of an IPv4 address is trivially reversible — there are
        only four billion of them, so a rainbow table is minutes of work. A
        "hashed" identifier that can be reversed is not anonymised at all.
    """
    raw = f"{current_app.config['SECRET_KEY']}:{request.remote_addr or 'unknown'}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


@public_bp.route("/s/<slug>", methods=["GET", "POST"])
# The public write endpoint, so the one most exposed to abuse. Rate limited per
# IP, with the limit read from config so it can be tuned without a deploy.
@limiter.limit(lambda: f"{current_app.config.get('RESPONSES_PER_MINUTE', 20)} per minute",
               methods=["POST"])
def respond(slug: str) -> ResponseReturnValue:
    """Show the public response form and accept submissions.

    Args:
        slug: The survey's unguessable public slug.

    Returns:
        ResponseReturnValue: The form, or a 303 redirect after submitting.

    Raises:
        werkzeug.exceptions.NotFound: when the slug is unknown.

    Note:
        A closed survey returns **404, not 403**. Telling a stranger "this
        survey exists but is closed" leaks more than it helps, and the slug is
        the only credential involved.
    """
    from sqlalchemy import select

    survey = db.session.execute(
        select(Survey).where(Survey.slug == slug)
    ).scalar_one_or_none()

    if survey is None or not survey.is_open:
        abort(404, description="This survey is not accepting responses.")

    form = PublicResponseForm()
    if form.validate_on_submit():
        db.session.add(SurveyResponse(
            survey_id=survey.id,
            score=int(form.score.data or 0),
            comment=(form.comment.data or "").strip(),
            respondent_hash=_respondent_hash(),
        ))
        db.session.commit()
        current_app.logger.info("Response recorded for survey %s", survey.slug)
        # POST/Redirect/GET, so a refresh cannot double-submit (Day 04 §6).
        return redirect(url_for("public.thanks", slug=slug), code=303)

    return render_template("public/respond.html", survey=survey, form=form), (
        422 if form.errors else 200
    )


@public_bp.get("/s/<slug>/thanks")
def thanks(slug: str) -> ResponseReturnValue:
    """Confirmation page after submitting.

    Args:
        slug: The survey's public slug.

    Returns:
        ResponseReturnValue: The rendered thank-you page.
    """
    return render_template("public/thanks.html", slug=slug)

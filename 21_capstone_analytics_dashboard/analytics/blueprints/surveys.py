"""Day 21 — The owner-facing dashboard."""

from __future__ import annotations

import csv
import io
from datetime import date

from flask import (
    Blueprint, Response, current_app, flash, redirect, render_template, request, url_for,
)
from flask.typing import ResponseReturnValue
from sqlalchemy import func, select

from flask_login import current_user, login_required

from ..extensions import cache, db
from ..forms import SurveyForm
from ..models import Response as SurveyResponse
from ..models import Survey, SurveyStatus, summarise
from ..security import owned_survey_or_404

surveys_bp = Blueprint("surveys", __name__, template_folder="../templates/surveys")


@surveys_bp.route("/", methods=["GET", "POST"])
@login_required
def index() -> ResponseReturnValue:
    """List the user's surveys and handle creation.

    Returns:
        ResponseReturnValue: The dashboard, or a 303 redirect after creating.

    Note:
        The query is scoped by ``owner_id``: authorisation lives in the
        ``WHERE`` clause, not in the template (Day 13 §10).
    """
    form = SurveyForm()
    if form.validate_on_submit():
        survey = Survey(
            slug=Survey.new_slug(),
            title=form.title.data or "",
            question=form.question.data or "",
            status=SurveyStatus(form.status.data),
            owner_id=current_user.id,
        )
        db.session.add(survey)
        db.session.commit()
        flash(f"Created “{survey.title}”.", "success")
        return redirect(url_for("surveys.detail", survey_id=survey.id), code=303)

    page = request.args.get("page", default=1, type=int)
    pagination = db.paginate(
        select(Survey).where(Survey.owner_id == current_user.id)
        .order_by(Survey.created_at.desc()),
        page=max(1, page), per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False,
    )

    # Totals computed in SQL across all of the user's surveys (Day 08 §11).
    totals = db.session.execute(
        select(func.count(SurveyResponse.id), func.coalesce(func.avg(SurveyResponse.score), 0))
        .join(SurveyResponse.survey).where(Survey.owner_id == current_user.id)
    ).one()

    return render_template(
        "surveys/index.html", form=form, surveys=pagination.items,
        pagination=pagination,
        total_responses=totals[0], overall_average=round(float(totals[1]), 2),
    ), (422 if form.errors else 200)


@surveys_bp.route("/<int:survey_id>")
@login_required
def detail(survey_id: int) -> ResponseReturnValue:
    """Show one survey with its statistics.

    Args:
        survey_id: Primary key from the URL.

    Returns:
        ResponseReturnValue: The rendered detail page.
    """
    survey = owned_survey_or_404(survey_id)
    return render_template("surveys/detail.html", survey=survey,
                           stats=summarise(survey.responses))


@surveys_bp.route("/<int:survey_id>/edit", methods=["GET", "POST"])
@login_required
def edit(survey_id: int) -> ResponseReturnValue:
    """Edit a survey.

    Args:
        survey_id: Primary key from the URL.

    Returns:
        ResponseReturnValue: The form, or a 303 redirect on success.
    """
    survey = owned_survey_or_404(survey_id)
    form = SurveyForm(obj=survey)
    if request.method == "GET":
        form.status.data = survey.status.value

    if form.validate_on_submit():
        survey.title = form.title.data or ""
        survey.question = form.question.data or ""
        survey.status = SurveyStatus(form.status.data)
        db.session.commit()
        # Invalidate the cached public page at the moment the data changes —
        # in the WRITE path, not on a guess (Day 19 §5).
        cache.delete_memoized(public_survey_payload, survey.slug)
        flash("Survey updated.", "success")
        return redirect(url_for("surveys.detail", survey_id=survey.id), code=303)

    return render_template("surveys/edit.html", form=form, survey=survey), (
        422 if form.errors else 200
    )


@surveys_bp.route("/<int:survey_id>/delete", methods=["POST"])
@login_required
def delete(survey_id: int) -> ResponseReturnValue:
    """Delete a survey and, by cascade, its responses.

    Args:
        survey_id: Primary key from the URL.

    Returns:
        ResponseReturnValue: 303 redirect to the dashboard.
    """
    survey = owned_survey_or_404(survey_id)
    title, slug = survey.title, survey.slug
    db.session.delete(survey)
    db.session.commit()
    cache.delete_memoized(public_survey_payload, slug)
    flash(f"Deleted “{title}” and its responses.", "info")
    return redirect(url_for("surveys.index"), code=303)


@surveys_bp.route("/<int:survey_id>/export.csv")
@login_required
def export_csv(survey_id: int) -> ResponseReturnValue:
    """Download a survey's responses as CSV.

    Args:
        survey_id: Primary key from the URL.

    Returns:
        ResponseReturnValue: A CSV attachment (Day 07 §7).
    """
    survey = owned_survey_or_404(survey_id)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Submitted (UTC)", "Score", "Category", "Comment"])
    for response in survey.responses:
        writer.writerow([
            response.created_at.strftime("%Y-%m-%d %H:%M"),
            response.score, response.category, response.comment,
        ])

    filename = f"{survey.slug}-{date.today().isoformat()}.csv"
    return Response(
        # utf-8-sig so Excel renders non-ASCII comments correctly.
        buffer.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@cache.memoize(timeout=30)
def public_survey_payload(slug: str) -> dict[str, object] | None:
    """Return the cached public view of a survey.

    Args:
        slug: The survey's public slug.

    Returns:
        dict[str, object] | None: Title, question and status, or ``None``.

    Note:
        The public page is the one strangers hit, so it is the one worth
        caching. Only the **immutable-per-edit** parts are cached — never the
        response count, which changes with every submission (Day 19 §4).
    """
    survey = db.session.execute(
        select(Survey).where(Survey.slug == slug)
    ).scalar_one_or_none()
    if survey is None:
        return None
    return {"id": survey.id, "title": survey.title,
            "question": survey.question, "status": survey.status.value}

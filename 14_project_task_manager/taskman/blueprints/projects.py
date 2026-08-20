"""Day 14 — ``projects`` blueprint: the user's own projects."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, url_for
from sqlalchemy import select
from flask.typing import ResponseReturnValue
from werkzeug.wrappers import Response

from flask_login import current_user, login_required

from ..extensions import db
from ..forms import ProjectForm
from ..models import Project
from ..security import owned_project_or_404

projects_bp = Blueprint("projects", __name__, template_folder="../templates/projects")


@projects_bp.route("/", methods=["GET", "POST"])
@login_required
def index() -> ResponseReturnValue:
    """List the user's projects and handle creation.

    Returns:
        str | Response: The rendered list, or a 303 redirect after creating.

    Note:
        The query is scoped by ``owner_id`` — authorisation lives in the
        ``WHERE`` clause, not in the template (Day 13 §10).
    """
    form = ProjectForm()
    if form.validate_on_submit():
        project = Project(
            name=form.name.data or "",
            description=form.description.data or "",
            owner_id=current_user.id,
        )
        db.session.add(project)
        db.session.commit()
        flash(f"Created “{project.name}”.", "success")
        return redirect(url_for("projects.detail", project_id=project.id), code=303)

    projects = db.session.execute(
        select(Project).where(Project.owner_id == current_user.id)
        .order_by(Project.created_at.desc())
    ).scalars().all()

    return render_template("projects/index.html", projects=projects, form=form), (
        422 if form.errors else 200
    )


@projects_bp.route("/<int:project_id>")
@login_required
def detail(project_id: int) -> str:
    """Show one project and its tasks.

    Args:
        project_id: Primary key from the URL.

    Returns:
        str: Rendered ``projects/detail.html``.
    """
    from ..forms import TaskForm

    project = owned_project_or_404(project_id)

    form = TaskForm()
    form.assignee_id.choices = [("", "Unassigned"), (current_user.id, current_user.display_name)]

    return render_template("projects/detail.html", project=project, form=form)


@projects_bp.route("/<int:project_id>/edit", methods=["GET", "POST"])
@login_required
def edit(project_id: int) -> ResponseReturnValue:
    """Edit a project you own.

    Args:
        project_id: Primary key from the URL.

    Returns:
        str | Response: The rendered form, or a 303 redirect on success.
    """
    project = owned_project_or_404(project_id)
    form = ProjectForm(obj=project)

    if form.validate_on_submit():
        form.populate_obj(project)
        db.session.commit()
        flash("Project updated.", "success")
        return redirect(url_for("projects.detail", project_id=project.id), code=303)

    return render_template("projects/edit.html", form=form, project=project), (
        422 if form.errors else 200
    )


@projects_bp.route("/<int:project_id>/delete", methods=["POST"])
@login_required
def delete(project_id: int) -> Response:
    """Delete a project and, by cascade, all of its tasks.

    Args:
        project_id: Primary key from the URL.

    Returns:
        Response: 303 redirect to the project list.
    """
    project = owned_project_or_404(project_id)
    name = project.name
    db.session.delete(project)
    db.session.commit()
    flash(f"Deleted “{name}” and its tasks.", "info")
    return redirect(url_for("projects.index"), code=303)

"""Day 14 — ``tasks`` blueprint: create, edit, move and delete tasks."""

from __future__ import annotations

from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, url_for,
)
from sqlalchemy import select
from flask.typing import ResponseReturnValue
from werkzeug.wrappers import Response

from flask_login import current_user, login_required

from ..extensions import db
from ..forms import TaskForm
from ..models import Project, Task, TaskPriority, TaskStatus
from ..security import owned_project_or_404, owned_task_or_404

tasks_bp = Blueprint("tasks", __name__, template_folder="../templates/tasks")


def _assignee_choices() -> list[tuple[object, str]]:
    """Build assignee choices for the current user.

    Returns:
        list[tuple[object, str]]: ``("", "Unassigned")`` plus the current user.

    Note:
        Only the signed-in user is offered. Listing **every** account in the
        system would leak your entire user directory to anyone who registers —
        a quiet but real privacy failure in many small apps. Real team software
        would list the members of *this project*.
    """
    return [("", "Unassigned"), (current_user.id, current_user.display_name)]


# An explicit path, because this blueprint is registered WITHOUT a url_prefix
# (its other routes live under /projects/<id>/tasks and /tasks/<id>/...).
# An earlier draft used "/" here, which silently collided with
# projects.index — Flask kept the first rule registered and /tasks/ 404ed.
# Two blueprints can share an endpoint NAME, never a URL rule.
@tasks_bp.route("/tasks/", methods=["GET"])
@login_required
def index() -> str:
    """List every task across the user's projects, with filters.

    Query parameters:
        ``status``, ``priority``, ``project``, ``overdue``, ``q``, ``page``.

    Returns:
        str: Rendered ``tasks/index.html``.

    Note:
        Every filter is applied in SQL, and the base query is joined to
        ``Project`` and scoped by ``owner_id`` — so a crafted
        ``?project=<someone else's id>`` returns nothing rather than their data.
        **Authorisation belongs in the query.**
    """
    statement = (
        select(Task).join(Task.project).where(Project.owner_id == current_user.id)
    )

    filters = {
        "status": request.args.get("status", "").strip(),
        "priority": request.args.get("priority", "").strip(),
        "project": request.args.get("project", "").strip(),
        "overdue": request.args.get("overdue", "").strip(),
        "q": request.args.get("q", "").strip(),
    }

    # Unknown enum values are ignored rather than raising: a stale bookmark or
    # a hand-edited URL should degrade to "no filter", not a 500.
    if filters["status"] in {s.value for s in TaskStatus}:
        statement = statement.where(Task.status == TaskStatus(filters["status"]))
    if filters["priority"] in {p.value for p in TaskPriority}:
        statement = statement.where(Task.priority == TaskPriority(filters["priority"]))
    if filters["project"].isdigit():
        statement = statement.where(Task.project_id == int(filters["project"]))
    if filters["overdue"] == "1":
        from datetime import date

        statement = statement.where(
            Task.due_on.is_not(None),
            Task.due_on < date.today(),
            Task.status != TaskStatus.DONE,
        )
    if filters["q"]:
        statement = statement.where(Task.title.ilike(f"%{filters['q']}%"))

    # Sort: unfinished first, then by due date with NULLs last, then newest.
    statement = statement.order_by(
        (Task.status == TaskStatus.DONE).asc(),
        Task.due_on.is_(None).asc(),
        Task.due_on.asc(),
        Task.created_at.desc(),
    )

    page = request.args.get("page", default=1, type=int)
    pagination = db.paginate(
        statement, page=max(1, page),
        per_page=current_app.config["TASKS_PER_PAGE"], error_out=False,
    )

    projects = db.session.execute(
        select(Project).where(Project.owner_id == current_user.id).order_by(Project.name)
    ).scalars().all()

    return render_template(
        "tasks/index.html",
        tasks=pagination.items, pagination=pagination,
        projects=projects, filters=filters,
        statuses=list(TaskStatus), priorities=list(TaskPriority),
    )


@tasks_bp.route("/projects/<int:project_id>/tasks", methods=["POST"])
@login_required
def create(project_id: int) -> Response:
    """Create a task inside one of the user's projects.

    Args:
        project_id: The owning project, verified against the current user.

    Returns:
        Response: 303 redirect back to the project.
    """
    project = owned_project_or_404(project_id)

    form = TaskForm()
    form.assignee_id.choices = _assignee_choices()

    if not form.validate_on_submit():
        # Surface the first message rather than silently discarding the input.
        message = next(
            (msgs[0] for msgs in form.errors.values() if msgs), "Could not save that task."
        )
        flash(message, "error")
        return redirect(url_for("projects.detail", project_id=project.id), code=303)

    task = Task(
        title=form.title.data or "",
        notes=form.notes.data or "",
        priority=TaskPriority(form.priority.data),
        due_on=form.due_on.data,
        project_id=project.id,
        assignee_id=form.assignee_id.data,
    )
    # mark() keeps completed_at consistent with status — one writer per
    # derived field.
    task.mark(TaskStatus(form.status.data))

    db.session.add(task)
    db.session.commit()
    flash(f"Added “{task.title}”.", "success")
    return redirect(url_for("projects.detail", project_id=project.id), code=303)


@tasks_bp.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
@login_required
def edit(task_id: int) -> ResponseReturnValue:
    """Edit a task the current user can reach through their own project.

    Args:
        task_id: Primary key from the URL.

    Returns:
        str | Response: The rendered form, or a 303 redirect on success.
    """
    task = owned_task_or_404(task_id)

    form = TaskForm(obj=task)
    form.assignee_id.choices = _assignee_choices()

    if request.method == "GET":
        # Enum members must be converted to their string values for the
        # SelectField, whose choices are strings.
        form.status.data = task.status.value
        form.priority.data = task.priority.value

    if form.validate_on_submit():
        task.title = form.title.data or ""
        task.notes = form.notes.data or ""
        task.priority = TaskPriority(form.priority.data)
        task.due_on = form.due_on.data
        task.assignee_id = form.assignee_id.data
        task.mark(TaskStatus(form.status.data))
        db.session.commit()
        flash("Task updated.", "success")
        return redirect(url_for("projects.detail", project_id=task.project_id), code=303)

    return render_template("tasks/edit.html", form=form, task=task), (
        422 if form.errors else 200
    )


@tasks_bp.route("/tasks/<int:task_id>/status", methods=["POST"])
@login_required
def set_status(task_id: int) -> Response:
    """Move a task to a new status — the one-click board action.

    Args:
        task_id: Primary key from the URL.

    Returns:
        Response: 303 redirect back to where the user came from.

    Note:
        A **POST**, even though it feels like a link. It changes state, so a GET
        would be triggered by crawlers and prefetching browsers (Day 06).
    """
    task = owned_task_or_404(task_id)
    raw = request.form.get("status", "")

    if raw not in {s.value for s in TaskStatus}:
        flash("Unknown status.", "error")
    else:
        task.mark(TaskStatus(raw))
        db.session.commit()

    # Only ever redirect to a URL we built ourselves (Day 13 §8).
    target = request.form.get("return_to", "")
    if target.startswith("/") and not target.startswith("//"):
        return redirect(target, code=303)
    return redirect(url_for("projects.detail", project_id=task.project_id), code=303)


@tasks_bp.route("/tasks/<int:task_id>/delete", methods=["POST"])
@login_required
def delete(task_id: int) -> Response:
    """Delete a task.

    Args:
        task_id: Primary key from the URL.

    Returns:
        Response: 303 redirect to the owning project.
    """
    task = owned_task_or_404(task_id)
    project_id = task.project_id
    db.session.delete(task)
    db.session.commit()
    flash("Task deleted.", "info")
    return redirect(url_for("projects.detail", project_id=project_id), code=303)

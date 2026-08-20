"""
Day 14 — ``api`` blueprint: JSON access to the same data.

Reuses Day 11's conventions (versioned prefix, JSON errors, pagination) and
Day 13's authorisation. The important detail is that the API shares the
**same** ownership helpers as the HTML views — two code paths to the same data
with two different authorisation implementations is how one of them ends up
wrong.
"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request
from sqlalchemy import select
from werkzeug.exceptions import HTTPException

from flask_login import current_user, login_required

from ..extensions import csrf, db
from ..models import Project, Task, TaskStatus
from ..security import owned_project_or_404, owned_task_or_404

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

# This API authenticates with the SESSION COOKIE, so CSRF protection must stay
# ON for unsafe methods — a cookie-authenticated JSON endpoint is exactly as
# CSRF-vulnerable as a form. (Day 10 exempted an API because it was designed
# for token auth; Day 15 makes that distinction concrete.)
# Read-only endpoints are unaffected either way.


@api_bp.errorhandler(HTTPException)
def json_error(error: HTTPException) -> tuple[Response, int]:
    """Return HTTP errors from this blueprint as JSON.

    Args:
        error: The raised exception.

    Returns:
        tuple[Response, int]: JSON envelope and status code.
    """
    return jsonify(error={"status": error.code, "message": error.description}), (
        error.code or 500
    )


# Register for specific codes too — a generic HTTPException handler loses to an
# app-level coded handler (the Day 10 §9 resolution rule).
for _status in (400, 401, 403, 404, 405, 409, 422):
    api_bp.register_error_handler(_status, json_error)


@api_bp.get("/projects")
@login_required
def list_projects() -> Response:
    """List the current user's projects.

    Returns:
        Response: ``200`` with a ``data`` array.
    """
    projects = db.session.execute(
        select(Project).where(Project.owner_id == current_user.id)
        .order_by(Project.created_at.desc())
    ).scalars().all()
    return jsonify(data=[project.to_dict() for project in projects])


@api_bp.get("/projects/<int:project_id>")
@login_required
def get_project(project_id: int) -> Response:
    """Return one project with its tasks.

    Args:
        project_id: Primary key from the URL.

    Returns:
        Response: ``200`` with the project and its tasks.
    """
    project = owned_project_or_404(project_id)
    return jsonify({
        **project.to_dict(),
        "tasks": [task.to_dict() for task in project.tasks],
    })


@api_bp.get("/tasks")
@login_required
def list_tasks() -> Response:
    """List tasks across the user's projects, paginated.

    Returns:
        Response: ``200`` with ``data`` and ``meta``.
    """
    statement = (
        select(Task).join(Task.project).where(Project.owner_id == current_user.id)
        .order_by(Task.created_at.desc())
    )
    if (status := request.args.get("status", "")) in {s.value for s in TaskStatus}:
        statement = statement.where(Task.status == TaskStatus(status))

    page = max(1, request.args.get("page", default=1, type=int))
    per_page = max(1, min(request.args.get("per_page", default=20, type=int), 100))
    pagination = db.paginate(statement, page=page, per_page=per_page, error_out=False)

    return jsonify(
        data=[task.to_dict() for task in pagination.items],
        meta={"page": pagination.page, "per_page": pagination.per_page,
              "total": pagination.total, "pages": pagination.pages},
    )


@api_bp.get("/tasks/<int:task_id>")
@login_required
def get_task(task_id: int) -> Response:
    """Return one task.

    Args:
        task_id: Primary key from the URL.

    Returns:
        Response: ``200`` with the task.
    """
    return jsonify(owned_task_or_404(task_id).to_dict())


@api_bp.patch("/tasks/<int:task_id>")
@login_required
def update_task(task_id: int) -> Response:
    """Update a task's status.

    Args:
        task_id: Primary key from the URL.

    Returns:
        Response: ``200`` with the updated task.

    Raises:
        werkzeug.exceptions.HTTPException: ``415`` when not JSON, ``422`` for an
            unknown status.
    """
    from flask import abort

    task = owned_task_or_404(task_id)

    if not request.is_json:
        abort(415, description="Send Content-Type: application/json.")

    body = request.get_json(silent=True) or {}
    status = body.get("status")
    if status not in {s.value for s in TaskStatus}:
        abort(422, description=f"status must be one of {[s.value for s in TaskStatus]}.")

    task.mark(TaskStatus(status))
    db.session.commit()
    return jsonify(task.to_dict())


@api_bp.get("/stats")
@login_required
def stats() -> Response:
    """Return per-status counts for the current user.

    Returns:
        Response: ``200`` with counts keyed by status value.

    Note:
        Counted with ``GROUP BY`` in the database, not by loading every task —
        the Day 08 lesson.
    """
    from sqlalchemy import func

    rows = db.session.execute(
        select(Task.status, func.count(Task.id))
        .join(Task.project).where(Project.owner_id == current_user.id)
        .group_by(Task.status)
    ).all()

    counts = {status.value: 0 for status in TaskStatus}
    for status, count in rows:
        counts[status.value if hasattr(status, "value") else str(status)] = count

    return jsonify(counts=counts, total=sum(counts.values()))

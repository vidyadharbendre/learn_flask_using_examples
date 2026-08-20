"""
Day 14 — Authorisation helpers.
===============================

Day 13's lesson, extracted into one module so it cannot be *almost* applied.

Ownership in this app flows ``User → Project → Task``. Deciding whether you may
touch a task therefore means asking who owns the task's **project** — a chain
that is easy to walk in the wrong place, or forget entirely.

Centralising it here means every view calls the same function, and a reviewer
can check authorisation by reading one file.
"""

from __future__ import annotations

from flask import abort

from flask_login import current_user

from .extensions import db
from .models import Project, Task


def owned_project_or_404(project_id: int) -> Project:
    """Return a project the current user owns, or abort with 404.

    Args:
        project_id: Primary key from the URL.

    Returns:
        Project: The project, guaranteed to belong to ``current_user``.

    Raises:
        werkzeug.exceptions.NotFound: when the project does not exist **or**
            belongs to someone else.

    Note:
        **404, not 403.** Returning "Forbidden" would confirm that project 7
        exists and belongs to another user — an information leak that helps an
        attacker map your data. 404 says nothing at all.
    """
    project = db.session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        abort(404, description="No such project.")
    return project


def owned_task_or_404(task_id: int) -> Task:
    """Return a task inside a project the current user owns, or abort with 404.

    Args:
        task_id: Primary key from the URL.

    Returns:
        Task: The task, guaranteed to be within the user's own project.

    Raises:
        werkzeug.exceptions.NotFound: when the task does not exist or is not
            reachable from one of the user's projects.

    Note:
        Note that it checks ``task.project.owner_id`` — **not** ``task.assignee_id``.
        Being assigned a task is not the same as owning the project it lives in,
        and getting this backwards is the sort of subtle authorisation bug that
        survives code review precisely because a check *is* present.
    """
    task = db.session.get(Task, task_id)
    if task is None or task.project.owner_id != current_user.id:
        abort(404, description="No such task.")
    return task

"""
Day 14 — Authorisation tests: the ones that matter most.

Every test here would pass trivially if ``@login_required`` were the only
check — and every one of them would then represent a data breach.
"""

from __future__ import annotations

from conftest import AuthActions
from flask import Flask
from flask.testing import FlaskClient

from taskman.extensions import db
from taskman.models import Project, Task, TaskPriority, TaskStatus, User


def _project_for(owner: User, name: str = "Private") -> Project:
    """Create a project with one task, owned by ``owner``.

    Args:
        owner: The owning user.
        name: Project name.

    Returns:
        Project: The persisted project.
    """
    project = Project(name=name, description="secret", owner_id=owner.id)
    db.session.add(project)
    db.session.flush()
    task = Task(title="Secret task", project_id=project.id, priority=TaskPriority.LOW)
    task.mark(TaskStatus.TODO)
    db.session.add(task)
    db.session.commit()
    return project


def test_cannot_read_another_users_project(
    client: FlaskClient, user: User, other_user: User, auth: AuthActions
) -> None:
    """A project belonging to someone else is a 404, not a 403."""
    theirs = _project_for(other_user)
    auth.login()

    response = client.get(f"/projects/{theirs.id}")
    # 404, not 403: a 403 would CONFIRM the project exists.
    assert response.status_code == 404


def test_cannot_read_another_users_task(
    client: FlaskClient, user: User, other_user: User, auth: AuthActions
) -> None:
    """A task inside someone else's project is unreachable."""
    theirs = _project_for(other_user)
    task_id = theirs.tasks[0].id
    auth.login()
    assert client.get(f"/tasks/{task_id}/edit").status_code == 404


def test_cannot_modify_another_users_task(
    client: FlaskClient, user: User, other_user: User, auth: AuthActions
) -> None:
    """Writes are checked as carefully as reads — and must not take effect."""
    theirs = _project_for(other_user)
    task = theirs.tasks[0]
    task_id, original = task.id, task.status
    auth.login()

    response = client.post(f"/tasks/{task_id}/status", data={"status": "done"})
    assert response.status_code == 404

    db.session.expire_all()
    assert db.session.get(Task, task_id).status == original


def test_cannot_delete_another_users_project(
    client: FlaskClient, user: User, other_user: User, auth: AuthActions
) -> None:
    """Deletion is authorised too."""
    theirs = _project_for(other_user)
    project_id = theirs.id
    auth.login()

    assert client.post(f"/projects/{project_id}/delete").status_code == 404
    assert db.session.get(Project, project_id) is not None


def test_cannot_add_a_task_to_another_users_project(
    client: FlaskClient, user: User, other_user: User, auth: AuthActions
) -> None:
    """Creating inside someone else's container is blocked."""
    theirs = _project_for(other_user)
    auth.login()

    response = client.post(
        f"/projects/{theirs.id}/tasks",
        data={"title": "Injected", "status": "todo", "priority": "low"},
    )
    assert response.status_code == 404


def test_task_list_only_shows_own_tasks(
    client: FlaskClient, user: User, other_user: User, project: Project, auth: AuthActions
) -> None:
    """The list query is scoped by owner, not filtered in the template."""
    _project_for(other_user, name="Theirs")
    auth.login()

    body = client.get("/tasks/").get_data(as_text=True)
    assert "Write copy" in body        # ours
    assert "Secret task" not in body   # theirs


def test_project_filter_cannot_reach_another_users_project(
    client: FlaskClient, user: User, other_user: User, project: Project, auth: AuthActions
) -> None:
    """A hand-edited ?project= returns nothing rather than someone else's data.

    This is the test that proves authorisation lives in the WHERE clause: the
    filter is applied on top of an already-scoped query, so a hostile value
    simply matches no rows.
    """
    theirs = _project_for(other_user)
    auth.login()

    body = client.get(f"/tasks/?project={theirs.id}").get_data(as_text=True)
    assert "Secret task" not in body


def test_api_scopes_to_the_current_user(
    client: FlaskClient, user: User, other_user: User, project: Project, auth: AuthActions
) -> None:
    """The JSON API applies the same ownership rules as the HTML views."""
    theirs = _project_for(other_user)
    auth.login()

    projects = client.get("/api/v1/projects").get_json()["data"]
    names = {item["name"] for item in projects}
    assert "Website relaunch" in names
    assert "Private" not in names

    assert client.get(f"/api/v1/projects/{theirs.id}").status_code == 404

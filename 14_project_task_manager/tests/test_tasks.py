"""Day 14 — Task behaviour and API tests."""

from __future__ import annotations

from datetime import date, timedelta

from conftest import AuthActions
from flask import Flask
from flask.testing import FlaskClient

from taskman.extensions import db
from taskman.models import Project, Task, TaskPriority, TaskStatus, User


def test_mark_done_sets_completed_at(app: Flask, project: Project) -> None:
    """`mark()` keeps completed_at in step with status — one writer."""
    task = project.tasks[0]
    assert task.completed_at is None

    task.mark(TaskStatus.DONE)
    assert task.completed_at is not None

    task.mark(TaskStatus.TODO)
    assert task.completed_at is None


def test_is_overdue(app: Flask, project: Project) -> None:
    """Overdue means: has a due date, in the past, and not done."""
    task = project.tasks[0]

    task.due_on = date.today() - timedelta(days=1)
    assert task.is_overdue

    task.mark(TaskStatus.DONE)
    assert not task.is_overdue          # finished work is never overdue

    task.mark(TaskStatus.TODO)
    task.due_on = None
    assert not task.is_overdue          # no due date, no deadline


def test_project_progress(app: Flask, user: User) -> None:
    """Progress is a percentage, and an empty project does not divide by zero."""
    project = Project(name="Empty", owner_id=user.id)
    db.session.add(project)
    db.session.commit()
    assert project.progress == 0

    for index in range(4):
        task = Task(title=f"T{index}", project_id=project.id, priority=TaskPriority.LOW)
        task.mark(TaskStatus.DONE if index < 3 else TaskStatus.TODO)
        db.session.add(task)
    db.session.commit()
    db.session.refresh(project)

    assert project.progress == 75
    assert project.open_count == 1


def test_create_task_via_form(client: FlaskClient, user: User, project: Project, auth: AuthActions) -> None:
    """Posting the task form creates a task and redirects (POST/Redirect/GET)."""
    auth.login()
    response = client.post(
        f"/projects/{project.id}/tasks",
        data={"title": "New task", "status": "todo", "priority": "high",
              "due_on": "", "assignee_id": "", "notes": ""},
    )
    assert response.status_code == 303

    titles = [task.title for task in db.session.get(Project, project.id).tasks]
    assert "New task" in titles


def test_status_change_requires_post(
    client: FlaskClient, user: User, project: Project, auth: AuthActions
) -> None:
    """Changing state via GET would be triggered by crawlers."""
    auth.login()
    assert client.get(f"/tasks/{project.tasks[0].id}/status").status_code == 405


def test_status_change_rejects_unknown_value(
    client: FlaskClient, user: User, project: Project, auth: AuthActions
) -> None:
    """An invalid status is ignored rather than crashing."""
    auth.login()
    task_id = project.tasks[0].id

    response = client.post(f"/tasks/{task_id}/status", data={"status": "banana"})
    assert response.status_code == 303
    db.session.expire_all()
    assert db.session.get(Task, task_id).status == TaskStatus.TODO


def test_unknown_filter_values_are_ignored_not_fatal(
    client: FlaskClient, user: User, project: Project, auth: AuthActions
) -> None:
    """A stale bookmark must degrade to 'no filter', not a 500."""
    auth.login()
    assert client.get("/tasks/?status=nonsense&priority=nope&project=abc").status_code == 200


def test_deleting_a_project_cascades_to_its_tasks(
    client: FlaskClient, user: User, project: Project, auth: AuthActions
) -> None:
    """Cascade is configured on the relationship (Day 08)."""
    auth.login()
    task_id = project.tasks[0].id

    assert client.post(f"/projects/{project.id}/delete").status_code == 303
    assert db.session.get(Task, task_id) is None


def test_api_task_list_and_patch(
    client: FlaskClient, user: User, project: Project, auth: AuthActions
) -> None:
    """The API lists and updates tasks."""
    auth.login()

    payload = client.get("/api/v1/tasks").get_json()
    assert payload["meta"]["total"] == 1
    assert payload["data"][0]["title"] == "Write copy"

    task_id = payload["data"][0]["id"]
    response = client.patch(f"/api/v1/tasks/{task_id}", json={"status": "done"})
    assert response.status_code == 200
    assert response.get_json()["status"] == "done"
    assert response.get_json()["completed_at"] is not None


def test_api_patch_validates_status(
    client: FlaskClient, user: User, project: Project, auth: AuthActions
) -> None:
    """An unknown status is a 422, and a non-JSON body is a 415."""
    auth.login()
    task_id = project.tasks[0].id

    assert client.patch(f"/api/v1/tasks/{task_id}", json={"status": "banana"}).status_code == 422
    assert client.patch(f"/api/v1/tasks/{task_id}", data="status=done").status_code == 415


def test_api_stats_counts_by_status(
    client: FlaskClient, user: User, project: Project, auth: AuthActions
) -> None:
    """Stats are aggregated in SQL and cover every status key."""
    auth.login()
    payload = client.get("/api/v1/stats").get_json()

    assert payload["total"] == 1
    assert payload["counts"]["todo"] == 1
    assert set(payload["counts"]) == {"todo", "in_progress", "blocked", "done"}

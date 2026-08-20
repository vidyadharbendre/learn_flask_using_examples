"""
Day 14 — Models: users, projects, tasks.
========================================

The domain is deliberately ordinary — that is what makes the *relationships*
worth studying:

- a **User** owns many Projects
- a **Project** contains many Tasks
- a **Task** may be assigned to a User (optional, nullable)

Ownership flows ``User → Project → Task``, which means authorisation has to
follow the chain: to decide whether you may touch a task, the app must ask who
owns the task's *project*. That is exactly the kind of check people forget.
"""

from __future__ import annotations

import enum
from datetime import date, datetime, timezone
from typing import Any

from flask import current_app
from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from flask_login import UserMixin

from .extensions import db


class TaskStatus(str, enum.Enum):
    """Lifecycle state of a task.

    Subclassing ``str`` as well as ``enum.Enum`` means the members compare
    equal to their values, so ``task.status == "todo"`` works, Jinja prints
    them cleanly, and JSON serialisation is trivial — while Python code still
    gets ``TaskStatus.TODO`` with autocompletion and typo protection.
    """

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"

    @property
    def label(self) -> str:
        """Human-readable name.

        Returns:
            str: e.g. ``"In Progress"``.
        """
        return self.value.replace("_", " ").title()


class TaskPriority(str, enum.Enum):
    """How urgent a task is."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"

    @property
    def label(self) -> str:
        """Human-readable name.

        Returns:
            str: e.g. ``"Urgent"``.
        """
        return self.value.title()

    @property
    def rank(self) -> int:
        """Sort weight, highest first.

        Returns:
            int: ``3`` for urgent down to ``0`` for low.
        """
        return {"low": 0, "medium": 1, "high": 2, "urgent": 3}[self.value]


class User(UserMixin, db.Model):
    """An account.

    Attributes:
        id: Surrogate primary key.
        email: Unique, lower-cased login identifier.
        display_name: Name shown in the UI.
        password_hash: scrypt verifier (Day 13).
        active: Whether the account may sign in.
        created_at: Registration timestamp.
        projects: Projects this user owns.
        assigned_tasks: Tasks assigned to this user.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    projects: Mapped[list["Project"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan",
        order_by="Project.created_at.desc()",
    )
    assigned_tasks: Mapped[list["Task"]] = relationship(
        back_populates="assignee", foreign_keys="Task.assignee_id"
    )

    def set_password(self, password: str) -> None:
        """Hash and store a password.

        Args:
            password: The plaintext password; used and discarded.

        Note:
            The hash method is read from config so the **testing** environment
            can use a cheap one. scrypt is intentionally slow (that is the
            point), which makes a suite that creates hundreds of users crawl.
            Scoping the weak setting to ``TestingConfig`` keeps it impossible
            to reach production.
        """
        method = current_app.config.get("PASSWORD_HASH_METHOD")
        self.password_hash = (
            generate_password_hash(password, method=method) if method
            else generate_password_hash(password)
        )

    def check_password(self, password: str) -> bool:
        """Verify a password guess in constant time.

        Args:
            password: The plaintext guess.

        Returns:
            bool: True when it matches.
        """
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self) -> bool:  # type: ignore[override]
        """Whether this account may sign in.

        Returns:
            bool: The ``active`` column.
        """
        return self.active

    def open_task_count(self) -> int:
        """Count this user's unfinished tasks across all their projects.

        Returns:
            int: Number of tasks not in the ``DONE`` state.

        Note:
            Counted **in SQL** rather than by loading every task and filtering
            in Python (Day 08). The join walks the ownership chain
            ``Task → Project → User``.
        """
        return db.session.execute(
            select(func.count(Task.id))
            .join(Task.project)
            .where(Project.owner_id == self.id, Task.status != TaskStatus.DONE)
        ).scalar_one()

    def __repr__(self) -> str:
        """Return an unambiguous representation, with no secret material.

        Returns:
            str: e.g. ``<User 1 'ana@example.com'>``.
        """
        return f"<User {self.id} {self.email!r}>"


class Project(db.Model):
    """A container for tasks, owned by one user.

    Attributes:
        id: Surrogate primary key.
        name: Project name.
        description: Optional longer text.
        owner_id: Foreign key to the owning :class:`User`.
        owner: The owning user.
        tasks: Tasks in this project.
        created_at: Creation timestamp.
    """

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner: Mapped["User"] = relationship(back_populates="projects")

    tasks: Mapped[list["Task"]] = relationship(
        back_populates="project", cascade="all, delete-orphan",
        order_by="Task.created_at.desc()", lazy="selectin",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def open_count(self) -> int:
        """Number of unfinished tasks.

        Returns:
            int: Tasks not in the ``DONE`` state.

        Note:
            Safe to use in a template only because ``tasks`` is eager-loaded
            (``lazy="selectin"``). On a lazily-loaded relationship this would
            fire a query per project — the N+1 problem from Day 08.
        """
        return sum(1 for task in self.tasks if task.status != TaskStatus.DONE)

    @property
    def progress(self) -> int:
        """Completion percentage.

        Returns:
            int: 0-100. An empty project reports ``0`` rather than dividing by
            zero.
        """
        if not self.tasks:
            return 0
        done = sum(1 for task in self.tasks if task.status == TaskStatus.DONE)
        return round(done / len(self.tasks) * 100)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API.

        Returns:
            dict[str, Any]: JSON-safe representation.
        """
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "task_count": len(self.tasks),
            "open_count": self.open_count,
            "progress": self.progress,
            "created_at": _iso_utc(self.created_at),
        }

    def __repr__(self) -> str:
        """Return an unambiguous representation.

        Returns:
            str: e.g. ``<Project 1 'Website relaunch'>``.
        """
        return f"<Project {self.id} {self.name!r}>"


class Task(db.Model):
    """A unit of work inside a project.

    Attributes:
        id: Surrogate primary key.
        title: Short description of the work.
        notes: Optional detail.
        status: Lifecycle state.
        priority: Urgency.
        due_on: Optional due date.
        project_id: Owning project.
        project: The related project.
        assignee_id: Optional assigned user.
        assignee: The related user, if any.
        created_at / completed_at: Timestamps.
    """

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # native_enum=False stores the value as VARCHAR with a CHECK constraint
    # instead of a database-native ENUM type. Native enums are a migration
    # nightmare — adding a value to a PostgreSQL enum requires ALTER TYPE and
    # cannot run inside a transaction on older versions. A constrained string
    # is portable and easy to evolve.
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, native_enum=False, length=20, validate_strings=True),
        nullable=False, default=TaskStatus.TODO, index=True,
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority, native_enum=False, length=20, validate_strings=True),
        nullable=False, default=TaskPriority.MEDIUM,
    )

    due_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project: Mapped["Project"] = relationship(back_populates="tasks")

    # ondelete="SET NULL": deleting a user must NOT delete their assigned
    # tasks — the work still exists, it simply becomes unassigned. Choosing
    # CASCADE here would silently destroy a departing employee's tasks.
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assignee: Mapped["User | None"] = relationship(
        back_populates="assigned_tasks", foreign_keys=[assignee_id]
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_overdue(self) -> bool:
        """Whether this task is past its due date and unfinished.

        Returns:
            bool: True when overdue.
        """
        return (
            self.due_on is not None
            and self.status != TaskStatus.DONE
            and self.due_on < date.today()
        )

    def mark(self, status: TaskStatus) -> None:
        """Change the status, maintaining ``completed_at``.

        Args:
            status: The new status.

        Note:
            Keeping the timestamp in step with the status here — rather than in
            each view — means no code path can set ``DONE`` and forget to stamp
            the completion time. One writer per derived field (Day 08).
        """
        self.status = status
        self.completed_at = datetime.now(timezone.utc) if status == TaskStatus.DONE else None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API.

        Returns:
            dict[str, Any]: JSON-safe representation.
        """
        return {
            "id": self.id,
            "title": self.title,
            "notes": self.notes,
            "status": self.status.value,
            "priority": self.priority.value,
            "due_on": self.due_on.isoformat() if self.due_on else None,
            "is_overdue": self.is_overdue,
            "project": {"id": self.project_id, "name": self.project.name},
            "assignee": (
                {"id": self.assignee.id, "name": self.assignee.display_name}
                if self.assignee else None
            ),
            "created_at": _iso_utc(self.created_at),
            "completed_at": _iso_utc(self.completed_at) if self.completed_at else None,
        }

    def __repr__(self) -> str:
        """Return an unambiguous representation.

        Returns:
            str: e.g. ``<Task 3 'Write docs' todo>``.
        """
        return f"<Task {self.id} {self.title!r} {self.status.value}>"


def _iso_utc(value: datetime) -> str:
    """Render a datetime as ISO 8601 in UTC with an explicit offset.

    Args:
        value: A naive or aware datetime.

    Returns:
        str: e.g. ``"2026-08-20T13:06:30+00:00"``.

    Note:
        SQLite has no timezone type, so values return naive even from a
        ``DateTime(timezone=True)`` column. Normalising here keeps the API
        contract identical across backends — see Day 11 §11.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()

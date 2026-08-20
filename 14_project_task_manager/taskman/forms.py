"""Day 14 — Forms (Day 05 patterns applied to the task domain)."""

from __future__ import annotations

from datetime import date

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    Optional,
    ValidationError,
)

from .models import TaskPriority, TaskStatus

STATUS_CHOICES = [(s.value, s.label) for s in TaskStatus]
PRIORITY_CHOICES = [(p.value, p.label) for p in TaskPriority]


class RegisterForm(FlaskForm):
    """Create an account."""

    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=160)],
                        render_kw={"autocomplete": "email"})
    display_name = StringField("Display name",
                               validators=[DataRequired(), Length(min=2, max=80)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=128)],
                             render_kw={"autocomplete": "new-password"})
    confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
        render_kw={"autocomplete": "new-password"},
    )
    submit = SubmitField("Create account")


class LoginForm(FlaskForm):
    """Sign in."""

    email = StringField("Email", validators=[DataRequired(), Email()],
                        render_kw={"autocomplete": "email", "autofocus": True})
    password = PasswordField("Password", validators=[DataRequired()],
                             render_kw={"autocomplete": "current-password"})
    remember = BooleanField("Keep me signed in")
    submit = SubmitField("Sign in")


class ProjectForm(FlaskForm):
    """Create or edit a project."""

    name = StringField("Project name", validators=[DataRequired(), Length(max=120)],
                       render_kw={"placeholder": "Website relaunch"})
    description = TextAreaField("Description", validators=[Optional(), Length(max=2000)],
                                render_kw={"rows": 3})
    submit = SubmitField("Save project")


class TaskForm(FlaskForm):
    """Create or edit a task.

    Attributes:
        title: What needs doing.
        notes: Optional detail.
        status: Lifecycle state.
        priority: Urgency.
        due_on: Optional due date.
        assignee_id: Optional assignee; choices are loaded per request.
        submit: Submit button.
    """

    title = StringField("Title", validators=[DataRequired(), Length(max=200)])
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=4000)],
                          render_kw={"rows": 3})
    status = SelectField("Status", choices=STATUS_CHOICES, default=TaskStatus.TODO.value)
    priority = SelectField("Priority", choices=PRIORITY_CHOICES,
                           default=TaskPriority.MEDIUM.value)
    due_on = DateField("Due date", validators=[Optional()])

    # coerce=int would crash on the empty "unassigned" option, because
    # int("") raises ValueError. A small custom coercer keeps "" as None.
    assignee_id = SelectField("Assignee", coerce=lambda v: int(v) if v not in (None, "", "None") else None,
                              validators=[Optional()])
    submit = SubmitField("Save task")

    def validate_due_on(self, field: DateField) -> None:
        """Warn about due dates far in the past.

        Args:
            field: The ``due_on`` field.

        Raises:
            ValidationError: when the date is implausibly old.

        Note:
            A *past* due date is legitimate — you can log work that was already
            late. A date in 1970 is a typo. Validators should reject mistakes,
            not legitimate-but-unusual input.
        """
        if field.data and field.data < date(2000, 1, 1):
            raise ValidationError("That date looks like a typo.")

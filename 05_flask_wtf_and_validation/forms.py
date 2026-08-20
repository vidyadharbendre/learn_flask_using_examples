"""
Day 05 — Form definitions (Flask-WTF + WTForms).
=================================================

Why a separate ``forms.py``?
----------------------------
Forms are neither routing nor persistence: they are the **validation boundary**
between the outside world and your domain. Giving them their own module keeps
views short, makes forms importable from tests without a request context, and
is the layout every real Flask project converges on:

    app.py / views.py   ->  what happens
    forms.py            ->  what is acceptable input
    models.py           ->  what is stored          (Day 08)

A :class:`~flask_wtf.FlaskForm` is a *declarative* description of a form. You
list the fields and the rules; WTForms handles parsing, coercion, validation,
error collection, and re-populating the fields on failure — all the machinery
you wrote by hand on Day 04.
"""

from __future__ import annotations

from datetime import date

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    EmailField,
    IntegerField,
    RadioField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
    URLField,
)
from wtforms.validators import (
    AnyOf,
    DataRequired,
    Email,
    InputRequired,
    Length,
    NumberRange,
    Optional,
    Regexp,
    ValidationError,
)

# Choice lists live next to the form that uses them. Passing them as `choices`
# means WTForms enforces the allow-list for you — the manual `if value not in
# TEAM_SIZES` check from Day 04 is now automatic.
ROLES: list[tuple[str, str]] = [
    ("", "Choose a role…"),
    ("data-engineer", "Data Engineer"),
    ("ml-engineer", "ML Engineer"),
    ("backend", "Backend Engineer"),
    ("analyst", "Business Analyst"),
]

WORK_MODES: list[tuple[str, str]] = [
    ("onsite", "On-site"),
    ("hybrid", "Hybrid"),
    ("remote", "Remote"),
]

BLOCKED_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com"}
)


def work_email(form: FlaskForm, field: EmailField) -> None:
    """Reject free consumer-mail domains — a **reusable** custom validator.

    A WTForms validator is any callable taking ``(form, field)`` that raises
    :class:`~wtforms.validators.ValidationError` to signal failure. Because it
    is a plain function, it can be attached to any field on any form:

        email = EmailField("Email", validators=[Email(), work_email])

    Args:
        form: The form being validated. Gives access to sibling fields, which
            is how cross-field rules are written.
        field: The field under validation; the submitted value is ``field.data``.

    Raises:
        ValidationError: when the address uses a known consumer-mail domain.

    Note:
        Compare this with :meth:`ApplicationForm.validate_expected_salary`,
        which is an *inline* validator: same idea, but bound to one field on one
        form. Use a function when the rule is reusable, a method when it is not.
    """
    if not field.data or "@" not in field.data:
        return  # Let Email() report a malformed address; do not double-report.
    domain = field.data.rsplit("@", 1)[1].lower()
    if domain in BLOCKED_EMAIL_DOMAINS:
        raise ValidationError("Please use your work email, not a personal one.")


class ApplicationForm(FlaskForm):
    """A job application.

    Every field below demonstrates something distinct. Read them as a catalogue
    of the WTForms features you will actually use.

    Attributes:
        full_name: ``StringField`` with a length rule.
        email: ``EmailField`` combining a built-in and a custom validator.
        phone: Optional field — note ``Optional()`` must come **first**.
        role: ``SelectField`` whose ``choices`` are the allow-list.
        years_experience: ``IntegerField``; WTForms coerces and range-checks it.
        expected_salary: Validated against ``years_experience`` (cross-field).
        work_mode: ``RadioField`` — same data model as a select, different UI.
        available_from: ``DateField`` producing a real ``datetime.date``.
        portfolio: Optional URL.
        cover_letter: ``TextAreaField`` with a minimum length.
        relocate: ``BooleanField`` — checkboxes are absent when unticked.
        consent: Must be ticked; see why ``InputRequired`` is used, not
            ``DataRequired``.
        submit: Renders the button.
    """

    full_name = StringField(
        "Full name",
        validators=[
            # DataRequired rejects empty AND whitespace-only input.
            DataRequired(message="We need your name."),
            Length(min=2, max=80, message="Between 2 and 80 characters."),
        ],
        # render_kw injects raw HTML attributes onto the rendered <input>.
        # Use it for placeholders and autocomplete hints, never for validation
        # rules — those belong in `validators` where they are enforced.
        render_kw={"placeholder": "Ananya Rao", "autocomplete": "name"},
    )

    email = EmailField(
        "Work email",
        validators=[
            DataRequired(message="An email is required."),
            Email(message="That is not a valid email address."),
            work_email,  # our reusable custom validator
        ],
        render_kw={"placeholder": "you@company.com", "autocomplete": "email"},
        # filters run BEFORE validators and normalise the raw input.
        # Storing "A@Example.COM" and "a@example.com" as two people is a
        # classic data-quality bug; normalise at the boundary.
        filters=[lambda value: value.strip().lower() if isinstance(value, str) else value],
    )

    phone = StringField(
        "Phone",
        validators=[
            # ORDER MATTERS. Optional() short-circuits the whole chain when the
            # field is empty, so Regexp never sees a blank string. Put it first
            # or an empty optional field will fail its own format check.
            Optional(),
            Regexp(r"^\+?[0-9\s-]{10,15}$",
                   message="10-15 digits, optionally starting with +."),
        ],
        render_kw={"placeholder": "+91 98765 43210", "autocomplete": "tel"},
    )

    role = SelectField(
        "Role applied for",
        choices=ROLES,
        # SelectField validates that the submitted value is one of `choices`
        # automatically. A hand-crafted POST with role=ceo is rejected without
        # you writing a line — this is the Day 04 allow-list, for free.
        validators=[DataRequired(message="Pick a role.")],
    )

    years_experience = IntegerField(
        "Years of experience",
        validators=[
            # InputRequired (not DataRequired) because 0 is a legitimate answer
            # and DataRequired treats every falsy value — including 0 — as
            # missing. This is the single most common WTForms gotcha.
            InputRequired(message="Enter a number (0 is fine)."),
            NumberRange(min=0, max=50, message="Between 0 and 50."),
        ],
        render_kw={"min": 0, "max": 50},
    )

    expected_salary = IntegerField(
        "Expected annual salary (INR)",
        validators=[
            InputRequired(message="Give us a number so we can be transparent."),
            NumberRange(min=100_000, message="Please enter an annual figure."),
        ],
        render_kw={"step": 50_000},
    )

    work_mode = RadioField(
        "Preferred work mode",
        choices=WORK_MODES,
        default="hybrid",
        validators=[AnyOf([value for value, _ in WORK_MODES])],
    )

    available_from = DateField(
        "Available from",
        validators=[DataRequired(message="When could you start?")],
        # DateField parses the input and hands your view a datetime.date,
        # not a string. Typed data at the boundary means no parsing later.
    )

    portfolio = URLField(
        "Portfolio or GitHub",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "https://github.com/you"},
    )

    cover_letter = TextAreaField(
        "Why this role?",
        validators=[
            DataRequired(message="A few sentences, please."),
            Length(min=40, max=2000,
                   message="Between 40 and 2000 characters."),
        ],
        render_kw={"rows": 6},
    )

    relocate = BooleanField("I am willing to relocate")

    consent = BooleanField(
        "I consent to my data being stored for this application",
        # A checkbox that is NOT ticked is simply absent from the POST body.
        # DataRequired works here too, but InputRequired states the intent
        # precisely: the input must be present.
        validators=[InputRequired(message="We cannot proceed without consent.")],
    )

    submit = SubmitField("Submit application")

    def validate_expected_salary(self, field: IntegerField) -> None:
        """Cross-field rule: expectations must track experience.

        WTForms automatically calls any method named ``validate_<fieldname>``
        after that field's own validator chain passes. This is the idiomatic
        home for rules that depend on **another** field, because ``self`` gives
        you the whole form.

        Args:
            field: The ``expected_salary`` field being validated.

        Raises:
            ValidationError: when a junior applicant asks for a senior number.

        Note:
            Guard against ``None``. If ``years_experience`` itself failed to
            validate, its ``.data`` is ``None`` and arithmetic would raise
            ``TypeError`` — surfacing as a 500 instead of a form error.
        """
        years = self.years_experience.data
        if years is None or field.data is None:
            return
        if years < 2 and field.data > 2_000_000:
            raise ValidationError(
                "That range usually needs 2+ years of experience. "
                "Tell us more in your cover letter if you disagree."
            )

    def validate_available_from(self, field: DateField) -> None:
        """Reject start dates in the past.

        Args:
            field: The ``available_from`` field.

        Raises:
            ValidationError: when the chosen date has already passed.
        """
        if field.data and field.data < date.today():
            raise ValidationError("Pick today or a future date.")


class SearchForm(FlaskForm):
    """A GET-based filter form for the applications list.

    Attributes:
        q: Free-text query.
        role: Optional role filter.

    Note:
        CSRF is disabled here on purpose. CSRF protects **state-changing**
        requests. A GET form that only filters a list changes nothing, and a
        CSRF token in the URL would be ugly, cached, and leaked in Referer
        headers. Disable it for GET filters; never for POST.
    """

    class Meta:
        """WTForms configuration hook for this form only."""

        csrf = False

    q = StringField("Search", validators=[Optional(), Length(max=80)],
                    render_kw={"placeholder": "Name or company…"})
    role = SelectField("Role", choices=[("", "Any role"), *ROLES[1:]],
                       validators=[Optional()])
    submit = SubmitField("Filter")

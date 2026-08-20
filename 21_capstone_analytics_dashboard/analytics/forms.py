"""Day 21 — Forms (Day 05)."""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField, IntegerField, PasswordField, SelectField, StringField,
    SubmitField, TextAreaField,
)
from wtforms.validators import (
    DataRequired, Email, EqualTo, InputRequired, Length, NumberRange, Optional,
)

from .models import SurveyStatus


class RegisterForm(FlaskForm):
    """Create an account."""

    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=160)],
                        render_kw={"autocomplete": "email"})
    display_name = StringField("Your name",
                               validators=[DataRequired(), Length(min=2, max=80)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=128)],
                             render_kw={"autocomplete": "new-password"})
    confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
        render_kw={"autocomplete": "new-password"})
    submit = SubmitField("Create account")


class LoginForm(FlaskForm):
    """Sign in."""

    email = StringField("Email", validators=[DataRequired(), Email()],
                        render_kw={"autocomplete": "email", "autofocus": True})
    password = PasswordField("Password", validators=[DataRequired()],
                             render_kw={"autocomplete": "current-password"})
    remember = BooleanField("Keep me signed in")
    submit = SubmitField("Sign in")


class SurveyForm(FlaskForm):
    """Create or edit a survey."""

    title = StringField("Title", validators=[DataRequired(), Length(min=3, max=140)],
                        render_kw={"placeholder": "Onboarding experience"})
    question = StringField(
        "Question", validators=[DataRequired(), Length(min=5, max=280)],
        render_kw={"placeholder": "How likely are you to recommend us?"})
    status = SelectField("Status", choices=[(s.value, s.label) for s in SurveyStatus],
                         default=SurveyStatus.DRAFT.value)
    submit = SubmitField("Save survey")


class PublicResponseForm(FlaskForm):
    """The public response form.

    Note:
        CSRF stays **on**. This is a public, unauthenticated POST, but CSRF
        protection costs nothing and stops a third-party page from submitting
        responses on a visitor's behalf. "Public" is not a reason to drop it —
        only a token-authenticated API is (Day 15 §9).
    """

    score = IntegerField("Your score (0-10)",
                         validators=[InputRequired(message="Please choose a score."),
                                     NumberRange(min=0, max=10)],
                         render_kw={"type": "range", "min": "0", "max": "10", "value": "8"})
    comment = TextAreaField("Anything else? (optional)",
                            validators=[Optional(), Length(max=1000)],
                            render_kw={"rows": 3})
    submit = SubmitField("Submit feedback")

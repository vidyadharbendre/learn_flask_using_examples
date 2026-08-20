"""Day 13 — Authentication forms (Day 05 patterns)."""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError


def strong_password(form: FlaskForm, field: PasswordField) -> None:
    """Require a password with some real variety.

    Args:
        form: The form being validated.
        field: The password field.

    Raises:
        ValidationError: when the password is too simple.

    Note:
        Current NIST guidance favours **length** over forced symbol classes:
        long passphrases beat ``P@ssw0rd!``, and complexity rules push people
        towards predictable substitutions and sticky notes. The single most
        valuable check is against a list of known-breached passwords (see the
        exercises), which this stands in for.
    """
    value = field.data or ""
    if len(value) < 12 and not (
        any(c.islower() for c in value)
        and any(c.isupper() for c in value)
        and any(c.isdigit() for c in value)
    ):
        raise ValidationError(
            "Use 12+ characters, or mix upper case, lower case and digits."
        )
    if value.lower() in {"password", "password123", "letmein", "qwerty123456"}:
        raise ValidationError("That password is far too common.")


class RegisterForm(FlaskForm):
    """Create an account.

    Attributes:
        email: Login identifier.
        display_name: Name shown in the UI.
        password: New password.
        confirm: Must equal ``password``.
        submit: Submit button.
    """

    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=160)],
                        render_kw={"autocomplete": "email"})
    display_name = StringField("Display name",
                               validators=[DataRequired(), Length(min=2, max=80)],
                               render_kw={"autocomplete": "name"})
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(min=8, max=128), strong_password],
        # autocomplete="new-password" tells password managers to OFFER to
        # generate one. Getting these hints right measurably improves the
        # passwords your users actually choose.
        render_kw={"autocomplete": "new-password"},
    )
    confirm = PasswordField(
        "Confirm password",
        # EqualTo compares against another field by NAME.
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
        render_kw={"autocomplete": "new-password"},
    )
    submit = SubmitField("Create account")


class LoginForm(FlaskForm):
    """Sign in.

    Attributes:
        email: Login identifier.
        password: Password.
        remember: Whether to stay signed in.
        submit: Submit button.

    Note:
        No ``Length`` or ``strong_password`` validator on the password here.
        Applying password *rules* at login is pointless — the password either
        matches the stored hash or it does not — and rejecting an over-long
        input before checking it leaks information about your policy.
    """

    email = StringField("Email", validators=[DataRequired(), Email()],
                        render_kw={"autocomplete": "email", "autofocus": True})
    password = PasswordField("Password", validators=[DataRequired()],
                             render_kw={"autocomplete": "current-password"})
    remember = BooleanField("Keep me signed in")
    submit = SubmitField("Sign in")


class PasswordChangeForm(FlaskForm):
    """Change the signed-in user's password.

    Attributes:
        current_password: Proof the person at the keyboard is the account owner.
        new_password: The replacement.
        confirm: Must equal ``new_password``.
        submit: Submit button.
    """

    current_password = PasswordField("Current password", validators=[DataRequired()],
                                     render_kw={"autocomplete": "current-password"})
    new_password = PasswordField(
        "New password",
        validators=[DataRequired(), Length(min=8, max=128), strong_password],
        render_kw={"autocomplete": "new-password"},
    )
    confirm = PasswordField(
        "Confirm new password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")],
        render_kw={"autocomplete": "new-password"},
    )
    submit = SubmitField("Update password")


class NoteForm(FlaskForm):
    """Create a private note.

    Attributes:
        title: Short heading.
        body: Note content.
        submit: Submit button.
    """

    title = StringField("Title", validators=[DataRequired(), Length(max=120)])
    body = TextAreaField("Body", validators=[Length(max=2000)], render_kw={"rows": 4})
    submit = SubmitField("Save note")

"""
Day 07 — Forms for the expense tracker.

Applies the Day 05 patterns to a real domain: a money field that must be
positive and bounded, a date that cannot be in the future, allow-listed
categories, and a GET filter form with CSRF deliberately disabled.
"""

from __future__ import annotations

from datetime import date

from flask_wtf import FlaskForm
from wtforms import (
    DateField,
    DecimalField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError

from storage import CATEGORIES, PAYMENT_METHODS

# Build (value, label) pairs once. "eating-out" -> "Eating Out".
CATEGORY_CHOICES: list[tuple[str, str]] = [
    (c, c.replace("-", " ").title()) for c in CATEGORIES
]
PAYMENT_CHOICES: list[tuple[str, str]] = [
    (p, p.upper() if p == "upi" else p.title()) for p in PAYMENT_METHODS
]


class ExpenseForm(FlaskForm):
    """Record a single expense.

    Attributes:
        spent_on: Date of spending; defaults to today and may not be in the future.
        description: Short label.
        category: Allow-listed category.
        amount: Rupees as a ``Decimal``; converted to integer paise before storage.
        payment_method: Allow-listed payment method.
        note: Optional free text.
        submit: Submit button.
    """

    spent_on = DateField(
        "Date",
        validators=[DataRequired(message="When did you spend it?")],
        # `default` accepts a callable, evaluated per request. Passing
        # `date.today()` directly would freeze the date at import time — the
        # app would still offer yesterday's date tomorrow. This is the same
        # mutable/early-binding default trap as `def f(x=[])`.
        default=date.today,
    )

    description = StringField(
        "Description",
        validators=[DataRequired(message="What was it for?"), Length(max=100)],
        render_kw={"placeholder": "Weekly groceries", "autocomplete": "off"},
    )

    category = SelectField(
        "Category", choices=CATEGORY_CHOICES,
        validators=[DataRequired(message="Pick a category.")],
    )

    amount = DecimalField(
        "Amount (₹)",
        # DecimalField, not FloatField: it parses to Decimal, so the value the
        # user typed is preserved exactly until we convert it to integer paise.
        places=2,
        validators=[
            DataRequired(message="Enter an amount."),
            NumberRange(min=0.01, max=10_00_000,
                        message="Between ₹0.01 and ₹10,00,000."),
        ],
        render_kw={"step": "0.01", "min": "0.01", "placeholder": "249.50"},
    )

    payment_method = SelectField(
        "Paid by", choices=PAYMENT_CHOICES, default="upi",
        validators=[DataRequired()],
    )

    note = TextAreaField(
        "Note", validators=[Optional(), Length(max=300)],
        render_kw={"rows": 2, "placeholder": "Optional details"},
    )

    submit = SubmitField("Add expense")

    def validate_spent_on(self, field: DateField) -> None:
        """Reject future-dated expenses.

        You cannot have spent money you have not spent yet, and future dates
        quietly corrupt every monthly total.

        Args:
            field: The ``spent_on`` field.

        Raises:
            ValidationError: when the date is after today.
        """
        if field.data and field.data > date.today():
            raise ValidationError("That date is in the future.")

    def amount_paise(self) -> int:
        """Return the submitted amount as integer paise.

        Keeping this conversion on the form means the view never handles a
        float, and there is exactly one place where rupees become paise.

        Returns:
            int: Amount in paise; ``0`` when the field did not validate.
        """
        return int(round(float(self.amount.data) * 100)) if self.amount.data else 0


class FilterForm(FlaskForm):
    """GET filters for the expense list.

    Note:
        CSRF is off: this form only reads. See Day 05 §9.
    """

    class Meta:
        """Disable CSRF for this read-only GET form."""

        csrf = False

    q = StringField("Search", validators=[Optional(), Length(max=60)],
                    render_kw={"placeholder": "Description or note…"})
    category = SelectField(
        "Category", choices=[("", "All categories"), *CATEGORY_CHOICES],
        validators=[Optional()],
    )
    month = StringField(
        "Month", validators=[Optional(), Length(max=7)],
        render_kw={"type": "month", "placeholder": "YYYY-MM"},
    )
    submit = SubmitField("Apply")

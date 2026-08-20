"""Day 08 — Forms for the inventory manager (Day 05 patterns, DB-aware)."""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import DecimalField, IntegerField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange, Regexp


class ProductForm(FlaskForm):
    """Create a product.

    Attributes:
        sku: Uppercase alphanumeric business key.
        name: Display name.
        price: Unit price; ``DecimalField`` keeps it exact.
        quantity: Opening stock; ``0`` is valid, hence ``InputRequired``.
        reorder_level: Low-stock threshold.
        category_id: Populated from the database at request time.
        submit: Submit button.
    """

    sku = StringField(
        "SKU",
        validators=[
            DataRequired(message="Every product needs a SKU."),
            Length(min=3, max=32),
            Regexp(r"^[A-Za-z0-9\-]+$",
                   message="Letters, numbers and hyphens only."),
        ],
        render_kw={"placeholder": "LAP-001", "style": "text-transform:uppercase"},
    )
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    price = DecimalField(
        "Unit price (₹)", places=2,
        validators=[InputRequired(), NumberRange(min=0, max=10_00_000)],
        render_kw={"step": "0.01", "min": "0"},
    )
    quantity = IntegerField(
        "Opening quantity",
        validators=[InputRequired(), NumberRange(min=0)],
        default=0, render_kw={"min": "0"},
    )
    reorder_level = IntegerField(
        "Reorder level",
        validators=[InputRequired(), NumberRange(min=0)],
        default=5, render_kw={"min": "0"},
    )
    # Choices are set in the view, not here: they come from the database and
    # would otherwise be frozen at import time — the same early-binding trap as
    # `default=date.today()` on Day 07.
    category_id = SelectField("Category", coerce=int, validators=[InputRequired()])
    submit = SubmitField("Add product")


class MovementForm(FlaskForm):
    """Record a stock movement.

    Attributes:
        delta: Signed change; negative removes stock.
        reason: Why the stock moved.
        submit: Submit button.
    """

    delta = IntegerField(
        "Change (+ receive / − remove)",
        validators=[InputRequired(message="Enter a non-zero change."),
                    NumberRange(min=-10_000, max=10_000)],
    )
    reason = StringField(
        "Reason", validators=[DataRequired(), Length(max=120)],
        render_kw={"placeholder": "Received from supplier"},
    )
    submit = SubmitField("Record movement")

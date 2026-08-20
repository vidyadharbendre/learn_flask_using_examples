"""Day 10 — Forms (Day 05 patterns; choices loaded per request)."""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import DecimalField, IntegerField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange, Regexp


class ProductForm(FlaskForm):
    """Create or edit a product.

    Attributes:
        sku: Unique business key.
        name: Display name.
        price: Exact decimal price.
        quantity: Units on hand; ``0`` is valid.
        reorder_level: Low-stock threshold.
        category_id: Chosen from the database at request time.
        submit: Submit button.
    """

    sku = StringField(
        "SKU",
        validators=[DataRequired(), Length(min=3, max=32),
                    Regexp(r"^[A-Za-z0-9\-]+$",
                           message="Letters, numbers and hyphens only.")],
        render_kw={"placeholder": "LAP-001"},
    )
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    price = DecimalField("Unit price (₹)", places=2,
                         validators=[InputRequired(), NumberRange(min=0)],
                         render_kw={"step": "0.01", "min": "0"})
    quantity = IntegerField("Quantity", validators=[InputRequired(), NumberRange(min=0)],
                            default=0, render_kw={"min": "0"})
    reorder_level = IntegerField("Reorder level",
                                 validators=[InputRequired(), NumberRange(min=0)],
                                 default=5, render_kw={"min": "0"})
    category_id = SelectField("Category", coerce=int, validators=[InputRequired()])
    submit = SubmitField("Save product")

"""
Day 03 — Jinja Templates and Static Files: stop repeating yourself in HTML.
===========================================================================

Real-world scenario
-------------------
A pricing and product-catalogue page for an analytics SaaS. Catalogue pages are
the classic case where beginners copy-paste the same 20 lines of card markup
five times. Today you learn the four tools that eliminate that duplication:
**macros**, **includes**, **custom filters**, and **context processors**.

What you will learn
-------------------
1. **Macros** — reusable, parameterised template functions (``{% macro %}``).
2. **Includes** — dropping a shared partial into a page (``{% include %}``).
3. **Custom filters** — moving formatting logic out of views and into Jinja
   (``@app.template_filter``).
4. **Context processors** — injecting variables into *every* template without
   passing them from every view (``@app.context_processor``).
5. **Autoescaping and ``|safe``** — why Jinja escapes by default and the narrow
   circumstances in which you may switch it off.
6. **Organising ``static/``** into ``css/``, ``js/`` and ``img/``.

How to run
----------
From the repository root::

    source .venv/bin/activate
    flask --app 03_jinja_templates_and_static/app.py run --port 5003 --debug

Design rule of the day
----------------------
**Views prepare data; templates present it.** If you find yourself building
HTML strings in Python, or writing business logic inside ``{% if %}``, the
responsibility has drifted to the wrong layer. Formatting (currency, dates,
pluralisation) belongs in a filter — testable in isolation, usable from every
template.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Final, TypedDict

from flask import Flask, abort, render_template
from markupsafe import Markup, escape

app = Flask(__name__)


# -----------------------------------------------------------------------------
# Domain data
# -----------------------------------------------------------------------------
class Plan(TypedDict):
    """One purchasable subscription tier.

    Attributes:
        slug: URL-safe identifier, used as the primary key.
        name: Display name of the tier.
        price_inr: Monthly price in whole rupees.
        seats: Number of included user seats.
        features: Bullet points shown on the pricing card.
        popular: Whether to render the "most popular" highlight.
        blurb: Short marketing sentence. Stored as **plain text**, deliberately
            containing characters that must be escaped, so you can see
            autoescaping at work.
    """

    slug: str
    name: str
    price_inr: int
    seats: int
    features: list[str]
    popular: bool
    blurb: str


PLANS: Final[list[Plan]] = [
    {
        "slug": "starter", "name": "Starter", "price_inr": 1499, "seats": 3,
        "features": ["5 dashboards", "7-day history", "Email support"],
        "popular": False,
        "blurb": "For solo analysts & tiny teams <no credit card needed>",
    },
    {
        "slug": "growth", "name": "Growth", "price_inr": 6999, "seats": 15,
        "features": ["Unlimited dashboards", "1-year history",
                     "Scheduled reports", "Priority support"],
        "popular": True,
        "blurb": "Our most-chosen plan — scales with you",
    },
    {
        "slug": "enterprise", "name": "Enterprise", "price_inr": 24999, "seats": 100,
        "features": ["Everything in Growth", "SSO & audit logs",
                     "Dedicated success manager", "99.9% uptime SLA"],
        "popular": False,
        "blurb": "Compliance, SSO & SLAs for regulated teams",
    },
]


# -----------------------------------------------------------------------------
# Custom Jinja filters
# -----------------------------------------------------------------------------
# A filter is just a Python function registered under a name. Inside a template
# `{{ 6999|inr }}` calls `inr(6999)`. Filters are the right home for formatting:
# they are pure functions, unit-testable, and reusable from every template.
@app.template_filter("inr")
def format_inr(amount: int | float) -> str:
    """Format a number as Indian Rupees using the lakh/crore grouping.

    Western grouping puts a separator every three digits (1,234,567). The Indian
    system groups the last three digits, then pairs (12,34,567). Getting this
    right matters to Indian users the same way ``$1,234.56`` matters elsewhere.

    Args:
        amount: A rupee value, e.g. ``1499`` or ``2499.5``.

    Returns:
        str: The formatted string, e.g. ``"₹1,499"`` or ``"₹12,34,567"``.

    Example:
        >>> format_inr(1499)
        '₹1,499'
        >>> format_inr(2400000)
        '₹24,00,000'
    """
    whole = int(round(amount))
    sign = "-" if whole < 0 else ""
    digits = str(abs(whole))

    if len(digits) <= 3:
        grouped = digits
    else:
        last3, rest = digits[-3:], digits[:-3]
        # Walk the remaining digits right-to-left in pairs (the lakh/crore rule).
        pairs: list[str] = []
        while len(rest) > 2:
            pairs.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            pairs.insert(0, rest)
        grouped = ",".join([*pairs, last3])

    return f"{sign}₹{grouped}"


@app.template_filter("pluralize")
def pluralize(count: int, singular: str, plural: str | None = None) -> str:
    """Return ``count`` with a correctly pluralised noun.

    Writing ``{{ n }} seat{% if n != 1 %}s{% endif %}`` in fifteen templates is
    how inconsistencies creep in. One filter, one behaviour.

    Args:
        count: How many things there are.
        singular: The singular noun, e.g. ``"seat"``.
        plural: Irregular plural. Defaults to ``singular + "s"``.

    Returns:
        str: e.g. ``"1 seat"``, ``"15 seats"``, ``"3 people"``.
    """
    word = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {word}"


@app.template_filter("highlight")
def highlight(text: str, term: str) -> Markup:
    """Wrap every occurrence of ``term`` in ``<mark>`` tags.

    This filter *intentionally* produces HTML, which makes it the one place you
    must think hard about escaping:

    - ``escape(text)`` neutralises any markup in the untrusted input **first**.
    - Only then do we add our own trusted ``<mark>`` tags.
    - Returning :class:`~markupsafe.Markup` tells Jinja "this is already safe,
      do not escape it again".

    Doing it in the other order — building the string then calling ``|safe`` —
    is exactly how cross-site-scripting holes are created.

    Args:
        text: Untrusted display text.
        term: Substring to highlight. Empty terms are a no-op.

    Returns:
        Markup: Escaped text with ``<mark>`` around each case-insensitive match.
    """
    if not term:
        return Markup(escape(text))

    safe_text = str(escape(text))
    needle = escape(term).lower()

    out: list[str] = []
    cursor = 0
    lowered = safe_text.lower()
    while (idx := lowered.find(str(needle), cursor)) != -1:
        out.append(safe_text[cursor:idx])
        out.append(f"<mark>{safe_text[idx:idx + len(needle)]}</mark>")
        cursor = idx + len(needle)
    out.append(safe_text[cursor:])
    return Markup("".join(out))


# -----------------------------------------------------------------------------
# Context processor
# -----------------------------------------------------------------------------
@app.context_processor
def inject_site_globals() -> dict[str, Any]:
    """Make site-wide values available in **every** template automatically.

    Without this, every single view would have to pass ``company=...`` and
    ``year=...`` into ``render_template``, and the day you forget one, the
    footer silently renders blank.

    A context processor runs once per request, just before rendering, and its
    returned dict is merged into the template context.

    Returns:
        dict[str, Any]: Globals available to all templates.

    Best practice:
        Keep this cheap. It runs on every render, so never put a database query
        here — cache the value or use a Jinja global instead.
    """
    return {
        "company": "Reinforcement Analytics",
        "current_year": datetime.now(timezone.utc).year,
        "support_email": "support@example.com",
    }


# -----------------------------------------------------------------------------
# Views
# -----------------------------------------------------------------------------
@app.route("/")
def pricing() -> str:
    """Render the pricing page — three plan cards built from ONE macro.

    Notice how little this view does: it selects data and hands it over. All
    presentation lives in the templates.

    Returns:
        str: Rendered ``pricing.html``.
    """
    return render_template("pricing.html", plans=PLANS)


@app.route("/plans/<slug>")
def plan_detail(slug: str) -> str:
    """Render one plan's detail page.

    Args:
        slug: The plan's URL-safe identifier, e.g. ``growth``.

    Returns:
        str: Rendered ``plan_detail.html``.

    Raises:
        werkzeug.exceptions.NotFound: when no plan matches ``slug``.
    """
    plan = next((p for p in PLANS if p["slug"] == slug), None)
    if plan is None:
        abort(404, description=f"No plan named {slug!r}.")
    return render_template("plan_detail.html", plan=plan)


@app.route("/escaping-demo")
def escaping_demo() -> str:
    """Show autoescaping, ``|safe``, and the ``highlight`` filter side by side.

    Open this page and **view source**. The same hostile string is rendered four
    ways so you can see exactly which ones neutralise it and which one executes.

    Returns:
        str: Rendered ``escaping_demo.html``.
    """
    hostile = "<script>alert('xss')</script> Bobby <b>Tables</b>"
    trusted = Markup("<em>This markup was produced by our own code.</em>")
    return render_template("escaping_demo.html", hostile=hostile, trusted=trusted)


@app.errorhandler(404)
def not_found(error: Exception) -> tuple[str, int]:
    """Render the shared 404 page.

    Args:
        error: The ``NotFound`` exception raised by :func:`~flask.abort`.

    Returns:
        tuple[str, int]: Rendered HTML and the 404 status code.
    """
    return render_template("404.html", error=error), 404


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5003, debug=True)

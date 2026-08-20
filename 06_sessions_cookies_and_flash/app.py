"""
Day 06 — Sessions, Cookies and Flash: remembering users between requests.
=========================================================================

Real-world scenario
-------------------
A shopping cart for a small bookstore. A cart is the canonical session problem:
it must survive navigation, it belongs to *this* browser only, it must not
require a login, and it must not be forgeable.

The thing to internalise
------------------------
**HTTP is stateless.** Every request arrives with no memory of the last one. The
*only* reason a server can tell that two requests came from the same person is
that the browser echoes back a cookie. Sessions are a thin, signed layer over
that single mechanism.

What you will learn
-------------------
1. ``session`` — a dict serialised into a **signed** cookie.
2. **Signed does not mean encrypted.** You will decode your own session cookie
   in the terminal and read its contents as plain text.
3. Mutation gotchas: ``session.modified`` and why mutating a nested structure
   silently fails to save.
4. Permanent vs browser sessions and ``PERMANENT_SESSION_LIFETIME``.
5. Raw cookies via ``response.set_cookie`` and the flags that matter:
   ``HttpOnly``, ``Secure``, ``SameSite``.
6. ``flash()`` — a one-shot message that survives exactly one redirect.
7. Where client-side sessions stop being appropriate.

How to run
----------
From the repository root::

    source .venv/bin/activate
    flask --app 06_sessions_cookies_and_flash/app.py run --port 5006 --debug

The rule that prevents a breach
-------------------------------
Flask's default session is **signed, not encrypted**. Anyone holding the cookie
can read every value inside it. Store identifiers and preferences; never store
passwords, API keys, card numbers, or trust decisions you have not re-verified
on the server.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Final, TypedDict

from flask import (
    Flask,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.wrappers import Response

app = Flask(__name__)

# -----------------------------------------------------------------------------
# Session and cookie configuration
# -----------------------------------------------------------------------------
app.config.update(
    # Signs the session cookie. Change it and every existing session is
    # invalidated — which is also your emergency "log everybody out" button.
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-not-for-production"),

    # HttpOnly: JavaScript cannot read the cookie via document.cookie. This is
    # what stops a single XSS bug from becoming full session theft. On by
    # default for the session cookie; set it explicitly on cookies you create.
    SESSION_COOKIE_HTTPONLY=True,

    # Secure: only send the cookie over HTTPS. MUST be True in production,
    # otherwise the session travels in clear text on any http:// request.
    # False here so the example works on http://127.0.0.1 (Day 18/20 flip it).
    SESSION_COOKIE_SECURE=False,

    # SameSite: the browser-level CSRF defence. "Lax" sends the cookie on
    # top-level navigations but NOT on cross-site POSTs, which neutralises the
    # classic hidden-form attack from Day 04. "Strict" is safer but breaks
    # inbound links from other sites. "Lax" is the right default.
    SESSION_COOKIE_SAMESITE="Lax",

    # How long a PERMANENT session lasts. Only applies when you set
    # session.permanent = True; otherwise the cookie dies with the browser.
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)


# -----------------------------------------------------------------------------
# Catalogue (the server's source of truth)
# -----------------------------------------------------------------------------
class Book(TypedDict):
    """One purchasable book.

    Attributes:
        sku: Stable identifier used as the cart key.
        title: Display title.
        author: Author name.
        price_inr: Unit price in whole rupees.
        stock: Units available; the cart is clamped to this.
    """

    sku: str
    title: str
    author: str
    price_inr: int
    stock: int


CATALOGUE: Final[dict[str, Book]] = {
    "flask-101": {"sku": "flask-101", "title": "Flask in Practice",
                  "author": "A. Rao", "price_inr": 899, "stock": 12},
    "sql-deep": {"sku": "sql-deep", "title": "SQL, Deeply",
                 "author": "V. Shah", "price_inr": 1250, "stock": 5},
    "py-arch": {"sku": "py-arch", "title": "Python Architecture Patterns",
                "author": "M. Iyer", "price_inr": 1499, "stock": 3},
    "http-guide": {"sku": "http-guide", "title": "HTTP: The Missing Guide",
                   "author": "R. Gupta", "price_inr": 650, "stock": 20},
}

# The session stores ONLY {sku: quantity} — never the price.
CART_KEY: Final[str] = "cart"


# -----------------------------------------------------------------------------
# Cart helpers
# -----------------------------------------------------------------------------
def get_cart() -> dict[str, int]:
    """Return the current cart mapping SKU to quantity.

    Returns:
        dict[str, int]: The cart, or an empty dict for a new visitor.

    Warning:
        This returns a **reference** to the object inside ``session``. Mutating
        it in place — ``cart["x"] = 1`` — does *not* mark the session dirty,
        because Flask cannot detect changes inside a mutable value it handed
        out. Always finish with :func:`save_cart`. This is the number one
        session bug, and it fails *silently*: the code looks right and the cart
        simply never persists.
    """
    cart = session.get(CART_KEY, {})
    return dict(cart) if isinstance(cart, dict) else {}


def save_cart(cart: dict[str, int]) -> None:
    """Persist the cart back into the session.

    Assigning a whole new value to a session key is what marks the session
    dirty, which is what makes Flask re-issue the cookie on this response.

    Args:
        cart: The updated ``{sku: quantity}`` mapping. Empty carts are removed
            entirely so the cookie does not carry dead weight.

    Note:
        The alternative is ``session.modified = True``, which forces a save
        after an in-place mutation. Reassignment is clearer — prefer it, and
        keep ``session.modified`` for the rare case where you truly cannot.
    """
    if cart:
        session[CART_KEY] = cart
    else:
        session.pop(CART_KEY, None)


def cart_lines() -> list[dict[str, Any]]:
    """Expand the stored cart into displayable line items, priced **now**.

    This is the security heart of the example. The session holds quantities
    only; every price and title is looked up from :data:`CATALOGUE` on each
    request. A user who edits their cookie can change *what* and *how many* —
    never *how much it costs*.

    Storing ``{"sku": "x", "price": 1}`` in the session would let anyone with a
    cookie editor buy a laptop for one rupee. This has happened to real shops.

    Returns:
        list[dict[str, Any]]: One entry per line with ``book``, ``quantity``
        and ``subtotal``. Unknown SKUs are skipped rather than raising, because
        a stale cookie must never 500 the page.
    """
    lines: list[dict[str, Any]] = []
    for sku, quantity in get_cart().items():
        book = CATALOGUE.get(sku)
        if book is None:
            continue
        lines.append({
            "book": book,
            "quantity": quantity,
            "subtotal": book["price_inr"] * quantity,
        })
    return lines


def cart_total() -> int:
    """Total value of the cart in rupees.

    Returns:
        int: Sum of every line's subtotal, recomputed from live prices.
    """
    return sum(line["subtotal"] for line in cart_lines())


# -----------------------------------------------------------------------------
# Views
# -----------------------------------------------------------------------------
@app.route("/")
def catalogue() -> str:
    """Show the shop front.

    Returns:
        str: Rendered ``catalogue.html``.
    """
    return render_template(
        "catalogue.html", books=list(CATALOGUE.values()), cart=get_cart()
    )


@app.route("/cart/add/<sku>", methods=["POST"])
def add_to_cart(sku: str) -> Response:
    """Add one unit of ``sku`` to the cart.

    A **POST**, not a GET: this changes state. A GET here would let a crawler,
    a prefetching browser, or an ``<img src="/cart/add/x">`` on another site
    fill someone's cart.

    Args:
        sku: Catalogue identifier from the URL.

    Returns:
        Response: 303 redirect back to wherever the user came from.
    """
    book = CATALOGUE.get(sku)
    if book is None:
        flash("That book is no longer available.", "error")
        return redirect(url_for("catalogue"), code=303)

    cart = get_cart()
    new_quantity = cart.get(sku, 0) + 1

    # Enforce stock on the SERVER. The template disables the button at the
    # limit, but a disabled button is a suggestion, not a constraint.
    if new_quantity > book["stock"]:
        flash(f"Only {book['stock']} copies of “{book['title']}” left.", "warning")
        return redirect(url_for("catalogue"), code=303)

    cart[sku] = new_quantity
    save_cart(cart)
    flash(f"Added “{book['title']}” to your cart.", "success")

    # request.referrer is a HINT from the client and may be absent or hostile.
    # Only ever redirect to a URL you built yourself; never redirect straight to
    # a user-supplied value, or you have an open-redirect vulnerability.
    target = "view_cart" if request.referrer and "/cart" in request.referrer else "catalogue"
    return redirect(url_for(target), code=303)


@app.route("/cart/update/<sku>", methods=["POST"])
def update_cart(sku: str) -> Response:
    """Set an explicit quantity for one line, or remove it when zero.

    Args:
        sku: Catalogue identifier from the URL.

    Returns:
        Response: 303 redirect to the cart page.
    """
    book = CATALOGUE.get(sku)
    if book is None:
        return redirect(url_for("view_cart"), code=303)

    # `type=int` makes Werkzeug coerce and fall back to the default on garbage,
    # so "?quantity=abc" yields 0 rather than raising ValueError.
    quantity = request.form.get("quantity", default=0, type=int)
    quantity = max(0, min(quantity, book["stock"]))  # clamp on the server

    cart = get_cart()
    if quantity == 0:
        cart.pop(sku, None)
        flash(f"Removed “{book['title']}”.", "info")
    else:
        cart[sku] = quantity
        flash(f"Updated “{book['title']}” to {quantity}.", "success")

    save_cart(cart)
    return redirect(url_for("view_cart"), code=303)


@app.route("/cart")
def view_cart() -> str:
    """Show the cart with live prices.

    Returns:
        str: Rendered ``cart.html``.
    """
    return render_template("cart.html", lines=cart_lines(), total=cart_total())


@app.route("/cart/clear", methods=["POST"])
def clear_cart() -> Response:
    """Empty the cart.

    Returns:
        Response: 303 redirect to the catalogue.

    Note:
        ``session.pop`` removes one key. ``session.clear()`` would also wipe the
        theme preference and, in a real app, the logged-in user — which is
        exactly what you want on **logout**, and not what you want here.
    """
    session.pop(CART_KEY, None)
    flash("Cart emptied.", "info")
    return redirect(url_for("catalogue"), code=303)


@app.route("/remember", methods=["POST"])
def toggle_remember() -> Response:
    """Switch between a browser session and a 7-day permanent session.

    - ``session.permanent = False`` (default): the cookie has **no expiry**, so
      the browser deletes it when it closes.
    - ``session.permanent = True``: the cookie gets an expiry of
      ``PERMANENT_SESSION_LIFETIME`` and survives a restart.

    The lifetime is a *rolling* window — Flask refreshes the expiry on each
    request, so an active user is not logged out mid-session.

    Returns:
        Response: 303 redirect back to the cart.
    """
    session.permanent = not session.permanent
    flash(
        "Cart will be remembered for 7 days." if session.permanent
        else "Cart will be forgotten when you close the browser.",
        "info",
    )
    return redirect(url_for("view_cart"), code=303)


@app.route("/theme/<theme>", methods=["POST"])
def set_theme(theme: str) -> Response:
    """Store a display preference in a **plain cookie**, not the session.

    When to use a raw cookie instead of the session:

    - the value is not sensitive and does not need signing;
    - you want it readable by JavaScript (so ``HttpOnly=False``);
    - you do not want it inflating every session payload.

    A theme choice is all three. A user id is none of them.

    Args:
        theme: ``"dark"`` or ``"light"``; anything else falls back to dark.

    Returns:
        Response: 303 redirect with a ``Set-Cookie`` header attached.

    Note:
        To set a cookie you need a *response object*, which is why this uses
        :func:`~flask.make_response` around the redirect rather than returning
        the redirect directly.
    """
    theme = theme if theme in {"dark", "light"} else "dark"
    response = make_response(redirect(request.referrer or url_for("catalogue"), code=303))
    response.set_cookie(
        "theme", theme,
        max_age=60 * 60 * 24 * 365,  # one year, in seconds
        httponly=False,   # this one IS meant to be readable by JS
        secure=False,     # True in production (HTTPS only)
        samesite="Lax",
    )
    return response


@app.route("/session-debug")
def session_debug() -> str:
    """Show what is actually inside the session and the cookie jar.

    Open this page, then compare it with the raw ``Cookie:`` header your browser
    sends (DevTools → Application → Cookies). The README shows how to decode the
    cookie on the command line and read these very values back out.

    Returns:
        str: Rendered ``session_debug.html``.
    """
    return render_template(
        "session_debug.html",
        session_items=dict(session),
        cookies=request.cookies.to_dict(),
        permanent=session.permanent,
        lifetime=app.config["PERMANENT_SESSION_LIFETIME"],
    )


@app.context_processor
def inject_globals() -> dict[str, Any]:
    """Expose cart size and theme to every template.

    Putting the cart count here means the header badge works on every page
    without each view remembering to pass it.

    Returns:
        dict[str, Any]: Template globals.
    """
    return {
        "company": "Reinforcement Books",
        "cart_count": sum(get_cart().values()),
        "theme": request.cookies.get("theme", "dark"),
    }


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5006, debug=True)

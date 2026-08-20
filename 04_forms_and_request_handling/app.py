"""
Day 04 — Forms and Request Handling: reading input without trusting it.
=======================================================================

Real-world scenario
-------------------
A "Request a demo" lead-capture form — the highest-value page on most B2B
sites. It must accept a POST, validate every field on the **server**, re-display
the form with errors *and the user's typed values intact* when validation
fails, and redirect on success so a browser refresh cannot submit twice.

Today we do all of this **by hand, with no form library**. Day 05 replaces it
with Flask-WTF. You need to see the manual version first, otherwise Flask-WTF
is just magic you cannot debug.

What you will learn
-------------------
1. The :data:`flask.request` object: ``method``, ``form``, ``args``, ``json``,
   ``headers``, and why ``.get()`` beats ``[]``.
2. ``GET`` vs ``POST`` semantics — safe/idempotent versus state-changing.
3. The **POST/Redirect/GET** pattern and the double-submit bug it prevents.
4. **Server-side validation**: client-side checks are UX, never security.
5. Re-rendering a form with errors *and* preserved input ("sticky" fields).
6. **CSRF** — what the attack actually is, and a hand-rolled token to prove it.
7. A **honeypot** field for cheap bot filtering.
8. **Content negotiation**: the same endpoint serving HTML or JSON.

How to run
----------
From the repository root::

    source .venv/bin/activate
    flask --app 04_forms_and_request_handling/app.py run --port 5004 --debug

The one rule to carry forward
-----------------------------
**Never trust the client.** Every ``required``, ``maxlength`` and ``type=email``
in your HTML is a hint to a cooperative browser. ``curl`` ignores all of them.
If a rule is not enforced in Python, it is not enforced.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Any, Final, TypedDict

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.wrappers import Response

app = Flask(__name__)

# -----------------------------------------------------------------------------
# SECRET_KEY
# -----------------------------------------------------------------------------
# Flask signs the session cookie with this key. Without it, `session` and
# `flash()` raise RuntimeError. The signature stops a user editing their own
# cookie: they can *read* it, but they cannot forge a new one.
#
# `secrets.token_hex(32)` is fine for a demo but regenerates on every restart,
# which logs everyone out. Day 18 loads a stable key from the environment.
# NEVER hardcode a real key in source control.
app.config["SECRET_KEY"] = secrets.token_hex(32)

# Maximum accepted request body. Werkzeug rejects anything larger with a 413
# before your view runs, so a malicious 4 GB upload cannot exhaust your RAM.
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024  # 64 KB is plenty for a text form


# -----------------------------------------------------------------------------
# "Database"
# -----------------------------------------------------------------------------
class Lead(TypedDict):
    """One submitted demo request.

    Attributes:
        name: Contact's full name.
        email: Contact email, validated with a pragmatic regex.
        company: Organisation name.
        team_size: Bucketed head-count, validated against a fixed allow-list.
        message: Free-text notes.
        submitted_at: UTC timestamp of acceptance.
    """

    name: str
    email: str
    company: str
    team_size: str
    message: str
    submitted_at: datetime


LEADS: list[Lead] = []

# An ALLOW-LIST for the <select>. A user can send any value they like — the
# browser dropdown is not a constraint — so the server decides what is legal.
TEAM_SIZES: Final[tuple[str, ...]] = ("1-5", "6-20", "21-100", "100+")

# Pragmatic, deliberately not RFC 5322. The only way to truly validate an email
# is to send one and see if it arrives; anything stricter rejects real users.
EMAIL_RE: Final[re.Pattern[str]] = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


# -----------------------------------------------------------------------------
# CSRF protection (hand-rolled, to show the mechanism)
# -----------------------------------------------------------------------------
def issue_csrf_token() -> str:
    """Return this session's CSRF token, generating one on first use.

    **The attack:** you are logged into ``bank.example`` and visit
    ``evil.example``. Its page auto-submits a hidden form to
    ``bank.example/transfer``. Your browser helpfully attaches your session
    cookie, and the transfer succeeds. The attacker never read your cookie —
    they simply made *your browser* act.

    **The defence:** put an unguessable token in the session *and* in a hidden
    form field. ``evil.example`` cannot read your session (same-origin policy),
    so it cannot produce a matching field.

    Returns:
        str: A URL-safe random token, stable for the life of the session.
    """
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return str(session["csrf_token"])


def csrf_is_valid(submitted: str | None) -> bool:
    """Constant-time comparison of a submitted CSRF token against the session's.

    Args:
        submitted: Value of the hidden ``csrf_token`` form field, if present.

    Returns:
        bool: True only when a session token exists and matches.

    Note:
        :func:`secrets.compare_digest` avoids leaking information through
        timing. ``a == b`` on secrets returns early at the first differing byte,
        which is measurable over many requests. Use it for **every** secret
        comparison: tokens, API keys, password hashes, signatures.
    """
    expected = session.get("csrf_token")
    if not expected or not submitted:
        return False
    return secrets.compare_digest(str(expected), submitted)


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------
def validate_lead(form: dict[str, str]) -> dict[str, str]:
    """Validate submitted form data and return one error message per bad field.

    Returning a ``{field: message}`` dict — rather than raising on the first
    problem — lets the template show **every** error at once. Making a user fix
    one mistake per round-trip is a good way to lose the lead.

    Args:
        form: Raw string values keyed by field name, already stripped.

    Returns:
        dict[str, str]: Field name → human-readable error. Empty means valid.

    Best practice:
        Validation returns data *about* the input; it never writes to the
        database, sends email, or touches ``session``. Keeping it side-effect
        free is what makes it trivially unit-testable — see Day 17.
    """
    errors: dict[str, str] = {}

    name = form.get("name", "")
    if not name:
        errors["name"] = "Please tell us your name."
    elif len(name) > 80:
        errors["name"] = "Name must be 80 characters or fewer."

    email = form.get("email", "")
    if not email:
        errors["email"] = "We need an email to reply to."
    elif not EMAIL_RE.match(email):
        errors["email"] = "That does not look like a valid email address."

    company = form.get("company", "")
    if not company:
        errors["company"] = "Which organisation are you with?"

    # Allow-list check. Never `if value not in BLOCKED` — enumerate what is
    # permitted, because you cannot enumerate everything that is not.
    team_size = form.get("team_size", "")
    if team_size not in TEAM_SIZES:
        errors["team_size"] = "Choose one of the listed team sizes."

    message = form.get("message", "")
    if len(message) > 1000:
        errors["message"] = "Please keep it under 1000 characters."

    return errors


# -----------------------------------------------------------------------------
# Views
# -----------------------------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def request_demo() -> str | Response:
    """Show the demo-request form and handle its submission.

    One endpoint handles both verbs, which keeps the URL stable and the logic
    together:

    - **GET** renders an empty form. GET is *safe*: it must never change state,
      because browsers, crawlers and prefetchers issue GETs freely.
    - **POST** validates. On failure it re-renders (status **422**) with errors
      and the user's values. On success it redirects (**POST/Redirect/GET**).

    Why redirect after a successful POST? If you render HTML directly in the
    POST response, the browser's address bar still points at a POST. Pressing
    F5 re-submits the form and creates a duplicate lead — and the browser's
    "Confirm resubmission?" dialog is the user's only warning. Redirecting
    leaves the browser on a harmless GET.

    Returns:
        str | Response: Rendered HTML (200 or 422), or a 303 redirect on success.
    """
    if request.method == "GET":
        return render_template(
            "form.html", data={}, errors={}, csrf_token=issue_csrf_token(),
            team_sizes=TEAM_SIZES,
        )

    # ---- POST from here down -------------------------------------------------
    # `request.form` is a MultiDict of the submitted body. `.get(key, "")` never
    # raises; `request.form["key"]` aborts with 400 when the field is missing —
    # which is exactly what a malicious client will do to probe your app.
    data = {
        "name": request.form.get("name", "").strip(),
        "email": request.form.get("email", "").strip().lower(),
        "company": request.form.get("company", "").strip(),
        "team_size": request.form.get("team_size", "").strip(),
        "message": request.form.get("message", "").strip(),
    }

    # 1. CSRF first — reject forged requests before doing any work.
    if not csrf_is_valid(request.form.get("csrf_token")):
        flash("Your session expired. Please submit the form again.", "error")
        return render_template(
            "form.html", data=data, errors={}, csrf_token=issue_csrf_token(),
            team_sizes=TEAM_SIZES,
        ), 400

    # 2. Honeypot. A field hidden with CSS that humans never see and never fill.
    #    Naive bots fill every input they find. Respond with a normal-looking
    #    success so the bot has no signal that it was caught.
    if request.form.get("website", ""):
        app.logger.info("Honeypot triggered from %s", request.remote_addr)
        return redirect(url_for("thank_you"), code=303)

    # 3. Field validation.
    errors = validate_lead(data)
    if errors:
        # 422 Unprocessable Content: the request was well-formed but the
        # contents failed validation. Returning 200 here would tell API clients
        # and tests that everything succeeded.
        return render_template(
            "form.html", data=data, errors=errors,
            csrf_token=issue_csrf_token(), team_sizes=TEAM_SIZES,
        ), 422

    # 4. Accept.
    LEADS.append({**data, "submitted_at": datetime.now(timezone.utc)})  # type: ignore[arg-type]
    flash(f"Thanks {data['name']} — we'll be in touch at {data['email']}.", "success")

    # 303 See Other is the precise status for "your POST worked, now GET this".
    # Werkzeug's default is 302, which some old clients re-issue as POST.
    return redirect(url_for("thank_you"), code=303)


@app.route("/thanks")
def thank_you() -> str:
    """Confirmation page shown after a successful submission.

    Safe to refresh, safe to bookmark, safe to share — which is the whole point
    of redirecting here instead of rendering from the POST.

    Returns:
        str: Rendered ``thanks.html``.
    """
    return render_template("thanks.html", lead_count=len(LEADS))


@app.route("/leads")
def list_leads() -> str | Response:
    """List captured leads as HTML **or** JSON, chosen by content negotiation.

    The same URL serves both representations. Which one you get depends on the
    ``Accept`` header your client sends — browsers ask for HTML, ``curl -H
    "Accept: application/json"`` asks for JSON.

    ``request.accept_mimetypes.best_match([...])`` does the negotiation for you,
    honouring the quality values (``q=``) in the header.

    Returns:
        str | Response: Rendered table, or a JSON array of leads.

    Warning:
        In a real app this endpoint would leak every lead's contact details to
        the internet. It is unauthenticated here only because auth is Day 13.
    """
    wants_json = request.accept_mimetypes.best_match(
        ["application/json", "text/html"]
    ) == "application/json"

    if wants_json:
        return jsonify([
            {**lead, "submitted_at": lead["submitted_at"].isoformat()}
            for lead in LEADS
        ])
    return render_template("leads.html", leads=LEADS)


@app.route("/inspect", methods=["GET", "POST"])
def inspect_request() -> Response:
    """Echo back everything Flask parsed out of your request.

    Point ``curl`` at this endpoint with different bodies and headers to build
    an accurate mental model of where each piece of data lands:

    ==========================  ==========================================
    ``request.args``            query string (``?q=1``)
    ``request.form``            form body (``application/x-www-form-urlencoded``
                                or ``multipart/form-data``)
    ``request.get_json()``      body when ``Content-Type: application/json``
    ``request.files``           uploaded files (Day 16)
    ``request.headers``         HTTP headers
    ``request.cookies``         cookies (Day 06)
    ==========================  ==========================================

    The classic beginner bug: POSTing JSON and then reading ``request.form``,
    which is empty. They are different parsers for different content types.

    Returns:
        Response: A JSON summary of the parsed request.
    """
    return jsonify({
        "method": request.method,
        "path": request.path,
        "args": request.args.to_dict(flat=False),
        "form": request.form.to_dict(flat=False),
        # silent=True returns None instead of raising when the body is not JSON.
        "json": request.get_json(silent=True),
        "content_type": request.content_type,
        "is_json": request.is_json,
        "user_agent": request.headers.get("User-Agent"),
        "remote_addr": request.remote_addr,
    })


@app.errorhandler(413)
def payload_too_large(error: Exception) -> tuple[Response, int]:
    """Return a clean 413 when the body exceeds ``MAX_CONTENT_LENGTH``.

    Args:
        error: The ``RequestEntityTooLarge`` exception Werkzeug raised.

    Returns:
        tuple[Response, int]: JSON error body and the 413 status code.
    """
    return jsonify(error="Request body too large.", limit_bytes=64 * 1024), 413


@app.context_processor
def inject_globals() -> dict[str, Any]:
    """Expose the company name to every template (see Day 03).

    Returns:
        dict[str, Any]: Template globals.
    """
    return {"company": "Reinforcement Analytics"}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5004, debug=True)

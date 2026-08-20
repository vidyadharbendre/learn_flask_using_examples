"""
Day 02 — Routing and Templates: turning URLs into Python, and Python into HTML.
===============================================================================

Real-world scenario
-------------------
A small company "team directory". Visitors browse a staff list, click through
to an individual profile at ``/team/<employee_id>``, and filter by department
at ``/departments/<name>``. Those three URL shapes — static, dynamic-with-a-type,
and dynamic-with-a-string — cover the vast majority of routing you will ever
write.

What you will learn
-------------------
1. **Static routes** (``/about``) versus **dynamic routes** (``/team/<int:id>``).
2. **URL converters** (``int``, ``string``, ``float``, ``path``, ``uuid``) and
   why letting Flask do the type conversion beats calling ``int()`` yourself.
3. ``url_for()`` — building URLs from endpoint *names* so that changing a path
   never means grepping your templates.
4. **Template inheritance**: one ``base.html`` skeleton, many child pages.
5. Returning a proper **404** with ``abort()`` when a record does not exist.
6. Handling **multiple HTTP methods** on one route.

How to run
----------
From the repository root::

    source .venv/bin/activate
    flask --app 02_routing_and_templates/app.py run --port 5002 --debug

Key insight
-----------
A URL is an *interface*. ``url_for('employee_detail', employee_id=3)`` says
"give me the address of that view" rather than hardcoding ``/team/3``. When you
later restructure to ``/staff/3``, every link in every template updates itself.
Hardcoded paths are the single biggest source of broken links in Flask apps.
"""

from __future__ import annotations

from typing import Final, TypedDict

from flask import Flask, abort, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

app = Flask(__name__)


# -----------------------------------------------------------------------------
# Stand-in "database"
# -----------------------------------------------------------------------------
# A module-level dict is fine for a routing lesson, but understand its limits:
# it lives in one process's memory, so it is lost on restart and is NOT shared
# between gunicorn workers in production. Day 08 replaces this with SQLAlchemy.
class Employee(TypedDict):
    """A single directory entry.

    Using ``TypedDict`` rather than a bare ``dict`` gives you editor
    autocompletion and lets ``mypy`` catch a typo such as ``emp["nmae"]``
    before you ever run the app.

    Attributes:
        id: Stable numeric identifier used in the URL.
        name: Full display name.
        role: Job title shown on the profile page.
        department: Lower-case department slug used for filtering.
        email: Contact address.
    """

    id: int
    name: str
    role: str
    department: str
    email: str


EMPLOYEES: Final[dict[int, Employee]] = {
    1: {"id": 1, "name": "Ananya Rao", "role": "Data Engineer",
        "department": "engineering", "email": "ananya@example.com"},
    2: {"id": 2, "name": "Vikram Shah", "role": "ML Researcher",
        "department": "research", "email": "vikram@example.com"},
    3: {"id": 3, "name": "Meera Iyer", "role": "Product Designer",
        "department": "design", "email": "meera@example.com"},
    4: {"id": 4, "name": "Rohan Gupta", "role": "Backend Engineer",
        "department": "engineering", "email": "rohan@example.com"},
}


@app.route("/")
def home() -> str:
    """Render the directory landing page with the full staff list.

    Returns:
        str: Rendered ``home.html`` containing every employee.
    """
    return render_template("home.html", employees=list(EMPLOYEES.values()))


@app.route("/about")
def about() -> str:
    """Render a static informational page.

    A *static route* has no variable parts: the URL rule ``/about`` matches one
    and only one path.

    Returns:
        str: Rendered ``about.html``.
    """
    return render_template("about.html")


@app.route("/team/<int:employee_id>")
def employee_detail(employee_id: int) -> str:
    """Show one employee's profile page.

    The ``<int:employee_id>`` part of the rule is a *URL converter*. It does
    three jobs at once:

    1. It only matches digits, so ``/team/abc`` returns 404 without your code
       running at all.
    2. It converts the captured text to a real ``int`` before calling you —
       note the parameter is typed ``int``, not ``str``.
    3. It lets ``url_for('employee_detail', employee_id=3)`` rebuild the path.

    Args:
        employee_id: Primary key captured from the URL, already an ``int``.

    Returns:
        str: Rendered ``employee.html`` for the matching employee.

    Raises:
        werkzeug.exceptions.NotFound: via :func:`abort` when no employee has
            that id. Returning a 404 for a missing *record* — not just a
            missing *route* — is what separates a toy app from a real one.
    """
    employee = EMPLOYEES.get(employee_id)
    if employee is None:
        # abort() raises an HTTPException that Flask converts into a proper
        # 404 response. Never return a 200 with the text "not found": clients,
        # crawlers and caches all key off the status code.
        abort(404, description=f"No employee with id {employee_id}.")
    return render_template("employee.html", employee=employee)


@app.route("/departments/<department>")
def department_list(department: str) -> str:
    """List everyone in a department.

    With no converter prefix, ``<department>`` defaults to the ``string``
    converter: it matches any text *except* a forward slash. Use ``<path:...>``
    when you genuinely need slashes (for example a file path).

    Args:
        department: Department slug captured from the URL, e.g. ``engineering``.

    Returns:
        str: Rendered ``department.html``. An empty department renders an
        empty-state message rather than a 404, because "a real department with
        nobody in it" is a valid answer to the question asked.
    """
    slug = department.lower()
    members = [e for e in EMPLOYEES.values() if e["department"] == slug]
    return render_template("department.html", department=slug, employees=members)


@app.route("/search")
def search() -> str:
    """Filter the directory using a **query string** rather than a path segment.

    Path parameters (``/team/3``) identify a *resource*. Query parameters
    (``/search?q=ananya``) *modify a collection*: filtering, sorting, paging.
    Choosing the right one is an API design decision, not a style preference.

    ``request.args`` is Werkzeug's immutable ``MultiDict`` of query parameters.
    ``.get()`` with a default is the safe accessor — indexing with ``[]`` raises
    a 400 error when the key is absent.

    Returns:
        str: Rendered ``search.html`` with the matching subset.
    """
    query = request.args.get("q", default="", type=str).strip().lower()
    if query:
        results = [e for e in EMPLOYEES.values() if query in e["name"].lower()]
    else:
        results = []
    return render_template("search.html", query=query, results=results)


@app.route("/staff/<int:employee_id>")
def legacy_staff_redirect(employee_id: int) -> Response:
    """Permanently redirect a retired URL to its current location.

    When you rename a route you do not delete the old one — you redirect it, or
    every existing bookmark and search-engine result breaks. A ``301`` tells
    clients and crawlers to update their records permanently.

    Args:
        employee_id: Identifier forwarded to the new endpoint.

    Returns:
        Response: A 301 redirect to :func:`employee_detail`. Note the target is
        built with ``url_for`` — the redirect keeps working even if the new path
        changes again.
    """
    return redirect(url_for("employee_detail", employee_id=employee_id), code=301)


@app.errorhandler(404)
def page_not_found(error: Exception) -> tuple[str, int]:
    """Render a friendly 404 page instead of Werkzeug's plain-text default.

    Registering error handlers keeps your branding consistent even when things
    go wrong, and stops internal details leaking to users.

    Args:
        error: The ``NotFound`` exception Flask caught.

    Returns:
        tuple[str, int]: Rendered HTML plus the explicit status code. Returning
        a ``(body, status)`` tuple is Flask's shorthand for setting a status
        without constructing a Response object.
    """
    return render_template("404.html", error=error), 404


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5002, debug=True)

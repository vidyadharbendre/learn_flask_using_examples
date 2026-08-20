"""
Day 01 — Hello, Flask: your first WSGI application.
===================================================

Real-world scenario
-------------------
Every service you will ever deploy needs two things on day one: a page a human
can look at, and a machine-readable health endpoint that your load balancer,
Kubernetes probe, or uptime monitor can poll. This example builds exactly that
— the smallest thing that is still shaped like a real service.

What you will learn
-------------------
1. What a Flask *application object* is and why ``__name__`` is passed to it.
2. How ``@app.route`` maps a URL path to a Python function (a "view function").
3. The difference between returning HTML (``render_template``) and returning
   JSON (``jsonify``).
4. Why ``debug=True`` is a development-only convenience and a production
   security hole.
5. The ``flask run`` CLI, which is the idiomatic way to start a dev server.

How to run
----------
From the repository root::

    source .venv/bin/activate
    flask --app 01_hello_world/app.py run --port 5001 --debug

Then open http://127.0.0.1:5001/ and http://127.0.0.1:5001/health

Mental model
------------
Flask is a *WSGI* application. WSGI is a Python standard that says: "a web
application is a callable that receives a request environment and returns a
response". Flask's job is to make that callable pleasant to write. When a
request arrives, Flask matches the URL against its routing table, calls the
matching view function, and converts whatever you return into an HTTP response.
"""

from __future__ import annotations

from flask import Flask, jsonify, render_template
from flask.wrappers import Response

# -----------------------------------------------------------------------------
# The application object
# -----------------------------------------------------------------------------
# ``Flask(__name__)`` creates the central registry for your app: routes, config,
# error handlers, extensions, and the Jinja environment all hang off it.
#
# Why ``__name__``?  Flask uses it to locate the folder this module lives in so
# it can find ``templates/`` and ``static/`` next to it. Pass a wrong value and
# ``render_template`` will raise TemplateNotFound. This is the single most
# common "it worked on my machine" bug for beginners.
app = Flask(__name__)


@app.route("/")
def home() -> str:
    """Render the human-facing landing page.

    The decorator registers the URL rule ``/`` for the HTTP GET method (GET is
    the implicit default) and binds it to this function. The function name
    ``home`` also becomes the *endpoint name*, which is what ``url_for('home')``
    resolves against — see Day 02.

    Returns:
        str: The rendered HTML of ``templates/index.html``. Flask wraps a
        returned string in a ``200 OK`` response with a ``text/html`` content
        type, so you rarely need to build a Response object by hand.

    Note:
        ``render_template`` looks in the ``templates/`` directory that sits
        beside this file. Jinja2 autoescapes ``.html`` templates, which is your
        first and best line of defence against XSS.
    """
    return render_template("index.html", framework="Flask", day=1)


@app.route("/health")
def health() -> Response:
    """Report service liveness in a machine-readable format.

    This is the endpoint your monitoring stack polls. Real deployments keep it
    cheap and dependency-free: it must answer even when the database is down,
    otherwise a transient DB blip will cause your orchestrator to kill an
    otherwise healthy container.

    Returns:
        Response: A JSON body such as ``{"status": "ok", "service": "day-01"}``
        with content type ``application/json``.

    Best practice:
        Return a *stable contract*. Monitors are written against these keys, so
        renaming ``status`` later is a breaking change for your ops tooling.
    """
    # jsonify() is preferred over json.dumps(): it sets the correct Content-Type
    # header, uses Flask's configured JSON encoder, and handles unicode safely.
    return jsonify(status="ok", service="day-01", version="1.0.0")


if __name__ == "__main__":
    # ---------------------------------------------------------------------
    # The ``if __name__ == "__main__"`` guard
    # ---------------------------------------------------------------------
    # This block only runs when you execute `python app.py` directly. It does
    # NOT run under gunicorn (Day 20), which imports the module and grabs the
    # ``app`` object instead. Keeping start-up logic here rather than at module
    # level is what makes the same file work in dev and in production.
    #
    # WARNING: ``debug=True`` enables the interactive Werkzeug debugger, which
    # allows arbitrary code execution through the browser. It is a development
    # tool only. Never ship it. Never bind ``host="0.0.0.0"`` with debug on.
    app.run(host="127.0.0.1", port=5001, debug=True)

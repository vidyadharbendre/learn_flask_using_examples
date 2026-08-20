# Day 01 — Hello, Flask

> **Goal:** get a real WSGI service running, understand what the application object
> actually is, and learn why `debug=True` never leaves your laptop.
> **Time:** ~45 minutes · **Port:** 5001

---

## 1. Why this matters

Most tutorials show you `return "Hello World"` and move on. That teaches you a
string, not a service. Every real Flask app you deploy has the same two faces:

| Face | Consumer | This example |
|---|---|---|
| **HTML** | a human in a browser | `/` renders `templates/index.html` |
| **JSON** | a load balancer, k8s probe, uptime monitor | `/health` returns `{"status": "ok"}` |

Starting with both means the shape of your first app already matches the shape
of your hundredth.

## 2. What you will build

A minimal service with a landing page and a health endpoint:

```
01_hello_world/
├── app.py                 # application object + two view functions
├── templates/
│   └── index.html         # Jinja2 template rendered by the / route
├── static/
│   └── style.css          # served automatically at /static/style.css
└── README.md              # you are here
```

## 3. Setup (do this once for the whole 21 days)

From the **repository root**:

```bash
python3 -m venv .venv
source .venv/bin/activate         # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Verify:

```bash
python -c "import flask; print('Flask ready')"
```

## 4. Run it

The idiomatic way — use the `flask` CLI, not `python app.py`:

```bash
flask --app 01_hello_world/app.py run --port 5001 --debug
```

Open <http://127.0.0.1:5001/> and <http://127.0.0.1:5001/health>.

Stop the server with `Ctrl+C`.

> **Why the CLI over `python app.py`?** The CLI gives you `--debug`,
> `--reload`, and `flask routes` for free, and it keeps start-up configuration
> out of your source file. Both work; the CLI is what teams use.

## 5. Try it — learn by doing

Run each of these and *predict the answer before you press Enter*.

```bash
# 1. The HTML page, as a browser would fetch it
curl -i http://127.0.0.1:5001/

# 2. The JSON health check — note the Content-Type header
curl -i http://127.0.0.1:5001/health

# 3. A URL that does not exist — Flask returns 404 automatically
curl -i http://127.0.0.1:5001/nope

# 4. A method the route does not allow — note 405, not 404
curl -i -X POST http://127.0.0.1:5001/

# 5. List every route Flask knows about
flask --app 01_hello_world/app.py routes
```

**Experiments that teach more than reading:**

1. Change `<h1>Hello, {{ framework }}!</h1>` in `templates/index.html` and refresh.
   The reloader picks it up with no restart.
2. In `app.py`, change `framework="Flask"` to `framework="<b>Flask</b>"` and
   refresh. You will see the literal tags, **not** bold text — that is Jinja2
   autoescaping protecting you from XSS.
3. Delete `templates/` temporarily and reload. Read the `TemplateNotFound`
   traceback carefully; it is the error you will hit most often as a beginner.

## 6. Code walkthrough

**The application object**

```python
app = Flask(__name__)
```

`__name__` tells Flask which module it lives in, so it can resolve
`templates/` and `static/` relative to this file. Everything else — routes,
config, extensions, error handlers — is registered onto this object.

**A view function**

```python
@app.route("/")
def home() -> str:
    return render_template("index.html", framework="Flask", day=1)
```

- `@app.route("/")` adds a *URL rule* to the routing table.
- `home` becomes the *endpoint name*, which `url_for("home")` resolves.
- Returning a `str` makes Flask build a `200 OK` / `text/html` response for you.
- Keyword arguments to `render_template` become variables inside the template.

**Returning JSON**

```python
return jsonify(status="ok", service="day-01", version="1.0.0")
```

Use `jsonify`, not `json.dumps`. It sets `Content-Type: application/json`,
uses Flask's configured encoder, and handles unicode correctly.

## 7. Best practices introduced today

| Practice | Reason |
|---|---|
| Type-annotate view functions (`-> str`, `-> Response`) | catches bugs before runtime; `mypy --strict` passes on this repo |
| Module + function docstrings in `"""triple quotes"""` | the code explains itself six months later |
| `url_for('static', filename=...)` instead of `/static/...` | survives being mounted under a sub-path or CDN |
| Keep `app.run()` inside `if __name__ == "__main__"` | lets gunicorn import the module without starting a server |
| Ship a `/health` endpoint from day one | your orchestrator needs it, and adding it later is always a rush job |
| Pin dependency versions | reproducible installs on every machine |

## 8. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `TemplateNotFound: index.html` | template not in `templates/` beside `app.py` | move the file; check spelling |
| `Address already in use` | port 5001 busy (on macOS, port **5000** is AirPlay) | use another `--port` |
| Edits don't show up | reloader off | add `--debug` |
| `debug=True` in production | Werkzeug debugger = remote code execution | drive it from an env var (Day 18) |
| `jsonify` returns wrong content type | you used `json.dumps` instead | use `jsonify` |
| `TemplateSyntaxError` from an HTML comment | `<!-- -->` is **not** a Jinja comment — Jinja still parses `{{ }}` inside it | use `{# … #}`, or `{% raw %}` to show syntax literally |
| Comment text leaks into the page | **Jinja comments do not nest** — the first `#}` closes the whole block | never open a comment inside a comment |

## 8b. Two comment traps (both hit while writing this file)

```jinja
<!-- The {{ variable }} syntax is Jinja -->     ❌ Jinja STILL parses this
{# The {{ variable }} syntax is Jinja #}        ✅ stripped before rendering
```

An HTML comment reaches the browser **and** is parsed by Jinja on the way. A
malformed example inside one raises `TemplateSyntaxError`; a valid one is
silently evaluated.

```jinja
{# outer … {# inner #} … still outer #}         ❌ comments DO NOT NEST
```

The first `#}` closes the whole block, and everything after it leaks into the
page as visible text.

To display Jinja syntax to a reader, use a raw block:

```jinja
{% raw %}{{ framework }}{% endraw %}
```

Both mistakes were made in `templates/index.html` before it was rendered even
once — which is the real lesson: **run the page.**

## 9. Exercises

1. Add a `/version` route that returns `{"version": "1.0.0"}` as JSON.
2. Make `/health` also return the current UTC time (`datetime.now(timezone.utc).isoformat()`).
3. Pass a list of three feature names into the template and render them as a `<ul>`
   using `{% for %}`. (Peek at Day 03 if you get stuck.)
4. Run `flask --app 01_hello_world/app.py routes` and explain each column to yourself.

## 10. What's next

**[Day 02 — Routing and Templates →](../02_routing_and_templates/)**
Dynamic URLs, typed converters, `url_for`, and template inheritance.

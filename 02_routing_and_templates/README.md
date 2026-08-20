# Day 02 — Routing and Templates

> **Goal:** make URLs do real work — dynamic segments, typed converters,
> `url_for`, template inheritance, and honest 404s.
> **Time:** ~60 minutes · **Port:** 5002 · **Builds on:** Day 01

---

## 1. Why this matters

A URL is a **public interface**. Once someone bookmarks `/team/3` or Google
indexes it, that string is a promise you have made. Two habits protect you:

1. **Never hardcode paths in templates.** Use `url_for('endpoint', **params)`.
   Change `@app.route("/team/<int:employee_id>")` to `/people/<int:employee_id>`
   and every link updates itself.
2. **Redirect retired URLs, never delete them.** `/staff/2` in this example
   301-redirects to `/team/2`.

## 2. What you will build

A team directory with six route shapes, each teaching one idea:

| URL | Endpoint | Teaches |
|---|---|---|
| `/` | `home` | static route |
| `/about` | `about` | static route |
| `/team/<int:employee_id>` | `employee_detail` | typed converter + `abort(404)` |
| `/departments/<department>` | `department_list` | string converter |
| `/search?q=…` | `search` | query strings via `request.args` |
| `/staff/<int:employee_id>` | `legacy_staff_redirect` | 301 redirect |

```
02_routing_and_templates/
├── app.py
├── templates/
│   ├── base.html          # the skeleton every page inherits
│   ├── home.html          # staff list
│   ├── employee.html      # one profile
│   ├── department.html    # filtered list + empty state
│   ├── search.html        # query-string results
│   ├── about.html         # the URL map, documented
│   └── 404.html           # custom error page
└── static/style.css
```

## 3. Run it

```bash
source .venv/bin/activate
flask --app 02_routing_and_templates/app.py run --port 5002 --debug
```

Open <http://127.0.0.1:5002/>.

## 4. Try it — learn by doing

```bash
# Typed converter: only digits match the rule
curl -i http://127.0.0.1:5002/team/1        # 200 — Ananya Rao
curl -i http://127.0.0.1:5002/team/99       # 404 — valid URL, missing record
curl -i http://127.0.0.1:5002/team/abc      # 404 — never even calls your view

# String converter
curl    http://127.0.0.1:5002/departments/engineering
curl    http://127.0.0.1:5002/departments/nosuch      # 200 with an empty state

# Query string
curl    "http://127.0.0.1:5002/search?q=an"

# Redirect: -i shows the 301, -L follows it
curl -i http://127.0.0.1:5002/staff/2
curl -iL http://127.0.0.1:5002/staff/2

# Inspect the whole routing table
flask --app 02_routing_and_templates/app.py routes
```

**Understand the two kinds of 404.** `/team/abc` fails at *routing* — the `int`
converter rejects it, so `employee_detail` never runs. `/team/99` fails at
*lookup* — your code runs, finds nothing, and calls `abort(404)`. Both return
404 to the client, but only one is your responsibility to write.

**Experiment with `url_for`** in a shell:

```bash
python - <<'PY'
import sys; sys.path.insert(0, '02_routing_and_templates')
from app import app
with app.test_request_context():
    from flask import url_for
    print(url_for('employee_detail', employee_id=3))   # /team/3
    print(url_for('search', q='meera'))                # /search?q=meera
    print(url_for('employee_detail', employee_id=3, _external=True))
PY
```

Note the third line: unknown keyword arguments become **query parameters**
automatically. That is why `url_for('search', q='meera')` works without `q`
being part of the route rule.

## 5. URL converters reference

| Converter | Matches | Example rule |
|---|---|---|
| *(none)* / `string` | any text without `/` | `/departments/<department>` |
| `int` | digits, converted to `int` | `/team/<int:employee_id>` |
| `float` | decimal numbers | `/price/<float:amount>` |
| `path` | text **including** `/` | `/files/<path:filepath>` |
| `uuid` | a UUID string | `/orders/<uuid:order_id>` |

Prefer a converter over validating inside the view: bad input is rejected at the
routing layer, which is earlier, cheaper, and impossible to forget.

## 6. Template inheritance in one picture

```
base.html                      home.html
┌────────────────────────┐     ┌──────────────────────────────┐
│ <head> … </head>       │     │ {% extends 'base.html' %}    │
│ header + nav           │ ◀── │ {% block title %}…{% endblock %}
│ {% block title %}      │     │ {% block content %}          │
│ {% block content %}    │     │   <ul class="cards">…</ul>   │
│ footer                 │     │ {% endblock %}               │
└────────────────────────┘     └──────────────────────────────┘
```

Rules worth memorising:

- `{% extends %}` must be the **first** tag in a child template.
- Markup outside a `{% block %}` in a child is **discarded**.
- Give blocks default content so a child can skip them.
- `{# … #}` is a Jinja comment (stripped); `<!-- … -->` reaches the browser —
  **and is still parsed by Jinja**, so `{{ }}` inside one is evaluated.
- Jinja comments **do not nest**: the first `#}` closes the whole block.

## 7. Jinja essentials used here

| Syntax | Meaning |
|---|---|
| `{{ value }}` | print, **HTML-escaped** |
| `{% if %}` / `{% elif %}` / `{% else %}` | conditionals |
| `{% for x in xs %}` … `{% else %}` | loop with a built-in empty case |
| `{{ value|title }}`, `{{ xs|length }}` | filters transform before printing |
| `{{ query|default('', true) }}` | fallback when undefined *or* falsy |

## 8. Best practices introduced today

| Practice | Reason |
|---|---|
| `url_for()` everywhere | renaming a route never breaks a link |
| Typed converters over manual `int()` | invalid input rejected before your code runs |
| `abort(404)` for missing records | status codes are an API; clients and crawlers read them |
| Custom `@app.errorhandler(404)` | consistent branding, no internal details leaked |
| `301` redirects for renamed URLs | bookmarks and SEO survive refactors |
| `request.args.get(k, default)` | `request.args[k]` raises 400 on a missing key |
| Empty states instead of blank pages | users can't tell "empty" from "broken" |
| `TypedDict` for record shapes | mypy catches `emp["nmae"]` at check time |

## 9. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `BuildError: Could not build url for endpoint` | passed a *path* to `url_for` instead of an endpoint name | use the function name: `url_for('home')` |
| Links break after renaming a route | hardcoded `href="/team/3"` | `url_for('employee_detail', employee_id=3)` |
| `TypeError: employee_detail() got an unexpected keyword argument` | rule variable name ≠ function parameter name | make them match exactly |
| Child template renders as a blank page | markup sits outside `{% block %}` | move it inside a block |
| `<b>` shows as literal text | Jinja autoescaping (working as intended) | only use `|safe` on content **you** produced |
| 400 on a missing query param | used `request.args['q']` | `request.args.get('q', '')` |

## 10. Exercises

1. Add `/departments` (no argument) listing every distinct department with a
   member count, each linking to its filtered page.
2. Add a `float` converter route `/budget/<float:amount>` that renders the value
   formatted to two decimals.
3. Make `/search` also match role and department, not just name.
4. Add a custom `405` error handler and trigger it with
   `curl -X POST http://127.0.0.1:5002/`.
5. Extract the employee card markup into a Jinja **macro** in
   `templates/_macros.html` and use it from all three list pages. (This is the
   `{% macro %}` / `{% import %}` pair you will meet properly on Day 03.)

## 11. What's next

**[Day 03 — Jinja Templates and Static Files →](../03_jinja_templates_and_static/)**
Macros, filters, `include`, context processors, and organising CSS/JS properly.

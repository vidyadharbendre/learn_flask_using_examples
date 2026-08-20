# Day 10 — Blueprints and the Application Factory

> **Goal:** turn a single `app.py` into a real package — a `create_app()`
> factory, blueprints per area, and one config class per environment.
> **Time:** ~90 minutes · **Port:** 5010 · **Builds on:** Days 08–09

---

## 1. Why this matters

Every day so far started the same way:

```python
app = Flask(__name__)          # created once, at import time
app.config["SECRET_KEY"] = ... # baked in
```

That single global has four real problems:

| Problem | Consequence |
|---|---|
| Configured at import time | you cannot run dev, test and prod from one codebase |
| Only one instance can exist | tests share state and fail depending on order |
| Forces circular imports | the fragile "import models *after* `init_app`" dance |
| One file grows forever | two people cannot edit it without conflicting |

A **factory** fixes all four. Nothing exists until you call it, and you may call
it as often as you like with different settings.

## 2. What you will build

Days 08–09's inventory, restructured as a package:

```
10_blueprints_and_app_factory/
├── wsgi.py                     # 3 lines: call the factory
└── inventory/
    ├── __init__.py             # create_app()  ← the factory
    ├── config.py               # Development / Testing / Production
    ├── extensions.py           # db, migrate, csrf — created bare
    ├── models.py
    ├── forms.py
    ├── commands.py             # CLI, registered by the factory
    ├── blueprints/
    │   ├── main.py             # /            dashboard, health
    │   ├── products.py         # /products    CRUD + pagination
    │   └── api.py              # /api         JSON, with JSON errors
    ├── templates/
    │   ├── base.html  _macros.html
    │   ├── main/  products/  errors/
    └── static/css/style.css
```

## 3. Run it

```bash
source .venv/bin/activate
cd 10_blueprints_and_app_factory

FLASK_APP=wsgi.py flask seed
FLASK_APP=wsgi.py flask run --port 5010 --debug
```

Open <http://127.0.0.1:5010/>.

See what registration actually did:

```bash
FLASK_APP=wsgi.py flask routes-by-blueprint
```

```
[api]
  GET   /api/products                  -> api.list_products
  GET   /api/products/<int:product_id> -> api.get_product
[main]
  GET   /                              -> main.dashboard
[products]
  GET   /products/                     -> products.index
  GET,POST /products/<int:product_id>/edit -> products.edit
```

Every endpoint is `<blueprint>.<view>`, and every rule carries the prefix given
at registration.

## 4. Try it — learn by doing

### The same error, two representations

```bash
curl -s -o /dev/null -w "%{content_type}\n" http://127.0.0.1:5010/products/9999
# text/html      -> renders errors/404.html

curl -s http://127.0.0.1:5010/api/products/9999
# {"error":"Not Found","message":"No product with id 9999.","status":404}
```

One application, two conventions, and **no `if request.path.startswith("/api")`
anywhere**. That is the strongest argument for blueprints.

### Prove the factory's payoff in a Python shell

```bash
cd 10_blueprints_and_app_factory
python - <<'PY'
from inventory import create_app
a, b = create_app("testing"), create_app("testing")
print(a is not b)                              # True — independent apps
print(a.config["SQLALCHEMY_DATABASE_URI"])     # sqlite:///:memory:
print(a.config["WTF_CSRF_ENABLED"])            # False — tests can POST freely

import os; os.environ.pop("SECRET_KEY", None)
try:
    create_app("production")
except RuntimeError as e:
    print("production refuses to boot:", e)
PY
```

Two independent apps in one process is exactly what per-test isolation needs —
and it is impossible with a module-level `app`.

### Other things to try

```bash
curl -s "http://127.0.0.1:5010/api/products?page=2" | python -m json.tool
curl -s http://127.0.0.1:5010/health | python -m json.tool
```

In the browser: search for `ssd`, then page through the results and watch the
filter survive in the URL.

## 5. The factory, step by step

```python
def create_app(config_name=None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    # 1. config
    config_class = config_by_name[config_name or os.environ.get("FLASK_CONFIG", "default")]
    app.config.from_object(config_class)
    config_class.init_app(app)

    # 2. extensions — bind the bare objects from extensions.py to THIS app
    db.init_app(app); migrate.init_app(app, db); csrf.init_app(app)

    # 3. models — imported INSIDE the function, so no circular import
    from . import models

    # 4. blueprints — url_prefix is applied HERE, not in the blueprint
    from .blueprints.products import products_bp
    app.register_blueprint(products_bp, url_prefix="/products")

    # 5. cross-cutting concerns
    _register_error_handlers(app); register_commands(app)
    return app
```

Two details worth calling out:

- **`from . import models` inside the function.** By the time the factory runs,
  the package is fully imported, so there is no partially-initialised module.
  This is the *structural* cure for the circular import — no import-order
  gymnastics needed.
- **`url_prefix` at registration.** The blueprint does not know where it lives.
  Moving a whole section of the site is a one-line change, and the same
  blueprint can be mounted twice.

## 6. Config classes

```python
class Config:                    # shared
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only")

class TestingConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False     # safe BECAUSE it is scoped to tests

class ProductionConfig(Config):
    SESSION_COOKIE_SECURE = True
    @staticmethod
    def init_app(app):
        if not os.environ.get("SECRET_KEY"):
            raise RuntimeError("SECRET_KEY must be set for production.")
```

- `from_object` reads **UPPERCASE attributes only** — lowercase names like
  `init_app` are ignored, which is how a config class carries behaviour without
  polluting `app.config`.
- Config holds *values*; `init_app` is for things that must *run*.
- **Fail loudly at start-up.** A production app on the default dev key means
  every session cookie in the world can be forged, and nothing in the logs would
  say so. Crashing on boot is the kind behaviour.

> Day 18 replaces these classes with `pydantic-settings`, which validates types
> and reports every missing variable at once.

## 7. `url_for` inside blueprints

```jinja
{{ url_for('dashboard') }}                          {# ❌ BuildError #}
{{ url_for('main.dashboard') }}                     {# ✅ #}
{{ url_for('products.detail', product_id=3) }}      {# ✅ #}
{{ url_for('.detail', product_id=3) }}              {# ✅ relative, same blueprint #}
```

Endpoints are namespaced by blueprint name — which is exactly why two blueprints
can each define `index`.

## 8. Template lookup order

1. the **app's** `templates/`
2. each blueprint's `template_folder`, in registration order

So an app-level file **wins** over a blueprint's file of the same name. That is
how you override a third-party blueprint's template without forking it. Namespace
your blueprint templates in sub-folders (`templates/products/index.html`) to
avoid accidental collisions.

## 9. Blueprint-scoped behaviour

| Decorator | Scope |
|---|---|
| `@bp.route` | this blueprint |
| `@bp.errorhandler(...)` | errors raised **inside** this blueprint |
| `@bp.before_request` | requests routed to this blueprint |
| `@bp.app_template_filter` | the **whole app** (filters are global) |
| `@bp.app_errorhandler(...)` | the whole app, registered from a blueprint |

### The error-handler gotcha this example hit

```python
@api_bp.errorhandler(HTTPException)      # ❌ silently loses to the app
```

Flask resolves handlers **by specific status code first, then by exception
class**, checking blueprint then app at each step:

```
for code in (404, None):
    for scope in (blueprint, app):
        ...look...
```

A generic `HTTPException` handler is stored under code `None`, so the app-level
`@app.errorhandler(404)` is found first — and the API returned an HTML page.
The fix is to register the blueprint handler for the specific codes too:

```python
for status in (400, 401, 403, 404, 405, 409, 415, 422, 429, 500):
    api_bp.register_error_handler(status, handle_api_error)
```

**Known limit:** a 404 from *routing* (a URL matching no rule, e.g. `/api/typo`)
belongs to no blueprint — Flask never determined which one you meant — so
`request.blueprint` is `None` and the app-level handler answers. If your API must
return JSON there too, handle 404 at app level and branch on `request.path`.

## 10. CLI commands without a global `app`

```python
@click.command("seed")
@with_appcontext                 # ← pushes an app context so `db` resolves
def seed_command() -> None: ...

def register_commands(app): app.cli.add_command(seed_command)
```

Without `@with_appcontext` you get `RuntimeError: Working outside of application
context` — because `db.session` must know *which* app it belongs to, and with a
factory there is no single global answer.

## 11. Best practices introduced today

| Practice | Reason |
|---|---|
| `create_app()` factory | per-environment config; multiple instances for tests |
| Config classes + `from_object` | one place per environment, no `if app.debug` |
| `ProductionConfig` raises on a missing secret | fail at boot, not silently at runtime |
| Extensions created bare, bound with `init_app` | the same objects serve many apps |
| Import models **inside** the factory | structurally impossible to cycle |
| `url_prefix` at registration | moving a section is one line |
| Blueprint per area of the site | parallel work without merge conflicts |
| Blueprint-scoped error handlers | JSON for the API, HTML for pages |
| Namespaced template folders | avoids silent template collisions |
| `csrf.exempt(api_bp)` for token APIs | CSRF protects cookie auth, not tokens |
| `current_app` instead of a global | works with any instance |
| Thin `wsgi.py` | gunicorn and the CLI share one entry point |
| Thread filters through pagination links | page 2 must keep the search |
| `db.session.rollback()` in the 500 handler | a failed request must not poison the session |
| Generic 500 page | exception text leaks paths and internals |

## 12. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `BuildError: Could not build url for endpoint 'index'` | unqualified endpoint | `url_for('products.index')` |
| `RuntimeError: Working outside of application context` | CLI command or script with no context | `@with_appcontext` / `with app.app_context():` |
| `Working outside of request context` | used `request` outside a request | pass the value in |
| API returns HTML errors | generic blueprint handler loses to a coded app handler | register the specific codes too (§9) |
| Blueprint routes 404 | forgot `app.register_blueprint(...)` | register it in the factory |
| `TemplateNotFound` after restructuring | template folder path wrong | check `template_folder` and sub-folders |
| Wrong template rendered | app-level file shadows the blueprint's | namespace in sub-folders |
| Tests interfere with each other | shared file database | `sqlite:///:memory:` per app |
| Every API POST returns 400 | CSRF on a token API | `csrf.exempt(api_bp)` |
| `ImportError: partially initialized module` | model imported at module top level | import inside the factory |
| Page 2 loses the search | filters not threaded into `url_for` | pass `**filters` |
| Production runs with the dev key | no start-up check | raise in `init_app` |

## 13. Exercises

1. Add an `auth` blueprint at `/auth` with placeholder login/logout pages.
   (Day 13 makes them real.)
2. Add `@products_bp.before_request` logging every request into that blueprint,
   and confirm it does **not** fire for `/` or `/api/...`.
3. Register `products_bp` a second time at `/inventory` with
   `name="inventory_products"`. Note what happens to `url_for`.
4. Add a `StagingConfig` and boot it with `FLASK_CONFIG=staging`.
5. Write `tests/test_products.py` using `create_app("testing")` as a fixture.
   Note you need no CSRF token and no cleanup — Day 17 formalises this.
6. Make `/api/typo` return JSON, using the app-level branch from §9.
7. Move `ITEMS_PER_PAGE` to an env var and page through with `ITEMS_PER_PAGE=2`.

## 14. What's next

**[Day 11 — REST API Fundamentals →](../11_rest_api_fundamentals/)**
The `api` blueprint was a sketch. Now design a real API: resources, status
codes, pagination, consistent errors, and content negotiation.

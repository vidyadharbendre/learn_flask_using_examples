# Learn Flask in 21 Days — by building real things

A hands-on Flask course. **Every day is a runnable application**, not a snippet:
you install once, run one command, open a browser, attack your own app with
`curl`, and read code that explains *why* as well as *what*.

Each day ships:

- a **`README.md`** with setup, run instructions, a "try it" section, best
  practices, a common-mistakes table, and exercises;
- **fully documented code** — module docstrings that frame the problem, function
  docstrings with `Args:` / `Returns:` / `Raises:`, and inline comments on the
  non-obvious decisions;
- **commented templates** explaining the Jinja as you meet it.

---

## Quick start

```bash
git clone git@github.com:vidyadharbendre/learn_flask_using_examples.git
cd learn_flask_using_examples

python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Day 01
flask --app 01_hello_world/app.py run --port 5001 --debug
```

Open <http://127.0.0.1:5001/>. That's it — no database server, no Docker, no
build step needed until the days that teach them.

**Check everything works before you start:**

```bash
./verify.sh                # runs all 21 days and reports pass/fail
./verify.sh --tests        # …and the three pytest suites
./verify.sh 08             # just one day
```

**Requirements:** Python 3.10+ (3.11 recommended). Verified on Python 3.11.7
with Flask 3.0.3.

> **Port convention:** day *N* runs on port `5000 + N`. Day 01 → 5001, Day 21 →
> 5021. Two days can run side by side. (Port 5000 itself is avoided — macOS
> gives it to AirPlay.)

---

## The 21 days

### Week 1 — Foundations: request in, HTML out

| Day | Topic | You build | Key ideas |
|---|---|---|---|
| **[01](01_hello_world/)** | Hello, Flask | landing page + `/health` | app object, view functions, `render_template` vs `jsonify`, why `debug=True` never ships |
| **[02](02_routing_and_templates/)** | Routing & templates | team directory | typed URL converters, `url_for`, query strings, 301s for retired URLs, custom 404 |
| **[03](03_jinja_templates_and_static/)** | Jinja & static files | SaaS pricing page | macros, includes, custom filters, context processors, autoescaping vs XSS |
| **[04](04_forms_and_request_handling/)** | Forms, the hard way | demo-request form | `request` object, POST/Redirect/GET, 422, sticky fields, hand-rolled CSRF, honeypot |
| **[05](05_flask_wtf_and_validation/)** | Flask-WTF | job application portal | form classes, custom + cross-field validators, `CSRFProtect`, the `DataRequired`/`0` trap |
| **[06](06_sessions_cookies_and_flash/)** | Sessions & cookies | shopping cart | signed ≠ encrypted, the mutation trap, `HttpOnly`/`Secure`/`SameSite`, how `flash` works |
| **[07](07_project_expense_tracker/)** | **Week 1 project** | expense tracker | layered modules, money as integer paise, CSV download, `flask` CLI commands |

### Week 2 — Data and structure: making it real

| Day | Topic | You build | Key ideas |
|---|---|---|---|
| **[08](08_database_with_sqlalchemy/)** | SQLAlchemy | inventory manager | models, sessions, CRUD, relationships, transactions, N+1 queries |
| **[09](09_migrations_with_flask_migrate/)** | Migrations | evolving that schema | Alembic, autogenerate, upgrade/downgrade, safe production migrations |
| **[10](10_blueprints_and_app_factory/)** | Blueprints & factory | restructured app | `create_app()`, blueprints, config classes, circular-import cures |
| **[11](11_rest_api_fundamentals/)** | REST APIs | bookstore API | resource design, status codes, JSON errors, pagination, content negotiation |
| **[12](12_pydantic_validation_and_schemas/)** | Pydantic schemas | typed API boundary | request/response models, `model_validate`, OpenAPI-shaped errors |
| **[13](13_authentication_with_flask_login/)** | Authentication | member portal | password hashing, `Flask-Login`, `@login_required`, session fixation |
| **[14](14_project_task_manager/)** | **Week 2 project** | task manager | blueprints + DB + auth + API + tests in one app |

### Week 3 — Production: making it survive

| Day | Topic | You build | Key ideas |
|---|---|---|---|
| **[15](15_jwt_auth_and_rbac/)** | JWT & roles | API auth layer | access/refresh tokens, claims, RBAC decorators, revocation |
| **[16](16_file_uploads_and_media/)** | File uploads | document vault | `secure_filename`, content sniffing, thumbnails, serving files safely |
| **[17](17_testing_with_pytest/)** | Testing | a real test suite | fixtures, the test client, DB isolation, coverage, what not to test |
| **[18](18_config_logging_and_errors/)** | Config & observability | 12-factor Flask | `pydantic-settings`, structured logging, request ids, error handling |
| **[19](19_caching_rate_limiting_and_jobs/)** | Performance | cached dashboard | `Flask-Caching`, `Flask-Limiter`, background work, timeouts and retries |
| **[20](20_docker_and_production_deploy/)** | Deployment | container + gunicorn | Dockerfile, gunicorn workers, reverse proxy, security headers |
| **[21](21_capstone_analytics_dashboard/)** | **Capstone** | full application | everything, assembled and deployable |

---

## Which days need a setup step?

Days 01–07 run with **no setup at all** — just `flask run`. From Day 08 there is
a database, so each day's README section 3 gives you the exact commands. For
reference:

| Days | Before `flask run` | Why |
|---|---|---|
| 01–07 | *nothing* | no database yet |
| **08** | `flask --app 08_.../app.py init-db` then `seed` | `create_all()` builds the tables |
| **09** | `flask db upgrade -d 09_.../migrations` then `seed` | migrations, not `create_all()` |
| **10–15, 17, 21** | `cd <day> && FLASK_APP=wsgi.py flask seed` | `seed` creates tables *and* demo data |
| 16, 18, 19, 20 | *nothing* | no database, or created on first use |

> **If you forget**, the app tells you. Skipping the step no longer produces a
> raw `OperationalError: no such table` — you get a page (or JSON) naming the
> exact command to run. Try it: delete a day's `instance/*.db` and reload.

## How to use this repository

**Do the days in order.** Each one assumes the last. Day 05 only makes sense
because Day 04 made you write CSRF by hand; Day 08 only lands because Day 07 hit
the limits of a JSON file.

For each day:

1. Read the day's `README.md` §1–§3 (why, what, run it).
2. **Run it and click around** before reading the code.
3. Work through the **"Try it — learn by doing"** section. The `curl` commands
   are where the real lessons are — they show your app being attacked the way a
   browser never would.
4. Read `app.py` top to bottom. The module docstring frames the problem.
5. Do at least two **Exercises**. Reading code teaches you less than breaking it.

**Budget:** ~1–2 hours per day. The project days (07, 14, 21) take longer.

---

## Repository conventions

| Convention | Why |
|---|---|
| `NN_topic_name/` folders | ordered, greppable, and each is self-contained |
| Day *N* on port `5000 + N` | run several days side by side |
| `templates/` and `static/` per day | each example is independently runnable |
| `_partial.html` naming | leading underscore = "not a page" |
| Type annotations everywhere | `./typecheck.sh` passes clean across all 21 days |
| Pinned dependency versions | the examples behave identically on every machine |
| Docstrings with `Args:`/`Returns:` | Google style, readable by humans and tooling |

### Verify your setup

```bash
./verify.sh --tests     # every day runs + all three test suites (118 tests)
./typecheck.sh          # type-check every day
```

> Each day ships its own `app.py`, so a single `mypy .` fails with
> `Duplicate module named "app"`. `typecheck.sh` runs the checker per directory,
> which is the correct fix for this layout.
>
> `mypy.ini` deliberately does **not** use `--strict`. Several Flask extensions
> ship no type stubs, so strict mode reports "Untyped decorator makes function X
> untyped" for every view — noise that says nothing about your code. The
> settings keep the checks that catch real bugs and silence the ones that only
> report missing third-party stubs.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Address already in use` | another app is on that port — macOS gives **5000** to AirPlay; use `--port 50NN` |
| `TemplateNotFound` | the template must be in `templates/` beside that day's `app.py` |
| `ModuleNotFoundError: flask` | activate the venv: `source .venv/bin/activate` |
| `RuntimeError: ... secret key` | that day needs `SECRET_KEY`; it defaults for dev — see Day 18 |
| Edits don't appear | add `--debug` to enable the reloader |
| `400 Bad Request` on every POST | missing `{{ form.hidden_tag() }}` (CSRF) — see Day 05 |

---

## Licence

MIT — see [LICENSE](LICENSE).

Built by [Reinforcement Analytics](https://reinforcementanalytics.in/).

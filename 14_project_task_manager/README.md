# Day 14 — Week 2 Project: Task Manager

> **Goal:** assemble Days 08–13 into one application — models, migrations,
> blueprints, a factory, auth, a JSON API, and **a real test suite**.
> **Time:** ~3 hours · **Port:** 5014 · **Builds on:** Days 08–13

---

## 1. What this consolidates

| Day | Technique | Where |
|---|---|---|
| 08 | models, relationships, cascades, SQL aggregates | `models.py`, `api.py` |
| 09 | Alembic migrations instead of `create_all()` | `migrations/` |
| 10 | factory, blueprints, config classes | `__init__.py`, `config.py` |
| 11 | REST conventions, JSON errors, pagination | `blueprints/api.py` |
| 13 | hashing, sessions, ownership checks | `blueprints/auth.py`, `security.py` |

**New today:** ownership that spans a *chain*, portable enums, and a test suite
that proves the authorisation actually works.

## 2. What you will build

Projects and tasks, per user. Tasks carry status, priority and due dates;
everything is filterable, and the same data is readable through a JSON API.

```
14_project_task_manager/
├── wsgi.py  pytest.ini
├── migrations/               generated with flask db migrate
├── tests/
│   ├── conftest.py           fixtures — the payoff of the factory
│   ├── test_auth.py
│   ├── test_authorization.py ← the tests that matter most
│   └── test_tasks.py
└── taskman/
    ├── __init__.py  config.py  extensions.py
    ├── models.py             User → Project → Task
    ├── security.py           ← ownership, in ONE place
    ├── forms.py
    └── blueprints/           auth · projects · tasks · api
```

## 3. Run it

```bash
source .venv/bin/activate
cd 14_project_task_manager

FLASK_APP=wsgi.py flask db upgrade -d migrations   # apply migrations
FLASK_APP=wsgi.py flask seed
FLASK_APP=wsgi.py flask run --port 5014 --debug

pytest                                             # 30 tests
```

| Account | Password |
|---|---|
| `ana@example.com` | `CorrectHorseBattery1` |
| `vik@example.com` | `CorrectHorseBattery2` |

## 4. Try it — learn by doing

### Attack it as the other user

Sign in as **ana**, then note a project id. Sign in as **vik** in a private
window and request it:

```
/projects/<ana's id>            → 404
/tasks/<ana's task id>/edit     → 404
/tasks/?project=<ana's id>      → empty list, not her tasks
/api/v1/projects/<ana's id>     → 404 JSON
```

Every one of these would leak data if `@login_required` were the only check.

```bash
pytest tests/test_authorization.py -v
```

Those eight tests are the most valuable in the suite.

### The API

```bash
# session-cookie auth: log in first, keep the jar
curl -s -c jar -X POST http://127.0.0.1:5014/auth/login \
     -d "email=ana@example.com&password=CorrectHorseBattery1" \
     -d "csrf_token=$(curl -s -c jar http://127.0.0.1:5014/auth/login \
        | grep -o 'csrf_token" value="[^"]*' | cut -d'"' -f3)" > /dev/null

curl -s -b jar http://127.0.0.1:5014/api/v1/tasks  | python -m json.tool
curl -s -b jar http://127.0.0.1:5014/api/v1/stats  | python -m json.tool
curl -s -b jar http://127.0.0.1:5014/api/v1/tasks  # 401 without the jar
```

## 5. Ownership across a chain

Ownership flows `User → Project → Task`. Deciding whether you may touch a task
means asking who owns the task's **project**:

```python
def owned_task_or_404(task_id: int) -> Task:
    task = db.session.get(Task, task_id)
    if task is None or task.project.owner_id != current_user.id:
        abort(404, description="No such task.")
    return task
```

Note it checks `task.project.owner_id`, **not** `task.assignee_id`. Being
assigned a task is not the same as owning the project it lives in — and getting
that backwards is the sort of bug that survives review precisely because a
check *is* present.

Both helpers live in `security.py` so that:

- every view calls the same function,
- a reviewer can audit authorisation by reading one file,
- and it is impossible to *almost* apply it.

### Authorisation belongs in the query

```python
statement = select(Task).join(Task.project).where(Project.owner_id == current_user.id)
if filters["project"].isdigit():
    statement = statement.where(Task.project_id == int(filters["project"]))
```

Because the filter is applied **on top of an already-scoped query**, a
hand-edited `?project=<someone else's id>` matches no rows. There is a test for
exactly this.

## 6. Enums, stored portably

```python
class TaskStatus(str, enum.Enum):
    TODO = "todo"
    ...

status: Mapped[TaskStatus] = mapped_column(
    Enum(TaskStatus, native_enum=False, length=20, validate_strings=True))
```

- **Subclass `str` as well as `Enum`** so members compare equal to their values.
  Templates print them cleanly and JSON serialisation is trivial, while Python
  still gets `TaskStatus.TODO` with autocompletion and typo protection.
- **`native_enum=False`** stores a `VARCHAR` with a `CHECK` constraint instead
  of a database-native `ENUM`. Native enums are a migration nightmare — adding a
  value to a PostgreSQL enum needs `ALTER TYPE` and historically could not run
  inside a transaction. A constrained string is portable and easy to evolve.

## 7. One writer per derived field

```python
def mark(self, status: TaskStatus) -> None:
    self.status = status
    self.completed_at = datetime.now(timezone.utc) if status == TaskStatus.DONE else None
```

Every code path — form, API, seed script — goes through `mark()`, so none of
them can set `DONE` and forget the timestamp. Same principle as Day 08's stock
ledger.

Note also the deliberate cascade choice:

```python
assignee_id: ... ForeignKey("users.id", ondelete="SET NULL")   # not CASCADE
```

Deleting a user must **not** delete their assigned tasks — the work still
exists, it just becomes unassigned.

## 8. Two bugs this build hit (both instructive)

### Two blueprints, one URL rule

```python
@projects_bp.route("/")   # registered first → wins
@tasks_bp.route("/")      # silently unreachable; /tasks/ returned 404
```

Blueprints namespace **endpoint names**, not **URL rules**. `projects.index` and
`tasks.index` can coexist as names, but both cannot own `/`. Fixed by
registering `projects_bp` with `url_prefix="/projects"` and giving the task list
an explicit `/tasks/` path.

> Run `flask routes` after wiring blueprints. It takes two seconds and shows
> exactly what you built.

### Macros cannot see context processors

```
jinja2.exceptions.UndefinedError: 'TaskStatus' is undefined
```

The `status_buttons` macro loops over `TaskStatus`, which was supplied by a
context processor — and **macros are isolated from the caller's context**
(Day 03 §9, Day 07 §11).

The fix is the distinction worth learning:

| | Lives on | Visible in macros? | Use for |
|---|---|---|---|
| `@app.context_processor` | per-request context | ❌ | per-request values (`today`) |
| `app.jinja_env.globals` | the environment | ✅ | constants, enums, helpers |

```python
app.jinja_env.globals.update(TaskStatus=TaskStatus, TaskPriority=TaskPriority)
```

Note `today` deliberately **stayed** a context processor: a global would freeze
the date at start-up, and a long-running process would report yesterday forever.

## 9. The test suite

```python
@pytest.fixture
def app() -> Iterator[Flask]:
    application = create_app("testing")       # ← this is why Day 10 exists
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()
```

Function-scoped, in-memory database. Every test gets a clean world — no ordering
dependencies, no "passes alone, fails in the suite" flakes.

`TestingConfig` also does two things worth copying:

```python
WTF_CSRF_ENABLED = False                       # tests can POST without scraping a token
PASSWORD_HASH_METHOD = "pbkdf2:sha256:1000"    # scrypt is slow BY DESIGN
```

scrypt's slowness is the whole point in production and murder in a suite that
creates users constantly. Scoping the cheap method to `TestingConfig` makes it
impossible to reach production — which is the argument for config classes over
`if app.debug` checks.

What the 30 tests cover:

| File | Proves |
|---|---|
| `test_auth.py` | hashing, salting, uniform failures, 401 JSON for APIs, open-redirect blocked |
| `test_authorization.py` | no cross-user read, write, delete, create, list or filter |
| `test_tasks.py` | `completed_at` upkeep, overdue rules, cascade, API 415/422 |

## 10. Best practices introduced today

| Practice | Reason |
|---|---|
| Ownership helpers in one module | authorisation can be audited in one file |
| Check the **owner of the container** | assignee ≠ owner |
| Scope the base query, then filter | a hostile filter value matches nothing |
| `str`-backed enums, `native_enum=False` | portable, migratable, template-friendly |
| One method owns each derived field | no path can forget the timestamp |
| `ondelete="SET NULL"` for assignment | deleting a person must not delete the work |
| Jinja globals for enums, context processors for per-request values | macros can see globals |
| Cheap password hashing **in testing config only** | fast suite, no production risk |
| Function-scoped app + in-memory DB | no cross-test contamination |
| Unknown filter values ignored, not fatal | stale bookmarks must not 500 |
| `flask routes` after wiring blueprints | catches URL collisions immediately |
| Tests for authorisation, not just happy paths | the bugs that matter are access-control bugs |

## 11. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| A route 404s that clearly exists | two blueprints claim one URL rule | prefix one; check `flask routes` |
| `UndefinedError` inside a macro | macros can't see context processors | `jinja_env.globals` |
| Users see each other's data | ownership checked on the wrong model | check the container's owner |
| `?project=` leaks another user's tasks | filter applied to an unscoped query | scope first, filter second |
| Test suite crawls | scrypt in tests | cheap hash in `TestingConfig` only |
| Tests pass alone, fail together | shared database | function-scoped in-memory DB |
| `completed_at` out of step | status set directly in a view | route every change through `mark()` |
| Enum migration fails on Postgres | `native_enum=True` | `native_enum=False` |
| 500 from a stale bookmark | filter value cast without validation | ignore unknown values |

## 12. Exercises

1. **Replace `to_dict` with Pydantic schemas** (Day 12). The API is the only
   caller, so this is a contained change — and you gain a JSON Schema.
2. Add **team projects**: a `ProjectMember` association table, and update
   `security.py` so members get read access while only the owner may delete.
   Note that every view is already funnelled through those two helpers.
3. Add `?sort=` to the task list with an allow-list (Day 11 §10).
4. Add task **comments**, with the same ownership chain.
5. Add a `tasks.due_soon` API endpoint and a test that pins the date rather than
   using `date.today()`, so the test cannot break tomorrow.
6. Add coverage: `pytest --cov=taskman --cov-report=term-missing`. Find an
   untested branch and decide whether it is worth a test.
7. Break authorisation on purpose — delete the owner check in
   `owned_task_or_404` — and watch `test_authorization.py` fail. That is what
   those tests are for.

## 13. Week 2 review

1. Why can `create_all()` not replace migrations?
2. What is the N+1 problem, and which two tools fix it?
3. Why does the factory pattern make per-test isolation possible?
4. Why 404 rather than 403 for another user's record?
5. Why must `PATCH` use `exclude_unset=True`?
6. Where does authorisation belong — the view, the template, or the query?

## 14. What's next

**[Day 15 — JWT Auth and RBAC →](../15_jwt_auth_and_rbac/)**
Session cookies work for browsers. Mobile apps and service-to-service calls need
tokens: access/refresh pairs, claims, roles, and revocation.

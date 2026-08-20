# Day 07 — Week 1 Project: Expense Tracker

> **Goal:** put Days 01–06 together into one real application, with a layered
> structure that Day 08 can upgrade without touching a single view.
> **Time:** ~2 hours · **Port:** 5007 · **Builds on:** Days 01–06

---

## 1. What this consolidates

| Day | Technique | Where it shows up |
|---|---|---|
| 01 | app object, `/health` | `health()` |
| 02 | routes, `abort(404)`, error handler | `expense_detail()`, `not_found()` |
| 03 | inheritance, macros, filters, context processor | `_macros.html`, `inr`, `nice_date` |
| 04 | POST/Redirect/GET, 303/422, `MAX_CONTENT_LENGTH` | `dashboard()`, `delete_expense()` |
| 05 | Flask-WTF, custom validators, GET filter form | `forms.py` |
| 06 | session preference, flash | `toggle_compact()` |

**New today:** a layered structure, money as integer paise, CSV downloads, and
custom `flask` CLI commands.

## 2. Architecture

```
07_project_expense_tracker/
├── app.py         ← knows about HTTP: routes, status codes, redirects
├── forms.py       ← knows about valid input: fields and validators
├── storage.py     ← knows about persistence: read, write, filter, aggregate
├── data/
│   └── expenses.json     (created on first write; gitignored)
├── templates/
│   ├── base.html  _macros.html  dashboard.html  detail.html  404.html
└── static/css/style.css
```

The layering is the lesson:

```
   HTTP request
        │
   app.py         "a POST arrived; is it valid? then redirect 303"
        │
   forms.py       "is this a real date, a positive amount, a known category?"
        │
   storage.py     "append this record; give me last month's totals"
        │
   data/expenses.json
```

Each layer talks only to the one below it. **`app.py` contains no `json.load`
and no `open()`.** That is what makes Day 08 a drop-in replacement: swap
`storage.py` for SQLAlchemy and every view keeps working.

## 3. Run it

```bash
source .venv/bin/activate

# Optional: 30 realistic expenses across the last 90 days
flask --app 07_project_expense_tracker/app.py seed --count 30

flask --app 07_project_expense_tracker/app.py run --port 5007 --debug
```

Open <http://127.0.0.1:5007/>.

Other commands:

```bash
flask --app 07_project_expense_tracker/app.py --help     # see `seed` and `wipe`
flask --app 07_project_expense_tracker/app.py wipe       # prompts before deleting
flask --app 07_project_expense_tracker/app.py routes
```

## 4. Try it — learn by doing

```bash
# Filters live in the URL, so every view is shareable and bookmarkable
curl -s "http://127.0.0.1:5007/api/summary?category=rent" | python -m json.tool
curl -s "http://127.0.0.1:5007/api/summary?month=2026-08" | python -m json.tool

# The CSV export respects the SAME filters as the page you are looking at
curl -s "http://127.0.0.1:5007/export.csv?category=groceries" | head -5
curl -sI "http://127.0.0.1:5007/export.csv" | grep -i content-disposition

# Health check reports storage state
curl -s http://127.0.0.1:5007/health | python -m json.tool
```

**In the browser:**

1. Add an expense of `249.50`. Open its detail page and see it stored as
   `24950` **paise** — an integer.
2. Try a future date → rejected by `validate_spent_on`.
3. Click a category badge → the list filters, and the URL changes. Press the
   back button. Copy the URL into another browser: same view.
4. Toggle "Compact rows" → the preference persists in your session across pages.
5. Delete an expense, then press the back button and try to delete it again →
   "That expense was already gone", not a crash.

## 5. Money: the rule that prevents silent corruption

```python
>>> 0.1 + 0.2
0.30000000000000004
```

Floats cannot represent most decimal fractions. Those errors accumulate until
your totals disagree with reality — and in accounting, "close enough" is a bug
report.

**Store the smallest currency unit as an integer.**

```python
rupees_to_paise("249.50")  # -> 24950   (int)
paise_to_rupees(24950)     # -> 249.5   (only when displaying)
```

Everything downstream — sums, comparisons, JSON — uses integers. The `inr`
filter divides by 100 exactly once, at the moment of rendering. In a database
this is `NUMERIC(12,2)`, never `FLOAT`.

## 6. Filters belong in the URL, not the session

It would be easy to store the current category filter in `session`. Don't:

| In the URL ✅ | In the session ❌ |
|---|---|
| shareable link | recipient sees *their* filter |
| bookmarkable | bookmark is meaningless |
| back button works | back button appears broken |
| CSV export can reuse it | export silently ignores it |

`session` is for *preferences* (compact rows), the URL is for *state you are
looking at*. That is why `toggle_compact` uses the session and `_current_filters`
reads `request.args`.

## 7. Serving a file download

```python
return Response(
    buffer.getvalue().encode("utf-8-sig"),
    mimetype="text/csv",
    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
)
```

Three things make it a download rather than a page:

1. `Content-Type: text/csv` — what it is.
2. `Content-Disposition: attachment; filename=…` — save it, and call it this.
3. `utf-8-sig` — writes a BOM so Excel renders `₹` correctly instead of `â¹`.

And **always** use the `csv` module. `",".join(...)` breaks the moment a
description contains a comma or a quote, and it breaks silently.

## 8. Custom CLI commands

```python
@app.cli.command("seed")
@click.option("--count", default=25)
def seed_command(count: int) -> None:
    ...
```

`flask <name>` commands run inside a real application context with your config
loaded — unlike a standalone script, which has to reconstruct it. This is the
right home for seeding, imports, migrations and cleanups.

Note that `wipe` calls `click.confirm(..., abort=True)`. **A destructive command
that runs silently is a command that will one day run on the wrong machine.**

## 9. Atomic writes (and their limit)

`_write_all` writes to a temporary file in the same directory, then calls
`os.replace()`, which is atomic. A crash mid-write leaves the old file intact
instead of a truncated one.

> **But this is not a transaction.** Two processes writing concurrently still
> lose one update — last writer wins. `gunicorn -w 4` (Day 20) would corrupt
> this app's data. That is exactly the problem Day 08 solves.

## 10. Best practices introduced today

| Practice | Reason |
|---|---|
| Layered modules (`app` / `forms` / `storage`) | each layer is replaceable — proven on Day 08 |
| Repository pattern | no `json.load` anywhere in the views |
| Money as integer minor units | floats silently corrupt totals |
| Keyword-only args (`def add_expense(*, ...)`) | transposed arguments can't compile |
| `Path(__file__).parent` for data paths | works regardless of the working directory |
| Atomic write + `os.replace` | a crash can't truncate the data file |
| Aggregate in Python, not Jinja | template logic cannot be unit-tested |
| Guard divisions (`if total else 0`) | an empty list must not 500 the dashboard |
| Display filters never raise | one malformed row must not break the page |
| `default=date.today` (callable, not value) | a value would freeze at import time |
| Filters in the URL, preferences in the session | shareable, bookmarkable, back-button safe |
| `DecimalField`, not `FloatField` | preserves what the user typed |
| Return `None` from lookups, let the caller 404 | storage shouldn't decide HTTP status |
| `click.confirm` on destructive commands | irreversible actions deserve friction |

## 11. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `UndefinedError: 'categories' is undefined` **inside a macro** | macros are isolated from the caller's context | pass it as an argument (or `import … with context`) |
| Totals drift by a paisa | money stored as `float` | integer minor units |
| Data file not found after `cd` | relative path | `Path(__file__).parent / "data"` |
| Date field always offers yesterday | `default=date.today()` evaluated at import | `default=date.today` (no parentheses) |
| Export ignores the filters | didn't pass `**filters` to `url_for` | thread the filters through |
| Excel shows `â¹` | encoded as plain `utf-8` | `utf-8-sig` |
| `ZeroDivisionError` on an empty dashboard | percentage with no guard | `x / total if total else 0` |
| Data lost on crash | wrote directly to the target file | temp file + `os.replace` |
| Duplicate expenses on refresh | rendered from the POST | POST/Redirect/GET |

> The macro-context bug in that first row is real: it was hit while building
> this very example, exactly as Day 03 §9 predicted.

## 12. Exercises

1. **Edit an expense.** Add `GET/POST /expenses/<id>/edit` reusing `ExpenseForm`
   with `obj=` pre-population. Watch how little new code it takes.
2. **Budgets.** Add a monthly budget per category and show over/under on the
   dashboard.
3. **Import CSV.** Accept an upload and merge it (Day 16 covers uploads safely).
4. **Pagination.** Show 20 rows per page with `?page=2`.
5. **Test `storage.py`.** It has no Flask imports at all — write pytest cases
   for `summarise()` and `rupees_to_paise()` with no app and no client. That
   testability *is* the payoff of the layering.
6. **Prove the concurrency limit.** Run two `seed` commands simultaneously and
   count the rows. Then re-read §9.

## 13. Week 1 review — can you answer these?

1. Why does `render_template` need `Flask(__name__)`?
2. What is the difference between `/team/abc` returning 404 and `/team/99`
   returning 404?
3. When would you use `include` instead of a macro?
4. Why 303 and not 302 after a successful POST?
5. Why does `DataRequired()` reject `0`?
6. Your session cookie is signed. Can the user read it? Can they change it?
7. Why must a shopping cart never store prices?

If any of those are shaky, revisit that day before moving on — Week 2 assumes
all of it.

## 14. What's next

**[Day 08 — Database with SQLAlchemy →](../08_database_with_sqlalchemy/)**
Replace `storage.py` with a real database: models, sessions, queries,
relationships, and the transactions that make concurrent writes safe.

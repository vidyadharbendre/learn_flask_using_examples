# Day 08 — Database with SQLAlchemy

> **Goal:** replace Day 07's JSON file with a real database — models, sessions,
> transactions, relationships, constraints, and the N+1 problem.
> **Time:** ~2 hours · **Port:** 5008 · **Builds on:** Day 07

---

## 1. Why this matters

Day 07 ended with three honest confessions about `storage.py`:

| Limit | Consequence |
|---|---|
| No transactions | two related writes can half-happen |
| No concurrency | `gunicorn -w 4` corrupts the file |
| No query engine | every filter loads *every* record into memory |

A database fixes all three. An ORM lets you use one without hand-writing SQL —
while still generating SQL you can read and reason about.

**And because Day 07 was layered, only the bottom two files change.** The views
are nearly identical. That was the whole point.

## 2. What you will build

An inventory manager: products, categories, stock levels, low-stock alerts, and
an append-only ledger of every stock movement.

```
08_database_with_sqlalchemy/
├── extensions.py    ← db = SQLAlchemy()   (the circular-import cure)
├── models.py        ← tables as classes
├── repository.py    ← queries (same shape as Day 07's storage.py)
├── forms.py         ← Day 05 patterns, choices loaded from the DB
├── app.py           ← views + CLI commands
├── instance/
│   └── inventory.db (created by init-db; gitignored)
└── templates/  static/
```

## 3. Run it

```bash
source .venv/bin/activate
flask --app 08_database_with_sqlalchemy/app.py init-db
flask --app 08_database_with_sqlalchemy/app.py seed
flask --app 08_database_with_sqlalchemy/app.py run --port 5008 --debug
```

Open <http://127.0.0.1:5008/>.

**Turn on SQL echo and actually read the output** — it is the fastest way to
learn what an ORM does:

```bash
SQL_ECHO=1 flask --app 08_database_with_sqlalchemy/app.py run --port 5008
```

## 4. Try it — learn by doing

### See the N+1 problem with your own eyes

```bash
SQL_ECHO=1 flask --app 08_database_with_sqlalchemy/app.py demo-n-plus-one
```

```
--- LAZY (N+1): one query for products, then one PER product ---
    touched 7 products -> ~8 queries
--- EAGER (selectinload): two queries, total ---
    touched 7 products -> 2 queries
```

With 7 products that is invisible. With 5,000 it is a 30-second page. **This is
the single most common cause of a slow Flask app.**

### Watch the database enforce rules your Python cannot

1. Add a product with SKU `LAP-001` (it already exists). The error comes from
   the database's `UNIQUE` constraint via `IntegrityError` — and is displayed as
   an ordinary field error.
2. Open any product and try to remove more stock than exists. The friendly
   message is from `record_movement()`; the `CHECK (quantity >= 0)` constraint
   is the backstop for every *other* code path.
3. Delete a product → its entire movement history goes with it (cascade).

### From the command line

```bash
curl -s http://127.0.0.1:5008/api/inventory | python -m json.tool
curl -s "http://127.0.0.1:5008/?low=1"     | grep -c is-low
curl -s http://127.0.0.1:5008/health       | python -m json.tool
```

### Inspect the actual database

```bash
sqlite3 08_database_with_sqlalchemy/instance/inventory.db
sqlite> .schema products
sqlite> SELECT sku, quantity FROM products;
sqlite> SELECT * FROM stock_movements ORDER BY created_at DESC LIMIT 5;
```

Reading the generated `CREATE TABLE` is worth ten pages of ORM documentation.

## 5. The circular-import trap (everyone hits it once)

```python
# app.py                          # models.py
from models import Product        from app import db      # 💥
db = SQLAlchemy(app)
```

```
ImportError: cannot import name 'db' from partially initialized module 'app'
             (most likely due to a circular import)
```

**The cure** — create extensions in a module that imports nothing of yours:

```python
# extensions.py
db = SQLAlchemy(model_class=Base)      # no app, inert until init_app

# models.py
from extensions import db

# app.py
from extensions import db
db.init_app(app)
```

This is the **deferred initialisation** (`init_app`) pattern, and it works for
every Flask extension. Day 10 generalises it into a full application factory.

## 6. The session: a unit of work

```python
db.session.add(product)     # stage it — nothing is written yet
db.session.commit()         # write it, atomically
db.session.rollback()       # discard everything staged
```

The session is a **transaction**, not a connection pool. Everything between
commits either lands together or not at all. That is how `record_movement()`
updates `products.quantity` *and* inserts a `stock_movements` row without ever
leaving stock that no ledger entry explains.

> **After an `IntegrityError` you MUST call `rollback()`.** Otherwise the
> session stays poisoned and every later query raises `PendingRollbackError` —
> an error that sends people debugging in completely the wrong place.

## 7. Check-then-act is a race; let the database decide

```python
# ❌ Race condition: two requests both pass the check, both insert
if db.session.execute(select(Product).where(Product.sku == sku)).first():
    return "SKU exists"
db.session.add(Product(sku=sku))

# ✅ Ask forgiveness: only the database can decide atomically
try:
    db.session.add(Product(sku=sku)); db.session.commit()
except IntegrityError:
    db.session.rollback()
    return None, f"SKU {sku!r} already exists."
```

## 8. Relationships

```python
class Category(db.Model):
    products: Mapped[list["Product"]] = relationship(
        back_populates="category", cascade="all, delete-orphan", lazy="selectin")

class Product(db.Model):
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), index=True)
    category: Mapped["Category"] = relationship(back_populates="products")
```

- `ForeignKey` creates the **column**; `relationship` creates the **Python
  attribute** and no column at all.
- `back_populates` keeps both sides in sync in memory.
- **Set cascade in both places.** `cascade="all, delete-orphan"` applies when you
  delete through the ORM session; `ondelete="CASCADE"` applies to everything
  else — a `psql` shell, another service, a bulk `DELETE`.

### Loading strategies

| Strategy | Queries for N parents | Use when |
|---|---|---|
| `lazy="select"` (default) | 1 + N ❌ | you rarely touch the relation |
| `lazy="selectin"` / `selectinload()` | 2 ✅ | you list parents *and* show children |
| `joinedload()` | 1 | one-to-one, or small one-to-many |

Set it per query with `.options(selectinload(Product.category))` when only some
views need it.

## 9. Constraints: guarantees Python cannot give you

```python
CheckConstraint("quantity >= 0", name="ck_products_quantity_non_negative")
```

Form validation (Day 05) stops honest mistakes at *one* entry point. A database
constraint stops **every** code path — the CLI script you write next year, the
colleague's migration, the `UPDATE` someone runs in a shell at midnight.

> **But a wrong constraint is worse than a missing one.** An earlier draft of
> `models.py` had `UniqueConstraint("product_id", "created_at")`. It looked
> tidy and it was wrong: two movements for one product genuinely can share a
> timestamp, and it blew up the first time two movements were recorded in the
> same second. A constraint encodes a **rule of the domain**, never a
> preference for neatness.

## 10. Money in the database

```python
price: Mapped[Decimal] = mapped_column(Numeric(10, 2))   # ✅ exact
price: Mapped[float]   = mapped_column(Float)            # ❌ 0.1+0.2 all over again
```

`Numeric`/`DECIMAL` maps to Python's `Decimal` and is exact. This is Day 07's
integer-paise rule, enforced one layer deeper — and note `jsonify` converts it
with `str()`, because JSON has no decimal type and a float would undo the point.

## 11. Aggregate in SQL, not in Python

```python
# Day 07: load everything, then sum in Python
total = sum(e["amount_paise"] for e in _read_all())

# Day 08: the database sums it — one query, no rows transferred
select(func.coalesce(func.sum(Product.price * Product.quantity), 0))
```

Two details that matter:

- **`coalesce(..., 0)`** — `SUM` over zero rows returns `NULL`, which becomes
  `None`, which crashes your formatter. Always coalesce.
- **`outerjoin`, not `join`** — an inner join silently drops categories with no
  products. "The empty category vanished from the report" costs an afternoon.

## 12. `Mapped[...]` annotations are resolved at runtime

```python
if TYPE_CHECKING:
    from decimal import Decimal      # ❌ ArgumentError: Could not resolve...
from decimal import Decimal          # ✅
```

SQLAlchemy reads the annotation when it maps the class, so **every name used in
`Mapped[...]` must be importable at runtime** — even with
`from __future__ import annotations`. This one bit while writing this very
example.

## 13. Best practices introduced today

| Practice | Reason |
|---|---|
| `extensions.py` + `init_app` | breaks the circular import |
| SQLAlchemy 2.0 `Mapped[...]` style | type-checked, and what current docs use |
| `Numeric`, never `Float`, for money | exactness at the storage layer |
| Aware UTC timestamps | naive/aware comparisons explode at 2am |
| `server_default=func.now()` | the DB stamps rows inserted by *any* client |
| Cascade in **both** ORM and DB | covers every code path |
| `try/except IntegrityError` + `rollback()` | check-then-act is a race |
| Eager-load what you render | avoids N+1 |
| Index the columns you filter and join on | that is what makes a DB fast |
| `coalesce` around `SUM` | empty tables must not crash |
| `outerjoin` for "including empty" reports | inner joins hide rows |
| Never f-string user input into SQL | the ORM parameterises everything |
| One writer for a derived total | `quantity` changes only via the ledger |
| Ledger tables for anything auditable | `UPDATE` destroys history |
| `instance/` for the DB file | environment-specific, never committed |

## 14. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `ImportError: partially initialized module` | circular import | `extensions.py` + `init_app` |
| `ArgumentError: Could not resolve all types` | name used in `Mapped[...]` only imported under `TYPE_CHECKING` | import it at runtime |
| `PendingRollbackError` on every query | didn't `rollback()` after `IntegrityError` | always roll back |
| Table missing after `create_all()` | the model module was never imported | import it before `create_all()` |
| New column missing → `no such column` | `create_all()` never **alters** tables | migrations — **Day 09** |
| Page slow with many rows | N+1 lazy loads | `selectinload` |
| Filtering on a `@property` silently ignored | properties run in Python, not SQL | express the rule with columns |
| `TypeError: unsupported format string` on an empty table | `SUM` returned `NULL` | `coalesce(..., 0)` |
| Totals drift by a paisa | `Float` column | `Numeric(10, 2)` |
| Empty categories missing from a report | inner join | `outerjoin` |
| Deleting a parent errors | no cascade configured | choose cascade *or* block the delete |
| Two records created despite a check | check-then-act race | `UNIQUE` + catch `IntegrityError` |

## 15. Exercises

1. **Edit a product.** Add `GET/POST /products/<id>/edit`. Note you only need
   `db.session.commit()` — the ORM tracks the change automatically.
2. **Suppliers.** Add a `Supplier` model and a many-to-one from `Product`.
3. **Many-to-many.** Add `Tag` with an association table so a product can carry
   several tags.
4. **Reconstruct from the ledger.** Write a `verify-stock` CLI command comparing
   each product's `quantity` against `SUM(movements.delta)`. They must match —
   that is why the ledger exists.
5. **Pagination.** Use `db.paginate(select(...), page=…, per_page=20)`.
6. **Prove the index matters.** Add 50,000 products, then run
   `EXPLAIN QUERY PLAN SELECT * FROM products WHERE sku = 'X';` with and without
   the index on `sku`.
7. **Break `create_all()` on purpose.** Add a `barcode` column to `Product`,
   re-run `init-db`, and watch nothing happen. Now you *need* Day 09.

## 16. What's next

**[Day 09 — Migrations with Flask-Migrate →](../09_migrations_with_flask_migrate/)**
`create_all()` cannot evolve a schema that already holds data. Alembic can.

---

<!-- nav -->
[← Day 07 — Week 1 Project: Expense Tracker](../07_project_expense_tracker/) · **[All 21 days](../README.md)** · [Day 09 — Migrations with Flask-Migrate →](../09_migrations_with_flask_migrate/)

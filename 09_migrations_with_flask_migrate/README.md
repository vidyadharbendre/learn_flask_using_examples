# Day 09 — Migrations with Flask-Migrate

> **Goal:** evolve a schema that already holds real data — without losing any of
> it. Alembic revisions, data backfills, and the autogenerate mistakes you must
> fix by hand.
> **Time:** ~90 minutes · **Port:** 5009 · **Builds on:** Day 08

---

## 1. Why this matters

Day 08 ended with an exercise: add a column to `Product`, re-run `init-db`, and
watch **nothing happen**.

```python
db.create_all()      # CREATE TABLE IF NOT EXISTS  -> table exists -> skipped
```

Your app then dies with `no such column: products.barcode`. `create_all()` can
*create* a schema; it can never *evolve* one.

Alembic keeps an ordered chain of migration scripts plus a single row in your
database (`alembic_version`) recording where **that** database sits in the
chain. `flask db upgrade` replays whatever is missing. That row is the whole
mechanism.

## 2. What you will build

Day 08's inventory, evolved across three real revisions that ship in this repo:

| Revision | Change |
|---|---|
| `d3752bd02e9d` | initial schema — `categories`, `products` |
| `7ad7e54df8a1` | `+ barcode`, `+ reorder_level` — **with a data backfill** |
| `32635f848382` | `+ suppliers` table, `+ products.supplier_id` |

```
09_migrations_with_flask_migrate/
├── extensions.py       db + migrate, both deferred
├── models.py           the schema as it is TODAY (the destination)
├── app.py              views + demo commands
├── migrations/
│   ├── env.py  alembic.ini
│   └── versions/       the JOURNEY — three revisions, two hand-edited
└── instance/inventory.db   (gitignored)
```

## 3. Run it

```bash
source .venv/bin/activate
export FLASK_APP=09_migrations_with_flask_migrate/app.py

flask db upgrade -d 09_migrations_with_flask_migrate/migrations
flask --app 09_migrations_with_flask_migrate/app.py seed
flask --app 09_migrations_with_flask_migrate/app.py run --port 5009 --debug
```

Open <http://127.0.0.1:5009/>. The page shows the applied revision and the
**live** database structure read with `inspect()` — models versus reality.

## 4. Try it — learn by doing

### Watch a data migration actually backfill rows

A backfill is invisible unless rows exist *before* the column does. This command
stages exactly that:

```bash
flask --app 09_migrations_with_flask_migrate/app.py demo-journey
```

```
1. downgrade -> base
2. upgrade   -> d3752bd02e9d (initial schema only)
3. insert products that PREDATE the barcode column
   3 products inserted, with NO barcode column in existence
4. upgrade   -> head (adds columns AND backfills them)

Result — barcodes written by the migration, not by the app:
   LAP-001    barcode=0000000000001  reorder=5  supplier=Unassigned
   LAP-002    barcode=0000000000002  reorder=5  supplier=Unassigned
   PER-001    barcode=0000000000003  reorder=5  supplier=Unassigned
```

**No application code ever wrote those barcodes.** Migration `7ad7e54df8a1` did.

### The core commands

```bash
D=09_migrations_with_flask_migrate/migrations

flask db current  -d $D          # where is this database?
flask db history  -d $D          # the whole chain
flask db upgrade  -d $D          # apply everything outstanding
flask db downgrade -d $D         # undo ONE revision
flask db downgrade -d $D base    # undo everything
flask db upgrade  -d $D <rev>    # go to a specific revision
flask db show     -d $D <rev>    # show one revision
```

### Create a migration yourself

1. Add a field to `Product` in `models.py`:

   ```python
   weight_grams: Mapped[int | None] = mapped_column(nullable=True)
   ```

2. Reload <http://127.0.0.1:5009/> — the column is **not** in the live table.

3. Generate and inspect:

   ```bash
   flask db migrate -d $D -m "add product weight"
   cat $D/versions/*add_product_weight*.py      # READ IT BEFORE APPLYING
   flask db upgrade -d $D
   ```

4. Reload. It appears.

5. Undo it: `flask db downgrade -d $D`.

### Test your downgrade

```bash
flask db downgrade -d $D base && flask db upgrade -d $D
```

**A downgrade that has never been run is a downgrade that does not work.** Both
migrations in this repo were tested this way, which is how the unnamed-constraint
bug was caught.

## 5. Autogenerate is a draft, not an author

`flask db migrate` compares your models to the database and **guesses**. Both
hand-written migrations here document what it got wrong:

### It leaves constraints unnamed

```python
batch_op.create_unique_constraint(None, ["barcode"])   # generated
batch_op.drop_constraint(None, type_="unique")         # downgrade — cannot work
```

You cannot drop a constraint by a name you never chose. **Always name them.**

### It cannot see data

Adding a nullable column and leaving it `NULL` for every existing row is half a
change. The migration must backfill.

### It reads a rename as drop + add

```python
op.drop_column("title")           # 💥 DATA LOSS
op.add_column("name")
```

Alembic has no idea those are the same column. Rewrite it as
`op.alter_column("products", "title", new_column_name="name")`.

### What it misses entirely

Table/column renames · data changes · `CHECK` constraints (on some backends) ·
server defaults · index *changes* · anything about intent.

> **Rule: open every generated migration and read it before committing.**

## 6. Adding columns to a populated table

| You want | Problem | Solution |
|---|---|---|
| nullable column | none | just add it |
| `NOT NULL` column | existing rows have no value | add with `server_default=...` |
| `NOT NULL` **foreign key** | no legal value for existing rows | **three deploys** ↓ |

The safe three-deploy sequence for a required FK:

```
(a) add the column NULLABLE                    <- ship
(b) backfill it; app code always sets it       <- ship
(c) migration flips it to NOT NULL             <- ship, once no NULLs remain
```

Attempting all three at once is a classic way to take a site down.

## 7. Migrations must never import your models

```python
from models import Supplier          # ❌ never do this in a migration
op.execute("UPDATE products SET ...")  # ✅
sa.table("suppliers", sa.column("id"), sa.column("name"))  # ✅ inline mini-model
```

**Why:** a migration is pinned to one moment in your schema's history;
`models.py` keeps evolving. Import the live model and this file breaks the day
someone adds a column — replaying history from scratch would emit SQL
referencing a column that does not exist *yet* at that point.

Migrations must be **self-contained and frozen in time**.

## 8. `render_as_batch=True`

```python
migrate.init_app(app, db, render_as_batch=True)
```

SQLite's `ALTER TABLE` is severely limited — no `DROP COLUMN` in older versions,
no `ALTER COLUMN`, no adding constraints to an existing table. Batch mode works
around it: create a new table with the target shape, copy the rows, drop the
old, rename.

Without this flag, your first SQLite `drop_column` fails with
`NotImplementedError`. On PostgreSQL and MySQL it is a harmless wrapper, so
leave it on.

## 9. Deploying migrations safely

```bash
pg_dump mydb > backup.sql            # 1. BACK UP. Always.
flask db upgrade                     # 2. migrate BEFORE starting new code
# 3. then roll out the new application version
```

| Rule | Reason |
|---|---|
| Back up first | `downgrade` cannot recover a dropped column's data |
| Migrate before deploying code | new code expecting a missing column crashes |
| Make migrations backward-compatible | during a rolling deploy, **old and new code run simultaneously** |
| Never `create_all()` again | it creates tables Alembic doesn't know about |
| Commit migration files | they are source code, and the chain must be shared |
| One logical change per revision | small revisions are reviewable and revertible |
| Never edit an applied migration | write a new one; teammates already ran the old |
| Expose the revision on `/health` | confirms the deploy actually migrated |

Backward compatibility deserves emphasis: dropping a column in the same release
that stops using it will break every old process still serving traffic. Deprecate
first, drop one release later.

## 10. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `No changes in schema detected` | the model module was never imported | import models in `app.py` |
| `Target database is not up to date` | pending revisions | `flask db upgrade` first |
| `Can't locate revision identified by '...'` | someone deleted or rewrote a revision | restore it; never rewrite applied history |
| `NotImplementedError` on `drop_column` | SQLite without batch mode | `render_as_batch=True` |
| `Cannot add a NOT NULL column with default value NULL` | populated table | add `server_default` |
| `downgrade` fails on a constraint | it was created unnamed | name every constraint |
| Migration works in dev, fails in prod | dev DB was empty; prod is not | test against a copy of prod |
| Column silently dropped | a rename autogenerated as drop + add | use `alter_column(new_column_name=…)` |
| Multiple heads | two branches each added a revision | `flask db merge heads` |
| Migration references a missing column | it imported a live model | use raw SQL or an inline `sa.table` |

## 11. Exercises

1. Add `Product.weight_grams` (nullable), generate the migration, read it,
   apply it, then downgrade.
2. Add `Supplier.phone` as `NOT NULL` with `server_default=''`, and explain to
   yourself why the default is mandatory.
3. **Rename** `Product.name` to `Product.title`. Autogenerate will produce
   drop + add — rewrite it as an `alter_column` and prove no data is lost.
4. Write a data-only migration that upper-cases every existing SKU.
5. Make `products.supplier_id` `NOT NULL`, following the three-deploy sequence
   from §6.
6. Add a naming convention to `Base.metadata` so constraints are named
   automatically, and confirm new autogenerated migrations pick it up.
7. Create two divergent revisions on purpose, hit "Multiple heads", and resolve
   it with `flask db merge heads`.

## 12. What's next

**[Day 10 — Blueprints and the Application Factory →](../10_blueprints_and_app_factory/)**
`app.py` is getting long. Split it into blueprints, build the app with
`create_app()`, and configure it per environment.

---

<!-- nav -->
[← Day 08 — Database with SQLAlchemy](../08_database_with_sqlalchemy/) · **[All 21 days](../README.md)** · [Day 10 — Blueprints and the Application Factory →](../10_blueprints_and_app_factory/)

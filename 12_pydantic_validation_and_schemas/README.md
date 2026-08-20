# Day 12 — Pydantic Validation and Schemas

> **Goal:** replace hand-written validation with declarative schemas — and get
> mass-assignment protection, correct `PATCH` semantics, and OpenAPI-ready
> documentation as side effects.
> **Time:** ~90 minutes · **Port:** 5012 · **Builds on:** Day 11

---

## 1. Why this matters

Day 11's `_validate_book()` was **~90 lines** of `isinstance` checks — and it
still needed a special case, because in Python:

```python
>>> isinstance(True, int)
True                       # so {"stock": true} quietly stored 1 copy
```

That function would have to be rewritten for every resource you add.

Pydantic makes validation a **declaration**:

```python
class BookCreate(BookBase):
    isbn: ISBN
    title: Title
    price: Price
    stock: Stock = 0
```

The logic did not vanish — it moved somewhere reusable, self-documenting, and
capable of generating its own JSON Schema.

## 2. Day 11 → Day 12

| Day 11, by hand | Day 12, declared |
|---|---|
| `if not isinstance(t, str) or not t.strip()` | `title: Annotated[str, Field(min_length=1)]` |
| `if not 1450 <= year <= 2100` | `Year = Annotated[StrictInt, Field(ge=1450, le=2100)]` |
| `isinstance(x, bool)` guard | `StrictInt` |
| building an `errors` dict | `ValidationError.errors()` |
| `to_dict()` on every model | `BookOut.model_validate(obj)` |
| hand-written API docs | `model_json_schema()` |

## 3. What you will build

```
12_pydantic_validation_and_schemas/
├── wsgi.py
└── catalogue/
    ├── schemas.py     ← the day's lesson: the typed boundary
    ├── api.py         ← views shrink to routing + persistence
    ├── errors.py      ← Day 11's envelope, reused unchanged
    ├── models.py      ← note: no to_dict() anywhere
    └── extensions.py
```

## 4. Run it

```bash
source .venv/bin/activate
cd 12_pydantic_validation_and_schemas

FLASK_APP=wsgi.py flask seed
FLASK_APP=wsgi.py flask run --port 5012 --debug
```

## 5. Try it — learn by doing

```bash
API=http://127.0.0.1:5012/api/v1

curl -s "$API/books?per_page=1" | python -m json.tool
```

Look at one serialised book. **Nothing in `api.py` produced this shape** — the
schema did: `price` as a string, `tags` as a list (stored as a CSV string),
`created_at` with a UTC offset, and the derived `in_stock`.

### Every way a write can fail

```bash
# mass assignment — a client trying to set server-owned fields
curl -sX POST "$API/books" -H "Content-Type: application/json" -d '{
  "isbn":"9781111111117","title":"X","price":"1","published_year":2000,
  "author_id":1,"id":999,"created_at":"2020-01-01"}' | python -m json.tool
# 422: {"id": ["Extra inputs are not permitted"], "created_at": [...]}

# the Day 11 bool bug, now caught by the type
curl -sX POST "$API/books" -H "Content-Type: application/json" -d '{
  "isbn":"9781111111117","title":"X","price":"1","published_year":2000,
  "author_id":1,"stock":true}' | python -m json.tool
# 422: {"stock": ["Input should be a valid integer"]}

# ALL errors at once, per field
curl -sX POST "$API/books" -H "Content-Type: application/json" \
  -d '{"isbn":"123","title":"","price":"-5","published_year":3000,"author_id":0}' \
  | python -m json.tool
```

### The `PATCH` test that matters

```bash
curl -sX PATCH "$API/books/1" -H "Content-Type: application/json" -d '{"stock":0}'
```

`stock` becomes `0` and **every other field is untouched**. That is
`exclude_unset=True` doing its job — see §8.

### Documentation that cannot drift

```bash
curl -s "$API/schema" | python -m json.tool | head -40
```

Generated from the very classes that validate, so it can never disagree with the
implementation.

## 6. Three schemas per resource, not one

```python
class BookCreate(BookBase):  ...   # what a client may SEND to create
class BookUpdate(BaseModel): ...   # what a client may send to MODIFY (all optional)
class BookOut(BaseModel):    ...   # what the server RETURNS
```

Sharing one model across all three is a real security bug — **mass assignment**.
If `id` and `created_at` are on the schema you validate input with, a client can
set them. `BookCreate` simply does not have those fields, and:

```python
model_config = ConfigDict(extra="forbid")
```

turns an attempt into a `422` instead of a silent drop. `extra="forbid"` also
catches honest typos: `{"titel": "..."}` tells the client about the mistake
rather than ignoring it while they wonder why nothing changed.

## 7. Validators: three levels

```python
# 1. Constraints on the type — reusable across schemas
Price = Annotated[Decimal, Field(ge=0, le=Decimal("100000"), decimal_places=2)]

# 2. field_validator — real logic for ONE field, runs AFTER type coercion
@field_validator("isbn")
@classmethod
def normalise_isbn(cls, value: str) -> str:
    cleaned = value.replace("-", "")
    if not cleaned.isdigit() or len(cleaned) != 13:
        raise ValueError("must be 13 digits")     # plain ValueError!
    return cleaned

# 3. model_validator(mode="after") — cross-field rules, `self` fully typed
@model_validator(mode="after")
def check_stock_for_old_books(self) -> "BookCreate":
    if self.published_year < 1900 and self.stock > 100:
        raise ValueError("probably a typo")
    return self
```

- Raise plain **`ValueError`** — Pydantic folds it into the structured report
  with the right field location. You never raise `ValidationError` yourself.
- `mode="after"` sees validated, typed fields. `mode="before"` sees the raw
  input — useful for reshaping a payload.
- **Validators normalise, not just reject.** Lower-casing tags at the boundary
  is what stops `"Fiction"`, `"fiction"` and `" fiction "` becoming three tags.

## 8. `exclude_unset` — the flag that makes `PATCH` correct

```python
def changes(self) -> dict[str, Any]:
    return self.model_dump(exclude_unset=True)
```

Without it you cannot distinguish:

| Client sent | `model_dump()` | `model_dump(exclude_unset=True)` |
|---|---|---|
| `{"stock": 0}` | `{stock: 0, title: None, price: None, …}` | `{stock: 0}` ✅ |
| `{}` | same as above | `{}` |

Using the first would reset every unmentioned field to `None` — `PUT` behaviour
wearing a `PATCH` label, quietly destroying data.

## 9. Strict types

```python
Stock = Annotated[StrictInt, Field(ge=0)]     # ✅
Stock = Annotated[int, Field(ge=0)]           # ❌ accepts True and "5"
```

Pydantic's default *lax* mode is helpfully permissive: it coerces `"5"` → `5`,
and since `bool` subclasses `int`, `True` → `1`. **Verified while building this
example:** without `StrictInt`, `{"stock": true}` passed validation as `1`.

Lax mode is right for HTML form data (everything arrives as a string). For a
JSON API, where the client controls the types, strict is right.

## 10. Serialisation: `from_attributes`, computed fields, serialisers

```python
model_config = ConfigDict(from_attributes=True)  # read SQLAlchemy objects directly

tags: list[str] = Field(validation_alias="tag_list")   # map CSV column -> list

@computed_field
@property
def in_stock(self) -> bool: return self.stock > 0      # output only, never input

@field_serializer("price")
def serialise_price(self, v: Decimal) -> str: return f"{v:.2f}"
```

Two things to remember:

- **`model_dump(mode="json")`**, not plain `model_dump()`. The plain version
  leaves real `Decimal` and `datetime` objects in the dict and `jsonify` then
  fails.
- **Two JSON Schemas.** `mode="validation"` says what the server *accepts*;
  `mode="serialization"` says what it *returns*, and is the only one containing
  computed fields like `in_stock`. A complete OpenAPI document needs both.

## 11. Where Pydantic should *not* reach

```python
if db.session.get(Author, payload.author_id) is None:
    raise APIError(422, ...)        # in the VIEW, not in the schema
```

A schema validates the **shape** of data. "Does this row exist?" is a database
question, and putting a query inside a validator couples your schemas to a live
session — which breaks the moment you validate a payload in a test, a CLI
script, or a queue consumer.

**Shape in the schema; existence in the view.**

## 12. The `@validate_body` decorator

```python
@api_bp.post("/books")
@validate_body(BookCreate)
def create_book(payload: BookCreate): ...
```

The value is not fewer lines — it is that there is **no third state** where
validation was simply forgotten. A view either declares a schema or takes no
body.

> **`@wraps(view)` is mandatory.** Without it every decorated view registers
> under the endpoint name `"wrapper"` and Flask raises on the second one. This
> is the most common decorator bug in Flask codebases.

## 13. Best practices introduced today

| Practice | Reason |
|---|---|
| Separate Create / Update / Out schemas | prevents mass assignment |
| `extra="forbid"` | catches typos and injected fields |
| `StrictInt` for JSON APIs | `True` is not `1`; `"5"` is not `5` |
| `exclude_unset=True` for PATCH | distinguishes "omitted" from "set to zero" |
| Reusable `Annotated` types | one definition of your domain's vocabulary |
| Raise `ValueError` in validators | Pydantic attaches the field location |
| Normalise in validators | one canonical form reaches storage |
| `from_attributes=True` | no `to_dict()` boilerplate |
| `computed_field` for derived values | output-only by construction |
| `field_serializer` for money/time | the rule is declared once |
| `model_dump(mode="json")` | handles `Decimal` and `datetime` |
| Serve `model_json_schema()` | docs that cannot drift |
| Existence checks in views | schemas stay usable without a database |
| `@wraps` on every decorator | endpoint names stay distinct |
| `validate_assignment=True` | constraints survive later mutation |

## 14. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `TypeError: Object of type Decimal is not JSON serializable` | plain `model_dump()` | `model_dump(mode="json")` |
| `PATCH` wipes unsent fields | dumped without `exclude_unset` | `model_dump(exclude_unset=True)` |
| Client can set `id` | one schema for input and output | separate `Create` / `Out` |
| Typos silently ignored | default `extra="ignore"` | `extra="forbid"` |
| `{"stock": true}` stores 1 | lax `int` | `StrictInt` |
| `View function mapping is overwriting` | decorator without `@wraps` | add `@wraps(view)` |
| Validator never runs | missing `@classmethod`, or wrong field name | check both |
| `computed_field` missing from schema | validation-mode schema | `mode="serialization"` |
| Schemas need a DB in tests | a query inside a validator | move it to the view |
| `ValidationError` escapes as a 500 | no handler | catch it in `@validate_body` |
| Nested errors unreadable | raw `loc` tuples | flatten with `format_validation_error` |

## 15. Exercises

1. Add a `PUT /books/<id>` using `BookCreate` and explain why `PUT` needs the
   *create* schema rather than the *update* one.
2. Add `AuthorCreate` and `POST /authors`, with a validator rejecting names
   under two characters.
3. Add `?fields=id,title` sparse fieldsets using `model_dump(include=...)`.
4. Replace `format_validation_error` with RFC 9457 (`application/problem+json`)
   and compare the two envelopes.
5. Generate a full OpenAPI 3 document from the schemas and serve it at
   `/api/v1/openapi.json`. Note you need **both** schema modes.
6. Add a `BookQuery` schema validating the *query string* too, and use it in
   `list_books`.
7. Set `model_config = ConfigDict(strict=True)` on `BookBase` and see which
   existing requests break — then decide whether you want that.

## 16. What's next

**[Day 13 — Authentication with Flask-Login →](../13_authentication_with_flask_login/)**
Every endpoint so far has been wide open. Time for password hashing, sessions,
and `@login_required`.

# Day 11 — REST API Fundamentals

> **Goal:** design an API other people can use without asking you questions —
> resources, method semantics, honest status codes, one error shape, pagination
> and versioning.
> **Time:** ~2 hours · **Port:** 5011 · **Builds on:** Day 10

---

## 1. Why this matters

> **An API is a contract with people you will never meet.**

They cannot ask what a field means, they will not read your source, and they
**will** retry after a timeout. Predictability beats cleverness every time.

Today's code is short. The *decisions* are the lesson.

## 2. What you will build

A bookstore catalogue API — books, authors, stats:

```
11_rest_api_fundamentals/
├── wsgi.py
└── bookstore/
    ├── __init__.py     create_app(), API-wide conventions, CLI
    ├── api.py          the resources — this is where the design lives
    ├── errors.py       ONE error envelope for every failure
    ├── models.py       small on purpose; today is about the interface
    └── extensions.py
```

## 3. Run it

```bash
source .venv/bin/activate
cd 11_rest_api_fundamentals

FLASK_APP=wsgi.py flask seed
FLASK_APP=wsgi.py flask run --port 5011 --debug
```

```bash
curl -s http://127.0.0.1:5011/api/v1/ | python -m json.tool
```

A root document listing the endpoints costs nothing and makes the API
explorable with `curl` alone.

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:5011/api/v1

# --- reading ---
curl -s "$API/books?per_page=2"                | python -m json.tool
curl -s "$API/books?sort=-price&per_page=3"    | python -m json.tool
curl -s "$API/books?q=Earthsea"                | python -m json.tool
curl -s "$API/books?min_price=500"             | python -m json.tool
curl -s "$API/authors?include=books"           | python -m json.tool
curl -s "$API/authors/1/books"                 | python -m json.tool

# --- the guard rails ---
curl -s "$API/books?sort=colour"     | python -m json.tool   # 400 + allowed list
curl -s "$API/books?per_page=999999" | python -m json.tool   # capped at 100
curl -s "$API/books/9999"            | python -m json.tool   # 404, JSON envelope

# --- creating: watch the 201 and the Location header ---
curl -isX POST "$API/books" -H "Content-Type: application/json" \
  -d '{"isbn":"978-1-2345-6789-7","title":"New Book","price":"299.50",
       "stock":3,"published_year":2024,"author_id":1}' | head -12

# --- the four ways a write can fail, each with the right status ---
curl -sX POST "$API/books" -d 'title=x'                            # 415
curl -sX POST "$API/books" -H "Content-Type: application/json" -d '{bad'   # 400
curl -sX POST "$API/books" -H "Content-Type: application/json" \
  -d '{"title":"","price":"-5","published_year":3000,"author_id":999}'     # 422
# repeat the successful POST above                                          # 409

# --- updating and deleting ---
curl -sX PATCH "$API/books/1" -H "Content-Type: application/json" -d '{"stock":42}'
curl -isX DELETE "$API/books/1" | head -3        # 204, empty body
curl -sX POST "$API/books/1"   | python -m json.tool   # 405

# --- protocol niceties you get almost for free ---
curl -sI "$API/books"            # HEAD: headers, no body
curl -isX OPTIONS "$API/books/1" | grep -i allow
```

**Read the error bodies.** Every single one has the same shape — that is the
day's most important habit.

## 5. Resource design

URLs name **things**; the method says what you are doing to them. A verb in your
URL means you are writing RPC with extra steps.

| ✅ | ❌ |
|---|---|
| `GET /books` | `GET /getAllBooks` |
| `POST /books` | `POST /createBook` |
| `GET /books/42` | `GET /getBook?id=42` |
| `PUT /books/42` | `POST /updateBook` |
| `DELETE /books/42` | `POST /deleteBook` |

Plural collections · ids in the path · filters in the query string.

Both of these are fine, and this API offers both:

```
GET /authors/5/books        # nested: expresses ownership, natural 404
GET /books?author_id=5      # filtered: composes with other filters
```

## 6. Method semantics — the part that breaks retries

| Method | Safe | Idempotent | Meaning |
|---|---|---|---|
| `GET` | ✅ | ✅ | read; **never** changes state |
| `POST` | ❌ | ❌ | create; twice creates two |
| `PUT` | ❌ | ✅ | replace whole resource |
| `PATCH` | ❌ | ❌* | modify some fields |
| `DELETE` | ❌ | ✅ | remove |

- **Safe** = changes nothing. Browsers prefetch, crawlers follow, proxies cache
  GETs. A GET that deletes something *will* be triggered by accident.
- **Idempotent** = doing it twice leaves the same state as once. This is what
  lets a client safely retry after a timeout — and clients retry whether or not
  you designed for it.

**`PUT` vs `PATCH`** is the pair people get wrong:

```bash
PUT   /books/42   # full replacement — omitted fields are RESET
PATCH /books/42   # partial — only what you send changes
```

## 7. Status codes that carry information

| Code | Meaning | Used here for |
|---|---|---|
| `200` | OK | reads, `PUT`, `PATCH` |
| `201` | Created | `POST` — **plus a `Location` header** |
| `204` | No Content | `DELETE` — body must be **empty** |
| `400` | Bad Request | malformed JSON, bad query parameter |
| `404` | Not Found | no such resource |
| `405` | Method Not Allowed | wrong verb on a real URL |
| `409` | Conflict | duplicate ISBN |
| `415` | Unsupported Media Type | client did not send JSON |
| `422` | Unprocessable Content | valid JSON, invalid **values** |

Three distinctions worth internalising:

- **415 vs 400.** 415 = "I don't speak your format" (no/incorrect
  `Content-Type`). 400 = "I speak JSON and yours is broken". Collapsing them
  makes a very common client mistake hard to diagnose.
- **400 vs 422.** 400 = the request is malformed. 422 = it parsed fine and the
  *values* are wrong. Field errors belong in 422.
- **409 vs 422.** 409 = conflicts with existing state (duplicate key). 422 =
  the payload itself is invalid.

A correct `POST` does three things:

```http
HTTP/1.1 201 Created
Location: http://127.0.0.1:5011/api/v1/books/8

{"id": 8, "created_at": "...", ...}
```

201 (not 200) · a `Location` header so the client never guesses the URL · the
created object including server-assigned fields.

## 8. One error envelope, always

```json
{
  "error": {
    "status": 422,
    "code": "validation_error",
    "message": "The request body failed validation.",
    "details": {"title": "This field is required.",
                "author_id": "No author with id 999."}
  }
}
```

| Field | Audience |
|---|---|
| `status` | mirrors HTTP status, convenient after the response is discarded |
| `code` | **machines** — stable, never reworded |
| `message` | **humans** — safe to display, may change |
| `details` | structured extras, e.g. field errors |

**Clients must branch on `code`, never on `message`** — prose gets reworded and
translated. Three handlers in `errors.py` cover everything: `APIError` (raised
deliberately), `HTTPException` (Flask's own 404/405/413), and `Exception` (the
catch-all, so a crash still returns JSON).

> **Never put `str(exception)` in a response.** It leaks file paths, SQL
> fragments and internal hostnames. Log the detail; tell the client nothing.

## 9. Pagination

```json
{
  "data": [ ... ],
  "meta":  {"page": 1, "per_page": 20, "total": 137, "pages": 7},
  "links": {"self": "...", "next": "...?page=2&q=e", "prev": null}
}
```

| Rule | Reason |
|---|---|
| **Always** paginate collections | works with 50 rows, takes the site down at 500,000 |
| **Cap** `per_page` (100 here) | `?per_page=1000000` is a DoS you invited |
| Return `links.next` | clients stop building URLs, so you can change the scheme |
| Preserve filters in links | otherwise page 2 silently drops the search |

## 10. Filtering and sorting from an allow-list

```python
SORTABLE = {"title": Book.title, "price": Book.price, ...}

field = sort.lstrip("-")
if field not in SORTABLE:
    raise APIError(400, "bad_request", f"Cannot sort by {field!r}.",
                   details={"allowed": sorted(SORTABLE)})
```

Never interpolate a query parameter into SQL — that is injection. Mapping to
real column objects also turns an unknown field into a clean 400 (with the
allowed values, which is *kind*) rather than a 500.

## 11. Three serialisation rules

```python
"price": str(self.price),          # money as a STRING
"created_at": iso_utc(self.created_at),
"author": {"id": ..., "name": ...},
```

1. **Money crosses the wire as a string.** JSON has one numeric type (IEEE 754
   double); `12.10` can arrive as `12.099999999999999`.
2. **Timestamps carry an explicit offset.** `DateTime(timezone=True)` is a
   *request* the backend may ignore — PostgreSQL honours it, **SQLite has no
   timezone type**, so a bare `.isoformat()` emits `2026-08-20T13:06:30` with no
   offset and the client guesses wrong. `iso_utc()` normalises at the boundary
   so the contract holds on every backend. (This gap was found by checking the
   real output, not by reading the column definition.)
3. **Embed small related objects.** Returning `author_id` alone forces the
   client into N+1 *HTTP requests* — Day 08's problem, over the network. Keep
   large relations opt-in (`?include=books`).

## 12. Versioning, negotiation, CORS

```python
api_bp = Blueprint("api", __name__, url_prefix="/api/v1")
```

Version from the first commit. Once a third party depends on a URL you cannot
make a breaking change — `/v1` gives you somewhere to put `/v2` while old
clients keep working.

`406 Not Acceptable` is returned only when a client explicitly asks for a type
we cannot produce. `Accept: */*` or no header at all is fine — being stricter
breaks `curl` defaults and annoys everyone.

```python
response.headers.setdefault("X-Content-Type-Options", "nosniff")
response.headers.setdefault("Access-Control-Allow-Origin", "*")   # ⚠ learning only
```

`nosniff` stops a browser treating a JSON body as HTML — a real XSS vector when
your API echoes user input. The `*` CORS header is **permissive for learning**;
in production name the exact origins you trust and use `flask-cors`, which
handles preflight `OPTIONS` and credential rules properly.

## 13. Best practices introduced today

| Practice | Reason |
|---|---|
| Nouns in URLs, verbs as methods | that *is* REST |
| Version from commit one | you cannot un-ship a URL |
| Honour safe/idempotent semantics | caches, proxies and retries depend on it |
| `201` + `Location` + body | the client never guesses the new URL |
| `204` with a genuinely empty body | some clients choke otherwise |
| Distinguish 400 / 409 / 415 / 422 | each names a different client mistake |
| One error envelope everywhere | one parser, not five |
| Stable `code`, human `message` | prose changes; contracts must not |
| Collect **all** validation errors | one round trip, not five |
| Never leak exception text | it contains paths and internals |
| Always paginate; cap page size | uncapped collections are a DoS |
| Links preserve filters | page 2 must keep the search |
| Allow-list sorting and filtering | injection-proof, and 400 not 500 |
| Money as a string | JSON floats lose precision |
| Timestamps with an explicit offset | naive timestamps are ambiguous |
| Embed small relations, opt in to large | avoids N+1 over HTTP |
| `nosniff` on every response | contains XSS from echoed input |
| A discoverable root document | `curl` becomes the documentation |
| `Decimal(str(x))`, never `Decimal(float)` | preserves the value exactly |
| Exclude `bool` from `isinstance(x, int)` | `True` would pass as `1` |

## 14. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Client retry creates duplicates | retried a `POST` | use `PUT`, or an idempotency key |
| `PATCH` wipes unsent fields | implemented as replace | only assign keys present in the body |
| Everything returns 200 | status codes unused | 201/204/404/409/422 as appropriate |
| 404 for a bad `author_id` in a body | conflated URL with payload | 422 — the *body* is wrong |
| Client can't tell errors apart | inconsistent shapes | one envelope |
| Client matches on message text | no stable `code` | add machine-readable codes |
| API returns an HTML error page | no `HTTPException` handler | register one (see `errors.py`) |
| Timeouts on a big collection | no pagination | paginate and cap |
| `?per_page=1000000` melts the server | no cap | clamp to a maximum |
| Page 2 loses the search | filters dropped from links | thread `request.args` through |
| `0.1 + 0.2` bugs in a client | money sent as a JSON number | send a string |
| "Wrong" timestamps by 5½ hours | no UTC offset | normalise at serialisation |
| `{"stock": true}` stores 1 | `isinstance(True, int)` is `True` | exclude `bool` explicitly |
| 500 on a bad `?sort=` | interpolated user input | allow-list |
| Browser blocks the API | no CORS headers | `flask-cors`, with named origins |

## 15. Exercises

1. Add `GET /api/v1/authors/<id>` and `POST /api/v1/authors`.
2. Add `ETag` + `If-None-Match` to `GET /books/<id>` so unchanged reads return
   `304 Not Modified`.
3. Add optimistic locking: `PUT` must send `If-Match` with the current ETag, and
   gets `412 Precondition Failed` if the resource changed underneath it.
4. Support **idempotency keys** on `POST`: an `Idempotency-Key` header that
   returns the original response instead of creating a duplicate.
5. Add cursor-based pagination (`?after=<id>`) and note why it beats
   offset paging on large, actively-written tables.
6. Add `?fields=id,title` sparse fieldsets.
7. Add `flask-cors` and restrict origins to one domain.
8. Write an OpenAPI 3 document describing `/books`. Notice how much of it is
   mechanical — Day 12's Pydantic schemas can generate it.

## 16. What's next

**[Day 12 — Pydantic Validation and Schemas →](../12_pydantic_validation_and_schemas/)**
`_validate_book()` is 90 lines of hand-written type checks. Pydantic replaces it
with a declarative model — and gives you an OpenAPI schema for free.

---

<!-- nav -->
[← Day 10 — Blueprints and the Application Factory](../10_blueprints_and_app_factory/) · **[All 21 days](../README.md)** · [Day 12 — Pydantic Validation and Schemas →](../12_pydantic_validation_and_schemas/)

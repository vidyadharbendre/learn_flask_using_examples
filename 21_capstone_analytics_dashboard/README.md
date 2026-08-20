# Day 21 — Capstone: Pulse, a Feedback Analytics App

> **Goal:** put all twenty days together into one application you could actually
> deploy — and see how the techniques interact.
> **Time:** ~3 hours · **Port:** 5021 · **Builds on:** everything

---

## 1. What you are building

**Pulse** is a small but complete product:

- sign up, sign in, manage your account
- create feedback surveys with a 0–10 question
- share **one public link** — respondents need no account
- read a dashboard with NPS, averages and a score histogram
- export to CSV, or read everything through a **token-authenticated JSON API**

Techniques that were clear in isolation interact in ways only a whole
application reveals. That is the point of today.

## 2. Where each day shows up

| Day | Technique | Where to look |
|---|---|---|
| 01 | `/healthz`, never `app.run()` in prod | `__init__.py` |
| 02 | converters, `url_for`, error pages | throughout, `error.html` |
| 03 | inheritance, macros, custom filters | `templates/_macros.html` |
| 04 | POST/Redirect/GET, 303/422 | `blueprints/public.py` |
| 05 | Flask-WTF forms with CSRF | `forms.py` |
| 06 | signed sessions, flash, cookie hardening | `settings.py` |
| 07 | CSV export, layered modules, CLI | `blueprints/surveys.py` |
| 08 | relationships, cascades, `CHECK`, SQL aggregates | `models.py` |
| 09 | Alembic migrations | `migrations/` |
| 10 | factory, blueprints, config classes | `__init__.py` |
| 11 | versioned API, status codes, one envelope | `blueprints/api.py` |
| 12 | Pydantic, `extra="forbid"`, `exclude_unset` | `schemas.py` |
| 13 | hashing, session fixation, open redirect, IDOR | `blueprints/auth.py` |
| 14 | ownership chain, centralised | `security.py` |
| 15 | token auth, revocation by rotation | `security.py`, `auth.py` |
| 17 | a real test suite | `tests/` |
| 18 | typed settings, request ids, redaction | `settings.py`, `__init__.py` |
| 19 | caching with write-path invalidation, limits | `surveys.py`, `public.py` |
| 20 | gunicorn, Docker, probes | `Dockerfile`, `gunicorn.conf.py` |

## 3. Run it

```bash
source .venv/bin/activate
cd 21_capstone_analytics_dashboard

FLASK_APP=wsgi.py flask db upgrade -d migrations    # or: flask init-db
FLASK_APP=wsgi.py flask seed
FLASK_APP=wsgi.py flask run --port 5021 --debug
```

`flask seed` prints what you need:

```
Sign in: ana@example.com / CorrectHorseBattery1
API token: knSiUAKA_EJk1zRNsMm2zgB8eZmj6q23jiMXUJTR358

/s/sbMUAbtzqkavmYgC  Onboarding experience (open)
/s/7qLG8J6EQWliQGLU  Support quality (open)
/s/s99hGfs9UYgYA9Ht  Q4 pricing research (draft)
```

Production-style, with a real WSGI server:

```bash
APP_SECRET_KEY=$(python -c "import secrets;print(secrets.token_hex(32))") \
  gunicorn --config gunicorn.conf.py wsgi:app
```

```bash
pytest              # 43 tests
```

## 4. Try it — learn by doing

### The full loop

1. Sign in and open a survey. Copy its **public link**.
2. Open that link in a private window — no account needed — and submit a score.
3. Back on the dashboard, watch the histogram and NPS update.
4. Download the CSV.

### The API

```bash
B=http://127.0.0.1:5021
T="<paste your API token>"

curl -s $B/api/v1/ -H "Authorization: Bearer $T" | python -m json.tool
curl -s $B/api/v1/surveys -H "Authorization: Bearer $T" | python -m json.tool
curl -s $B/api/v1/surveys/2/stats -H "Authorization: Bearer $T"
```

```json
{"average":8.12,"detractors":4,"nps":33,"passives":8,"promoters":12,"total":24}
```

`(12 − 4) / 24 × 100 = 33`. The dashboard, the API and the CSV all read the same
`summarise()` function, so they cannot disagree.

### Attack it

```bash
# no token
curl -s -o /dev/null -w "%{http_code}\n" $B/api/v1/surveys                    # 401

# mass assignment — try to own someone else's survey
curl -s -X POST $B/api/v1/surveys -H "Authorization: Bearer $T" \
  -H "Content-Type: application/json" \
  -d '{"title":"Sneaky","question":"Can I set the owner?","owner_id":2,"id":999}'
# 422: {"owner_id": ["Extra inputs are not permitted"], "id": [...]}

# a boolean score
curl -s -X POST $B/api/v1/surveys/2/responses -H "Authorization: Bearer $T" \
  -H "Content-Type: application/json" -d '{"score":true}'                     # 422

# open redirect
curl -si -X POST "$B/auth/login?next=https://evil.example" \
  -d "email=ana@example.com&password=CorrectHorseBattery1" | grep -i location
# Location: /surveys/     ← the hostile target is discarded

# a draft survey is not publicly reachable
curl -s -o /dev/null -w "%{http_code}\n" $B/s/s99hGfs9UYgYA9Ht                # 404
```

Then sign in as `vik@example.com` and try to open Ana's survey by id. **404** —
not 403, because 403 would confirm it exists.

```bash
pytest tests/test_authorization.py -v
```

Those nine tests are the most valuable in the suite.

## 5. Design decisions worth studying

### The public slug is random, not the id

```python
slug: Mapped[str] = mapped_column(String(24), unique=True, index=True)
```

The public URL is shared with strangers. A sequential id invites walking
`/s/1`, `/s/2`, `/s/3` to enumerate every survey in the system — Day 13's IDOR
lesson applied to a deliberately *public* resource.

### Respondents are hashed, never stored

```python
raw = f"{current_app.config['SECRET_KEY']}:{request.remote_addr}"
return hashlib.sha256(raw.encode()).hexdigest()[:32]
```

The `SECRET_KEY` is the salt, and that matters: an **unsalted** hash of an IPv4
address is trivially reversible — there are only four billion of them, so a
rainbow table is minutes of work. A "hashed" identifier that can be reversed is
not anonymised at all.

### Two auth systems, deliberately

| | Web UI | JSON API |
|---|---|---|
| Credential | signed session cookie | `Authorization: Bearer …` |
| CSRF | **required** | **not applicable** |
| Revocation | `session.clear()` | rotate the token |

CSRF exists because browsers attach **cookies** automatically. They never attach
an `Authorization` header — which is why `csrf.exempt(api_bp)` is correct here
and would be a serious bug on the cookie-authenticated HTML routes.

Note the token design differs from Day 15's JWT: an **opaque random token** with
a database lookup. The trade-off is explicit — a lookup per request, in exchange
for revocation being a single `UPDATE` with no blocklist and no `token_version`.

### CSRF stays on for the *public* form

The response form is unauthenticated, and still carries a CSRF token. "Public"
is not a reason to drop it — only header-based auth is. It costs nothing and
stops a third-party page submitting on a visitor's behalf.

### Cache invalidation happens in the write path

```python
survey.title = form.title.data
db.session.commit()
cache.delete_memoized(public_survey_payload, survey.slug)   # ← right here
```

Not on a timeout, not on a guess — at the moment the data changes (Day 19 §5).
Note also that only the **immutable-per-edit** parts are cached; the response
count never is, because it changes with every submission.

### Enums are Jinja globals, not context-processor values

```python
app.jinja_env.globals.update(SurveyStatus=SurveyStatus)
```

Macros are **isolated from the caller's context**, so a context processor's
values are invisible inside them. Globals live on the environment and are not
(Day 14 §8). `request_id` deliberately stays a context processor — it genuinely
varies per request.

## 6. A bug the tests caught while writing this

`test_api_errors_share_one_envelope` asserts every API error has the same shape:

```python
assert {"status", "code", "message"} <= set(body["error"])
```

It **failed**. `token_required` returned `{"code", "message"}` without `status` —
one inconsistent shape out of a dozen, invisible by inspection, and exactly what
forces a client to write a second parser (Day 11 §8).

The fix was in the code, not the test. That is what a test suite is *for*: it
noticed a small inconsistency that a human reviewer would skim straight past.

## 7. The architecture

```
                       ┌──────────────┐
   browser ──cookie──▶ │  auth        │──┐
                       │  surveys     │  │
   stranger ─────────▶ │  public      │  ├─▶ security.py ─▶ models.py ─▶ DB
                       │              │  │   (ownership)     (schema)
   API client ─token──▶│  api         │──┘
                       └──────────────┘
                              │
                    schemas.py (Pydantic)
                    settings.py (typed config)
```

Every request passes through **one** authorisation module. Adding a route
cannot accidentally skip the check, because there is only one function to call —
which is the whole reason Day 14 extracted it.

## 8. Deploying

```bash
export APP_SECRET_KEY=$(python -c "import secrets;print(secrets.token_hex(32))")
docker compose up --build
```

The compose file wires Postgres, Redis (for **both** the cache and the rate-limit
counters, so `gunicorn -w 4` does not give you four independent caches and 4× the
intended limit), and nginx in front.

Production refuses to boot when misconfigured:

```bash
APP_ENV=production flask run
#   Configuration error — cannot start:
#     Unsafe production config: APP_SECRET_KEY is still the development default
```

> **Verification note:** the app, tests, migrations, gunicorn and graceful
> shutdown were all exercised directly. The **`docker build` was not run** here —
> no Docker daemon was available in this environment. The compose YAML was
> parsed; treat the image build as reviewed-but-unexecuted.

## 9. What is deliberately missing

An honest capstone names its gaps:

| Missing | Where it would go |
|---|---|
| Email verification & password reset | Day 13, exercises 2 and 4 |
| File uploads (logo, CSV import) | Day 16 |
| Background jobs (scheduled digests) | Day 19, exercise 6 |
| Team accounts / shared surveys | Day 14, exercise 2 |
| Real error tracking (Sentry) | Day 18, exercise 1 |
| CI pipeline | Day 20, exercise 6 |

Each is a genuine exercise, and each one you complete makes this a real product.

## 10. Exercises

1. **Email verification.** Register inactive, email a signed `itsdangerous`
   token, activate on click.
2. **Team surveys.** Add a `SurveyMember` table; update `security.py` so members
   read and only the owner deletes. Note every view already funnels through one
   function.
3. **CSV import** of historical responses, applying Day 16's rules — sniff the
   content, never trust the filename.
4. **A weekly digest** as a background job (Day 19), emailing each owner their
   NPS trend.
5. **Trend over time**: `GET /api/v1/surveys/<id>/trend?period=week`, aggregated
   with `GROUP BY` in SQL rather than in Python.
6. **Rate-limit the public form per survey**, not just per IP, so one popular
   survey cannot exhaust another's allowance.
7. **Push coverage past 90%** with `pytest --cov=analytics --cov-report=term-missing`
   — then re-read Day 17 §8 on why that number is not the goal.
8. **Deploy it** somewhere real (Fly.io, Railway, a VPS) and put a domain and
   TLS in front of it.

## 11. You have finished the 21 days

Look back at Day 01: eleven lines returning `"Hello, Flask!"`. This app has
authentication, authorisation, a validated API, migrations, caching, rate
limiting, structured logging and a test suite — and every part of it is
something you built a small version of first.

The ideas worth carrying to the next framework, because none of them are really
about Flask:

1. **Never trust the client.** Not the filename, the content type, the hidden
   field, the `?next=` parameter, or the JSON body.
2. **Validate at the boundary**, once, and make everything downstream able to
   assume correctness.
3. **Authorisation belongs in the query**, not the template.
4. **Fail loudly at start-up** rather than quietly at 3am.
5. **Measure before optimising** — a broken cache looks exactly like a working one.
6. **Test behaviour, not lines.** Coverage is a smoke detector, not a fire alarm.
7. **Make the failure mode you can detect** the one you choose.

### Where to go next

- **Async**: Quart, or Flask 3's async views — and learn where they help
  (I/O-bound fan-out) and where they do not (CPU-bound work).
- **FastAPI**: the Pydantic-first framework. Day 12 is most of the mental model.
- **Scale**: read-replicas, connection pooling, `EXPLAIN ANALYZE`.
- **Observability**: OpenTelemetry traces, building on Day 18's request ids.
- **Frontend**: HTMX pairs beautifully with server-rendered Flask.

---

**[← Back to the course index](../README.md)**

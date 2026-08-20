# Day 18 — Config, Logging and Error Handling

> **Goal:** make an app that is safe to deploy and possible to debug — typed
> settings that fail loudly, structured logs with request ids, and errors that
> tell *you* everything and the client nothing.
> **Time:** ~90 minutes · **Port:** 5018 · **Builds on:** Day 10

---

## 1. Why this matters

The same image runs on your laptop, in staging and in production. Only the
environment differs — and when something breaks at 3am, the logs are your only
witness.

Day 10's config classes were an improvement, but they had three weaknesses:

| Weakness | Consequence |
|---|---|
| Everything is a `str` | `PORT + 1` is a `TypeError` at some unlucky moment |
| Typos are silent | `APP_DATABSE_URL` → boots on the default, wrong database |
| Failure is late and vague | `NoneType has no attribute…` deep inside a request |

## 2. What you will build

An instrumented service demonstrating each idea in isolation.

```
18_config_logging_and_errors/
├── .env.example              every variable, documented
├── wsgi.py
└── observe/
    ├── settings.py           typed config; refuses to boot when unsafe
    ├── logging_setup.py      request ids, JSON/text formats, redaction
    └── __init__.py           demo endpoints, error handlers, probes
```

## 3. Run it

```bash
source .venv/bin/activate
cd 18_config_logging_and_errors
cp .env.example .env

FLASK_APP=wsgi.py flask run --port 5018
```

Then vary the environment and watch the behaviour change:

```bash
APP_LOG_FORMAT=json APP_LOG_LEVEL=DEBUG flask run --port 5018
APP_ENV=production flask run --port 5018             # refuses to boot
APP_DATABSE_URL=oops flask run --port 5018           # typo caught at start-up
```

## 4. Try it — learn by doing

```bash
S=http://127.0.0.1:5018

curl -s $S/ | python -m json.tool
curl -s $S/config | python -m json.tool          # secrets redacted
curl -i $S/healthz | grep -i x-request-id        # every response carries one

# Secrets are in the RESPONSE (they came from you) and NOT in the log
curl -s -X POST $S/echo -H "Content-Type: application/json" \
  -d '{"email":"ana@example.com","password":"hunter2","api_key":"sk-live-123"}' \
  | python -m json.tool

curl -s "$S/slow?ms=800"     # logged as WARNING, over the threshold
curl -s $S/fail/404          # logged as WARNING
curl -s $S/boom              # full traceback in the log, generic message to you
```

### Correlation across services

```bash
curl -is $S/ -H "X-Request-ID: upstream-trace-abc123" | grep -i x-request-id
```

```
2026-08-20 19:36:55 INFO [upstream-trace-abc123] app.request: GET / -> 200
```

The inbound id is **reused**, so one user action can be followed across every
service it touches.

### CLI

```bash
FLASK_APP=wsgi.py flask check-config   # what is this box actually running?
FLASK_APP=wsgi.py flask new-secret     # generate a strong key
```

## 5. Twelve-factor configuration

> **Config lives in the environment, not in code.**

Anything that varies between deployments — database URL, secret key, log level,
feature flags — is config. Anything that does not is code.

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_")

    env: Literal["development", "testing", "production"] = "development"
    slow_request_ms: int = Field(default=500, ge=1, le=60_000)
    log_level: Literal["DEBUG","INFO","WARNING","ERROR","CRITICAL"] = "INFO"
```

Values are **parsed and validated at start-up**, and every problem is reported
at once:

```bash
$ APP_LOG_LEVEL=CHATTY APP_SLOW_REQUEST_MS=notanumber flask run

  Configuration error — the application cannot start:

    APP_LOG_LEVEL: Input should be 'DEBUG', 'INFO', 'WARNING', 'ERROR' or 'CRITICAL'
    APP_SLOW_REQUEST_MS: Input should be a valid integer, unable to parse string
```

## 6. Fail fast, and loudly

```python
@model_validator(mode="after")
def production_must_be_locked_down(self) -> "Settings":
    if self.env != "production":
        return self
    problems = []
    if self.secret_key == "dev-only-not-for-production":
        problems.append("APP_SECRET_KEY is still the development default")
    if self.debug:
        problems.append("APP_DEBUG is true — remote code execution")
    if self.database_url.startswith("sqlite"):
        problems.append("SQLite cannot serve concurrent writers")
    if problems:
        raise ValueError("Unsafe production configuration: " + "; ".join(problems))
```

A production app on the default secret key means every session cookie in the
world can be forged, **and nothing in the logs would say so**.

> A deploy that fails loudly is a problem. One that succeeds quietly and
> insecurely is an incident.

### The gap `extra="forbid"` does not close

`extra="forbid"` rejects unknown keys passed to the **constructor**. It does
**not** reject unknown environment variables — pydantic-settings ignores those.
Verified against pydantic-settings 2.6:

```bash
$ APP_DATABSE_URL=postgresql://…  flask run     # note the typo
Starting in development mode                    # ← booted on the SQLite default
```

Everything *works*, just against the wrong backend — which is the hardest kind
of bug to notice. So `check_unknown_env_vars()` does it explicitly:

```bash
$ APP_DATABSE_URL=oops flask run

  Configuration error — Unknown environment variable(s): APP_DATABSE_URL
  (a typo here would otherwise boot silently on the default)
```

Ten lines to eliminate a whole class of silent failure.

## 7. `.env` files

| | `.env` | `.env.example` |
|---|---|---|
| Contains | real local values | placeholders + comments |
| Committed | **never** | **yes** |
| Used in production | **never** | no |

Production uses real environment variables injected by your platform or a secret
manager (Vault, AWS Secrets Manager, Kubernetes Secrets, Doppler). Committing
`.env.example` is good practice: it documents every variable the app needs.

## 8. Logging

`print()` has no level, no timestamp, no source, no structure, and cannot be
turned down in production or up during an incident.

A log line must answer five questions:

| | |
|---|---|
| **When** | ISO 8601, in **UTC** — local time cannot be correlated across regions |
| **How bad** | a level, so you can filter |
| **Where** | logger name, module |
| **What** | the message |
| **Which one** | a **request id** |

That last one is the one people skip, and the one that matters most. Under
concurrency, interleaved lines from twenty simultaneous requests are unreadable
without a correlation id.

### Levels, decided in advance

| Level | Meaning |
|---|---|
| `DEBUG` | development detail; usually off in production |
| `INFO` | normal notable events |
| `WARNING` | unexpected but handled: retry, slow query, fallback |
| `ERROR` | this request failed; a human should look |
| `CRITICAL` | the process cannot continue |

The commonest mistake is logging everything at `INFO`, which makes the level
useless as a filter and buries the two lines that mattered. This app picks the
level **from the outcome**:

```python
if response.status_code >= 500:    level = ERROR
elif status >= 400 or slow:        level = WARNING
else:                              level = INFO
```

### Text for humans, JSON for machines

```
2026-08-20 19:36:27 INFO [15efaa36cbb6] app.request: GET / -> 200 in 0.1ms
```

```json
{"ts":"2026-08-20T14:06:27.542Z","level":"INFO","logger":"app.request",
 "message":"GET / -> 200","request_id":"201c2fe2e057","status":200,
 "duration_ms":0.2,"slow":false}
```

Aggregators parse structured lines into **queryable fields**:
`request_id:"abc"` is a filter; the same value inside prose is a substring
search across terabytes.

### Two implementation notes

- **`dictConfig` configures the root logger**, so Flask, Werkzeug, SQLAlchemy
  and your own modules share one destination and format. Configuring
  `app.logger` alone leaves every library logging somewhere else.
- **`disable_existing_loggers: False`** — the default (`True`) silently mutes
  loggers created before your call, including ones libraries create at import.
- A `logging.Filter` is the idiomatic way to *enrich* records. It must check
  `has_request_context()`, because logs are also emitted at start-up and from
  CLI commands, where touching `request` raises. **A logging call must never
  crash the thing it is reporting on.**

## 9. What must never reach a log

```python
logger.info("login", extra={"body": request.form})           # ❌ plaintext password
logger.info("login", extra={"body": scrub(request.form)})    # ✅
```

```
"body": {"email":"ana@example.com", "password":"***REDACTED***",
         "api_key":"***REDACTED***", "nested":{"csrf_token":"***REDACTED***"}}
```

Logging `request.form` wholesale is how plaintext passwords end up in a
retained, searchable log — **a breach that password hashing does nothing to
prevent**, because the value was written to disk before it ever reached the
hashing code.

The check is a substring match, so `user_password` and `api_key_v2` are caught,
and it recurses into nested dicts. Never log a settings object directly either —
its `repr` contains your secret key.

## 10. Errors: everything to you, nothing to the client

```python
logger.exception("Unhandled %s on %s %s", type(error).__name__, method, path)
return jsonify(error={"status": 500,
                      "message": "An unexpected error occurred.",
                      "request_id": g.request_id}), 500
```

```
STATUS: 500
BODY:   {"message":"An unexpected error occurred.","request_id":"d5034d7d73a3",...}
leaks 'ZeroDivisionError'? False
leaks a file path?         False
```

- `logger.exception` logs at ERROR **with the traceback**. Never
  `logger.error(str(e))` — that throws away the part you will want most.
- Never put `str(error)` in a response: it leaks paths, SQL fragments, internal
  hostnames, sometimes credentials — and it is exactly what an attacker probes.
- The **request id bridges the two**: the user quotes it, you search for it, and
  you have the traceback in seconds.

## 11. Liveness vs readiness — two different questions

| Probe | Question | Failure means | Checks dependencies? |
|---|---|---|---|
| `/healthz` | is the process alive? | **restart me** | ❌ no |
| `/readyz` | can I serve traffic? | **skip me for now** | ✅ yes |

A liveness probe that checks the database turns a brief DB blip into a **restart
storm across your whole fleet** — because restarting your app does not fix
someone else's database. A readiness probe failing simply removes the instance
from the load balancer; it rejoins automatically when the dependency recovers.

Confusing these two is among the most common Kubernetes misconfigurations.

## 12. Best practices introduced today

| Practice | Reason |
|---|---|
| Typed settings validated at start-up | wrong config is a boot failure, not a 3am mystery |
| Report **all** config errors at once | one restart, not five |
| Refuse unsafe production config | a silent insecure boot is worse than a crash |
| Detect unknown `APP_*` variables | a typo must not become a silent default |
| `.env` for dev, real env vars in prod | secrets never live in the repo |
| Commit `.env.example` | documents every required variable |
| `dictConfig` on the root logger | one format for your code *and* libraries |
| `disable_existing_loggers: False` | avoids silently muting library loggers |
| Request ids, reused from inbound headers | correlation across services |
| Echo the id in a response header | users can quote it |
| Pick the level from the outcome | keeps levels meaningful |
| UTC timestamps | the only sane choice in a distributed system |
| JSON in production, text in dev | queryable vs readable |
| `scrub()` before logging any payload | prevents a searchable password leak |
| `logger.exception`, not `.error(str(e))` | keeps the traceback |
| Generic 500 bodies | no path, SQL or hostname leakage |
| Rotating file handlers | an unrotated log fills the disk |
| Separate `/healthz` and `/readyz` | restart vs remove-from-pool |
| Log config once at boot, redacted | answers "what is this box running?" |

## 13. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Works locally, breaks deployed | config hardcoded | move it to the environment |
| App uses the wrong database | env var typo | `check_unknown_env_vars()` |
| Production runs on the dev key | no start-up check | fail fast |
| `Working outside of request context` in a log filter | no `has_request_context()` guard | add it |
| Logs interleaved unreadably | no request id | add one |
| Two different log formats | only `app.logger` configured | use `dictConfig` |
| A library's logs vanish | `disable_existing_loggers: True` | set it `False` |
| Passwords in the log aggregator | logged `request.form` | `scrub()` |
| Cannot debug a production 500 | logged `str(e)` without the traceback | `logger.exception` |
| Attacker learns your file paths | exception text in the response | generic message |
| Disk full at 4am | file handler with no rotation | `RotatingFileHandler` |
| Whole fleet restarts during a DB blip | liveness probe checks the DB | move it to readiness |
| Everyone logged out on deploy | regenerated `SECRET_KEY` | stable key from the environment |

## 14. Exercises

1. Add `APP_SENTRY_DSN` and wire up `sentry-sdk` when it is set. Note how the
   request id can be attached as a Sentry tag.
2. Add a `/metrics` endpoint (`prometheus-client`) with request counts and
   latency histograms.
3. Propagate the request id to an outbound `requests` call so a downstream
   service logs the same id. This is distributed tracing in miniature.
4. Make `/readyz` actually check the database, and confirm it returns 503 when
   the URL is wrong while `/healthz` stays 200.
5. Add `APP_LOG_SAMPLE_RATE` so only a fraction of `INFO` request logs are
   emitted under load — and explain why `ERROR` must never be sampled.
6. Write tests for `Settings`: valid, invalid, and unsafe-production. They need
   no app at all (Day 17).
7. Add a `Settings` field with a secret type and confirm its `repr` is masked
   even when someone logs the object by accident.

## 15. What's next

**[Day 19 — Caching, Rate Limiting and Background Jobs →](../19_caching_rate_limiting_and_jobs/)**
Making it fast and keeping it up: caching, limits, timeouts, and work that
should not happen inside a request.

---

<!-- nav -->
[← Day 17 — Testing with pytest](../17_testing_with_pytest/) · **[All 21 days](../README.md)** · [Day 19 — Caching, Rate Limiting and Background Jobs →](../19_caching_rate_limiting_and_jobs/)

# Day 19 — Caching, Rate Limiting and Background Jobs

> **Goal:** make it fast and keep it up — caching that measurably works, limits
> that bound what anyone can ask, and work moved off the request path.
> **Time:** ~2 hours · **Port:** 5019 · **Builds on:** Days 11, 18

---

## 1. Why this matters

Three questions, three techniques:

| Question | Technique |
|---|---|
| "Why is this so slow?" | **caching** — don't repeat expensive work |
| "Why is one client melting my server?" | **rate limiting** — bound what anyone can ask |
| "Why does this request take ten seconds?" | **background jobs** — get work off the request path |

Every claim below is a **number**. The demo API reports upstream call counts and
elapsed milliseconds, because *"is my cache working?"* is not a matter of
opinion — and a misconfigured cache key produces a cache that looks fine and
never hits.

## 2. Run it

```bash
source .venv/bin/activate
cd 19_caching_rate_limiting_and_jobs
FLASK_APP=wsgi.py flask run --port 5019
```

Start with the benchmark:

```bash
FLASK_APP=wsgi.py flask benchmark
```

```
  uncached (upstream every time):
      361.3 ms
      360.7 ms
      360.8 ms

  cached (first call warms it):
      353.8 ms  MISS
        0.9 ms  HIT
        0.8 ms  HIT

  upstream calls for 6 requests: 4
```

**400× faster on a hit**, and two of six requests never touched the upstream.

## 3. Try it — learn by doing

```bash
P=http://127.0.0.1:5019

curl -s -X POST $P/stats/reset
curl -s $P/cached/Mumbai > /dev/null
curl -s $P/cached/Mumbai > /dev/null
curl -s $P/cached/Mumbai > /dev/null
curl -s $P/stats | python -m json.tool     # → {"Mumbai": 1}   three requests, one call

# HTTP caching: the second request transfers ZERO bytes
ETAG=$(curl -si $P/etag/Chennai | grep -i '^etag' | cut -d' ' -f2 | tr -d '\r')
curl -si $P/etag/Chennai -H "If-None-Match: $ETAG" | head -1     # 304 Not Modified

# Rate limiting
for i in $(seq 1 7); do curl -s -o /dev/null -w "%{http_code} " $P/limited; done; echo
# 200 200 200 200 200 429 429

# A flaky upstream: retries, then serves stale rather than failing
curl -s "$P/resilient/Kolkata?fail_rate=0"   > /dev/null   # prime the cache
curl -s "$P/resilient/Kolkata?fail_rate=1.0" | python -m json.tool

# Background job: 202 now, result later
JOB=$(curl -s -X POST $P/reports -H "Content-Type: application/json" \
       -d '{"cities":["Bengaluru","Mumbai","Delhi"]}')
echo $JOB | python -m json.tool
curl -s $P/jobs/$(echo $JOB | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
```

## 4. `cached` vs `memoize`

```python
@app.get("/cached/<city>")
@cache.cached(timeout=30)          # caches the RESPONSE, keyed by request.path
def cached(city): ...

@cache.memoize(timeout=30)         # caches the FUNCTION, keyed by its ARGUMENTS
def weather_for(city): ...
```

| | `@cache.cached` | `@cache.memoize` |
|---|---|---|
| Decorates | a **view** | **any function** |
| Key | the request | the arguments |
| Shared with | that view only | every caller — views, CLI, jobs |

**Memoize is usually what you want**, because it caches the *expensive thing*
rather than one particular presentation of it.

### The `@cache.cached` footgun

> **`@cache.cached` ignores the query string by default.**

`?units=f` and `?units=c` would share one entry and serve each other's data.
Fix it explicitly:

```python
@cache.cached(timeout=30, query_string=True)
```

Verified: four requests across two distinct query strings → **two** upstream
calls, not one.

## 5. Invalidation is the hard half

```python
cache.delete_memoized(weather_for, city)     # same function AND same arguments
```

`delete_memoized` needs the arguments **because that is what the key was built
from**. Get them wrong and you clear the wrong entry — silently.

Rules of thumb:

- Invalidate in the **write path**, at the moment the underlying data changes.
- A cache cleared only by timeout serves stale data for exactly that long. That
  may be fine — as long as you *chose* it.
- `cache.clear()` is a blunt instrument: on a shared Redis it wipes other
  applications' keys, and clearing everything under load invites a **stampede**
  (every client misses at once and they all hit the upstream together).

## 6. The backend decides whether any of this is real

```python
CACHE_TYPE = "SimpleCache"          # a per-process dictionary
RATELIMIT_STORAGE_URI = "memory://" # per-process counters
```

Both are fine for learning and **wrong in production**:

| | With `gunicorn -w 4` |
|---|---|
| `SimpleCache` | **four independent caches** — hit rate collapses, workers disagree, all lost on deploy |
| `memory://` limits | each worker enforces its own, so `"10 per minute"` actually allows **40** |

Redis fixes both, and nothing else in the code changes. Flask-Limiter warns
loudly about in-memory storage in production, and it is right to.

## 7. HTTP caching — the cache you don't run

```python
response.set_etag(version)
response.headers["Cache-Control"] = "public, max-age=30"
```

```
first: 200, 102 bytes, ETag "8d49188a79886223"
again: 304,   0 bytes
```

The **client** keeps this cache. Browsers, CDNs and reverse proxies honour it
automatically, and it composes with your server-side cache rather than replacing
it:

- server cache saves **computation**
- HTTP cache saves **transfer**

| `Cache-Control` | Who may store it |
|---|---|
| `public` | CDNs and proxies too |
| `private` | only the end client — **use for per-user data** |
| `no-store` | nobody — use for anything sensitive |

Getting `private` versus `public` wrong on a personalised page is how one user's
data gets served to another from a CDN.

## 8. Rate limiting

```python
limiter = Limiter(key_func=get_remote_address)   # "per what?"
limiter.default_limits = ["120 per minute"]      # every route, unless exempt

@limiter.limit("3 per 10 seconds;20 per hour")   # burst AND sustained
```

- **A default limit is the right way round**: a new endpoint is protected unless
  someone deliberately opts out with `@limiter.exempt`.
- **Two limits together** is usually what you want — brief bursts are normal,
  sustained load is not.
- **Prefer a per-user key** in an authenticated API. Several users behind one
  office NAT share an IP, and limiting them together punishes the innocent.
- **Exempt your polling endpoints** (`/jobs/<id>`), or clients get rate-limited
  out of collecting their own results.

Always answer helpfully:

```json
{"error": {"code": "rate_limited", "retry_after_seconds": 60}}
```

A 429 without `Retry-After` invites a tight retry loop, making the problem
worse. Flask-Limiter also sends `X-RateLimit-Limit/Remaining/Reset`.

**What to limit first:** expensive endpoints (search, export, reports) and
security-sensitive ones (login, password reset, token issue).

## 9. Timeouts and retries

```python
for attempt in range(3):                     # 1. BOUNDED
    try:
        return fetch(...)                    #    (with a timeout — Day 17 §7)
    except UpstreamError:
        time.sleep(0.05 * (2 ** attempt))    # 2. exponential backoff

stale = cache.get(f"last_good:{city}")       # 3. degrade, don't fail
if stale: return stale + {"source": "stale-cache"}
```

1. **Bound the retries.** Retrying forever turns an upstream blip into *your*
   outage, and a retry storm can stop the upstream recovering at all.
2. **Back off**, and in production add **jitter** so a thousand clients do not
   retry in lockstep.
3. **Only retry what is safe to repeat.** `GET` is idempotent (Day 11); retrying
   a payment `POST` charges twice.

Serving stale data on failure is often better than an error — weather from two
minutes ago beats no weather. Make it a deliberate product decision.

## 10. Background jobs

```python
job = runner.submit("build_report", build_report, cities)
return jsonify({**job.to_dict(), "poll": f"/jobs/{job.id}"}), 202, \
       {"Location": f"/jobs/{job.id}"}
```

**202 Accepted** means "I have taken responsibility for this; here is where to
check on it."

Why it matters concretely: **a WSGI worker is occupied for the whole request.**
Four workers and a ten-second view = 0.4 requests/second, and the fifth visitor
waits. Moving that work off the request path is usually the largest single
performance win available.

### This implementation is honest about its limits

| | This thread pool | Celery / RQ |
|---|---|---|
| Survives a restart | ❌ jobs lost | ✅ stored in a broker |
| Retries on failure | ❌ | ✅ with backoff |
| Scales past one process | ❌ | ✅ |
| Scheduling | ❌ | ✅ |
| Extra infrastructure | none | a broker to run |

Use threads for genuinely fire-and-forget work in a small app. Use a real queue
the moment losing a job would matter — and *"did the receipt email actually
send?"* is a question you will be asked.

Two details that make the thread pool usable at all:

- **The pool is bounded.** An unbounded `threading.Thread(...)` per request
  means a traffic spike spawns thousands of threads and the process dies. A
  bounded pool queues instead.
- **Exceptions are caught and recorded.** An exception escaping a pool worker is
  swallowed by the executor — the job would sit in `running` forever with no
  trace. Verified: a failing job reports `failed | ValueError: deliberate`.

> Background code has **no request context**. It cannot touch `request`,
> `session` or `g`; pass everything it needs as arguments.

## 11. Best practices introduced today

| Practice | Reason |
|---|---|
| Measure before and after | a broken cache looks identical to a working one |
| `memoize` for expensive functions | shared by every caller |
| `query_string=True` when output varies | otherwise entries collide silently |
| Invalidate in the write path | timeouts alone serve known-stale data |
| Shared cache backend in production | per-worker caches destroy the hit rate |
| Shared rate-limit storage | otherwise N workers allow N× the limit |
| Default limits, explicit exemptions | new endpoints are protected by default |
| Burst + sustained limits together | matches how real traffic behaves |
| Per-user keys where possible | NAT means IP ≠ user |
| Exempt polling endpoints | do not rate-limit clients out of their results |
| `Retry-After` on every 429 | prevents tight retry loops |
| ETag + `Cache-Control` | saves transfer, not just computation |
| `private` for per-user responses | avoids CDN cross-user leaks |
| Bounded retries with backoff | prevents retry storms |
| Serve stale on upstream failure | degrade rather than fail |
| Never retry non-idempotent writes | double charges |
| 202 + `Location` for slow work | frees the worker immediately |
| Bounded worker pool | a spike must not exhaust memory |
| Record job failures explicitly | executors swallow exceptions |
| Lock shared counters | concurrent `+= 1` loses updates |

## 12. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Cache "works" but nothing is faster | key includes something per-request | inspect the key |
| Two query strings share a response | `@cache.cached` ignores it by default | `query_string=True` |
| Users see each other's data | cached a per-user response publicly | include the user in the key; `private` |
| Hit rate collapses in production | per-process `SimpleCache` | Redis |
| Limit allows 4× what you set | per-worker `memory://` storage | shared storage |
| Legitimate users blocked together | per-IP key behind NAT | per-user key |
| Clients hammer you after a 429 | no `Retry-After` | send it |
| Upstream outage becomes your outage | unbounded retries | bound + back off |
| Duplicate charges | retried a `POST` | only retry idempotent calls |
| Requests hang forever | no timeout on an external call | always set one |
| Server dies under a spike | unbounded thread creation | bounded pool |
| A job is stuck in `running` | exception swallowed by the executor | catch and record |
| Jobs vanish on deploy | in-memory queue | Celery/RQ with a broker |
| `TypeError: takes either args or kwargs` | `jsonify(some_dict, extra=1)` | merge into one dict |
| Everything stampedes after a cache clear | mass simultaneous miss | stagger TTLs; lock on miss |

## 13. Exercises

1. Switch to Redis (`CACHE_TYPE=RedisCache`, `RATELIMIT_STORAGE_URI=redis://…`),
   run `gunicorn -w 4`, and compare `/stats` before and after.
2. Prevent a **cache stampede**: on a miss, take a lock so only one request
   fetches while the others wait for the result.
3. Add jitter to the retry backoff and explain what it prevents.
4. Add `Last-Modified` / `If-Modified-Since` alongside the ETag.
5. Add a **circuit breaker**: after five consecutive upstream failures, stop
   calling for 30 seconds and serve stale immediately.
6. Replace the thread pool with **RQ**, and note what you gain (persistence,
   retries) and what it costs (a broker to run).
7. Cache per user and prove with a test that user A never sees user B's cached
   response. This is the caching bug with the worst consequences.

## 14. What's next

**[Day 20 — Docker and Production Deployment →](../20_docker_and_production_deploy/)**
`app.run()` is not a production server. Gunicorn, a Dockerfile, a reverse proxy,
and the security headers that belong in front of everything.

---

<!-- nav -->
[← Day 18 — Config, Logging and Error Handling](../18_config_logging_and_errors/) · **[All 21 days](../README.md)** · [Day 20 — Docker and Production Deployment →](../20_docker_and_production_deploy/)

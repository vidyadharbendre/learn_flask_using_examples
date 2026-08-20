# Day 20 — Docker and Production Deployment

> **Goal:** ship it — gunicorn instead of `app.run()`, a small non-root
> container, a reverse proxy, security headers, and a deploy that does the steps
> in the right order.
> **Time:** ~2 hours · **Port:** 5020 (app) / 8080 (via nginx) · **Builds on:** Day 18

---

## 1. Why `app.run()` is not a production server

Werkzeug's development server is **explicitly documented as not for
production**, and prints a warning every time it starts. It is single-process by
default, with no request timeouts, no graceful shutdown and no worker recycling.
It exists to reload your code when you save a file.

A production WSGI server gives you:

| Feature | Why it matters |
|---|---|
| Multiple workers | one slow request doesn't block everyone |
| Request timeouts | a hung request is killed, not left holding a worker |
| Graceful reload | a deploy doesn't drop in-flight requests |
| Worker recycling | papers over slow memory leaks |

## 2. What you will build

```
20_docker_and_production_deploy/
├── Dockerfile            multi-stage, non-root, cache-friendly
├── .dockerignore         written BEFORE the Dockerfile
├── docker-compose.yml    app + postgres + redis + nginx
├── gunicorn.conf.py      every setting, with the reasoning
├── nginx/default.conf    TLS, static files, slow-client buffering
├── deploy.sh             the steps, in the order that matters
└── shipit/               the app: ProxyFix, security headers, probes
```

## 3. Run it

### Locally with gunicorn

```bash
source .venv/bin/activate
cd 20_docker_and_production_deploy
SECRET_KEY=dev GUNICORN_WORKERS=3 gunicorn --config gunicorn.conf.py wsgi:app
```

```bash
for i in 1 2 3 4; do curl -s http://127.0.0.1:8000/ | python -m json.tool | grep pid; done
```

```
"pid": 32155
"pid": 32154
"pid": 32155
"pid": 32154
```

The **pid changes** — that is load spreading across workers. Under `flask run`
it never would, because there is only one.

> This also shows why **in-process state is a lie** in production. A counter, a
> cache or a session in a module-level variable is *per worker* — which is why
> Days 06 and 19 insisted on shared backends.

### With Docker

```bash
export SECRET_KEY=$(python -c "import secrets;print(secrets.token_hex(32))")
docker compose up --build
curl -s http://localhost:8080/ | python -m json.tool
```

> **Note on verification:** gunicorn, the security headers, graceful shutdown
> and `ProxyFix` were all exercised directly while writing this day. The
> **`docker build` was not run here** — no Docker daemon was available in this
> environment. The compose file and shell script were syntax-checked; treat the
> image build as reviewed-but-unexecuted and run it yourself first.

## 4. Gunicorn, setting by setting

```python
workers = (multiprocessing.cpu_count() * 2) + 1
threads = 2
timeout = 30
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 100
```

| Setting | Reasoning |
|---|---|
| `workers` | `(2 × cores) + 1` is a **starting point, not a law**. Measure. Every worker is a full copy of your app — 16 workers of a 300 MB app needs 5 GB |
| `threads` | let a worker overlap I/O waits; irrelevant for CPU-bound work (the GIL) |
| `worker_class` | `sync` is right for ordinary blocking Flask. `gevent` only for very high concurrency *and* only if every library is monkey-patch-safe |
| `timeout` | longer than your slowest legitimate request, **shorter than nginx's** |
| `graceful_timeout` | lets in-flight requests finish during a restart |
| `max_requests` | recycles workers, turning a slow memory leak into an invisible restart |
| `preload_app` | saves memory via copy-on-write — but anything created at import time is **shared across forks**, so database connections must be per-worker |

**Timeout ordering matters.** If gunicorn's timeout is *longer* than nginx's,
nginx returns 504 while gunicorn is still working — a "timeout" with no trace in
your application log.

### Graceful shutdown, verified

```
[gunicorn] Handling signal: term
[gunicorn] Worker exiting (pid: 32155)
[gunicorn] Shutting down: Master
```

## 5. The Dockerfile, decision by decision

```dockerfile
FROM python:3.11.7-slim AS builder     # pin the FULL version
COPY requirements.txt .                # ← requirements ALONE, first
RUN pip wheel --wheel-dir /wheels -r requirements.txt

FROM python:3.11.7-slim AS runtime     # no compiler in the final image
RUN groupadd --system appuser && useradd --system --gid appuser appuser
COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels /wheels/*
COPY --chown=appuser:appuser shipit/ ./shipit/
USER appuser                           # ← not root
CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]   # ← exec form
```

| Decision | Why |
|---|---|
| **Multi-stage** | the compiler stays in stage 1; a compiler in production is a tool for an attacker |
| **Pin the full version** | `python:3` means today's build ≠ next month's |
| **`COPY requirements.txt` alone, first** | `pip install` re-runs only when *dependencies* change, not on every source edit |
| **Copy code last** | it changes most often, so it invalidates the fewest layers |
| **`USER appuser`** | containers run as root by default; one line removes a whole escalation class |
| **Exec form `CMD`** | see below |
| **`PYTHONUNBUFFERED=1`** | otherwise `docker logs` from a crashing container is often empty |
| **`HEALTHCHECK`** | compose and Kubernetes consume it |

### Exec form vs shell form — a real bug

```dockerfile
CMD gunicorn --config gunicorn.conf.py wsgi:app          # ❌ shell form
CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]   # ✅ exec form
```

In shell form the process is a child of `/bin/sh`, which **does not forward
signals**. `docker stop` waits ten seconds, then `SIGKILL`s — killing every
in-flight request. Exec form makes gunicorn PID 1, so it receives `SIGTERM` and
shuts down gracefully.

### `.dockerignore` is written *before* the Dockerfile

Without it, `docker build` uploads your `.venv`, `.git` and database files as
build context — slow, cache-destroying, and **`COPY . .` then ships your `.git`
directory, which contains every secret you ever committed and removed.**

## 6. `ProxyFix`: the header trust problem

Behind nginx, every request reaches Flask from `127.0.0.1` over plain HTTP.

```
TRUST_PROXY unset  -> remote_addr: 127.0.0.1  scheme: http   is_secure: False
TRUST_PROXY=1      -> remote_addr: 10.0.0.1   scheme: https  is_secure: True
```

Without it: every log line, rate limit (Day 19) and audit record shows the same
IP, and `url_for(_external=True)` generates `http://` links on an HTTPS site.

### Counting hops — get this exactly right

`X-Forwarded-For` is appended left-to-right, so the **original client is
leftmost** and the nearest proxy is rightmost:

```
X-Forwarded-For: 203.0.113.45, 10.0.0.1
                 ^client        ^your proxy
```

`x_for=N` takes the Nth value **from the right**. Verified:

| Setting | `remote_addr` |
|---|---|
| `x_for=1` | `10.0.0.1` — one hop back |
| `x_for=2` | `203.0.113.45` — the real client |

So the count must equal the number of proxies that actually append to the
header: one nginx → `x_for=1`; a CDN in front of nginx → `x_for=2`.

> **Too high is a vulnerability.** Anyone can send an `X-Forwarded-For` header.
> Trust more hops than exist and a client can inject a fake address — spoofing
> its way past your rate limits and IP allow-lists.

## 7. Why have nginx at all?

| Reason | Detail |
|---|---|
| TLS termination | certificates live in nginx; the app never sees them |
| Static files | served from disk without waking a Python worker |
| **Slow clients** | nginx buffers a request arriving over 30 seconds, so a worker is busy for milliseconds |
| Request-size caps | rejects an oversized upload before it reaches Python |
| Maintenance page | something to serve when the app is down |

The slow-client point is the one people underestimate: without buffering, a
handful of deliberately slow connections can occupy every worker you have — the
classic **slowloris** attack.

Note also that the app is **not** published to the host in compose. Only nginx
is. Exposing gunicorn directly would let anyone bypass the proxy, and with it
the TLS, the rate limits and the size caps.

## 8. Security headers

| Header | Blocks |
|---|---|
| `X-Content-Type-Options: nosniff` | a JSON response being sniffed as HTML and executed |
| `X-Frame-Options: DENY` | clickjacking |
| `Referrer-Policy` | leaking URLs (with ids/tokens) to other sites |
| `Permissions-Policy` | browser features the app never uses |
| `Strict-Transport-Security` | protocol downgrade — **but see the warning** |
| `Content-Security-Policy` | XSS — the strongest, and easiest to get wrong |

> **HSTS is sticky.** A browser that has seen it refuses plain HTTP for your
> domain for `max-age` seconds, whatever you do. If your certificate expires the
> site becomes *unreachable*, not merely insecure. Start with a short `max-age`,
> and add `preload` only when certain — removal takes months.

For CSP, deploy in `Content-Security-Policy-Report-Only` first, watch the
violations, then enforce.

### A correction worth keeping

The app sets `Server: shipit`, and it **does not work** — gunicorn writes its own
`Server: gunicorn/23.0.0` at the WSGI layer, after the `after_request` hook.
Verified with `curl -I`. The fix belongs at the proxy, which touches the response
last:

```nginx
server_tokens off;
proxy_hide_header Server;
```

The lesson generalises: **a header set later in the chain wins.** Know what your
proxy adds, removes and overwrites.

## 9. `docker compose`: two traps

```yaml
depends_on:
  db:
    condition: service_healthy      # ← not just service_started
```

`depends_on` alone waits for the container to **start**, not to be **ready**. The
app then starts while Postgres is still initialising, fails to connect, and
crash-loops. This is among the most common compose misunderstandings.

```yaml
volumes:
  - pgdata:/var/lib/postgresql/data   # ← a NAMED volume
```

Without it the database lives in the container's writable layer and is destroyed
by `docker compose down`. This is how people lose their development data and
conclude Docker "ate" it.

## 10. Deploying, in the right order

```bash
set -euo pipefail          # stop at the problem, don't half-apply

docker compose build
docker compose run --rm app flask db upgrade    # migrate BEFORE new code runs
docker compose up -d --no-deps --build app
# wait for /readyz, roll back if it never passes
docker compose exec -T web nginx -s reload
```

| Rule | Reason |
|---|---|
| `set -euo pipefail` | a failed step must stop the deploy |
| Migrate **before** the new code | new code querying a missing column crashes (Day 09 §9) |
| Migrate in a **one-off container** | in the start command, every replica races to migrate the same database |
| Wait for `/readyz` | "started" is not "working" |
| Have a rollback path | a deploy script without one is a script you regret at 2am |
| Back up first | `downgrade` cannot recover a dropped column's data |

## 11. Best practices introduced today

| Practice | Reason |
|---|---|
| Gunicorn, never `app.run()` | workers, timeouts, graceful reload |
| Config file, not a flag soup | version-controlled and commented |
| `timeout` < nginx's `proxy_read_timeout` | otherwise 504s with no app-side trace |
| Worker recycling with jitter | survives slow leaks without a thundering restart |
| Multi-stage build | no compiler in the runtime image |
| Pin the full base-image version | reproducible builds |
| `COPY requirements.txt` first | dependency layer stays cached |
| `.dockerignore` before the Dockerfile | speed, caching, and not shipping `.git` |
| Non-root `USER` | removes an escalation class |
| Exec-form `CMD` | signals reach the app; graceful stop works |
| `PYTHONUNBUFFERED=1` | crash logs are not swallowed |
| `HEALTHCHECK` in the image | the orchestrator can act on it |
| App not published; only the proxy | nobody can bypass TLS and rate limits |
| `ProxyFix` with the **correct** hop count | real client IPs, no spoofing |
| Security headers in the app *and* the proxy | they travel with the app |
| CSP in report-only first | an enforced bad policy breaks the site |
| Short HSTS `max-age` initially | it cannot be taken back quickly |
| `service_healthy`, not `service_started` | avoids start-up crash loops |
| Named volumes for data | `down` must not delete the database |
| Bounded Redis memory | an unbounded cache is a slow outage |
| Migrate once, before rollout | replicas must not race |

## 12. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Works in dev, dies under load | `app.run()` in production | gunicorn |
| `docker stop` takes 10s and kills requests | shell-form `CMD` | exec form |
| Every build reinstalls all packages | `COPY . .` before `pip install` | copy requirements first |
| Image is 1.2 GB | single stage, full base image | multi-stage + `-slim` |
| `.git` and `.env` inside the image | no `.dockerignore` | write it first |
| Container escape is catastrophic | running as root | `USER appuser` |
| Empty `docker logs` after a crash | output buffered | `PYTHONUNBUFFERED=1` |
| All clients share one IP in logs | no `ProxyFix` | add it, with the right count |
| Clients spoof their IP | hop count too high | match your real topology |
| `http://` links on an HTTPS site | `X-Forwarded-Proto` not trusted | `x_proto=1` |
| 504 with nothing in the app log | gunicorn timeout > nginx timeout | reorder them |
| App crash-loops on first boot | `depends_on` without `service_healthy` | add the condition |
| Database empties on `compose down` | no named volume | add one |
| Site unreachable after cert expiry | HSTS with a long `max-age` | start short |
| Two replicas migrate at once | migrations in the start command | one-off container |
| Workers slowly consume all RAM | no recycling | `max_requests` |

## 13. Exercises

1. Run `docker compose up --build` and confirm `curl localhost:8080/whoami`
   shows your real IP and `https` when nginx forwards it.
2. Set `GUNICORN_TIMEOUT=3` and request `/slow?s=10`. Watch the worker get
   killed and replaced, and find it in the logs.
3. Add a TLS server block with a self-signed certificate and redirect port 80.
4. Shrink the image further with `python:3.11-alpine` — then measure the build
   time, and read about musl vs glibc before adopting it.
5. Add `docker compose --profile debug` with an extra service running the app in
   development mode.
6. Add a GitHub Actions workflow: test → build → push to a registry.
7. Write Kubernetes manifests (Deployment, Service, Ingress) with
   `livenessProbe: /healthz` and `readinessProbe: /readyz`, and map each back to
   Day 18 §11.

## 14. What's next

**[Day 21 — Capstone →](../21_capstone_analytics_dashboard/)**
Everything from all twenty days, assembled into one deployable application.

---

<!-- nav -->
[← Day 19 — Caching, Rate Limiting and Background Jobs](../19_caching_rate_limiting_and_jobs/) · **[All 21 days](../README.md)** · [Day 21 — Capstone: Pulse, a Feedback Analytics App →](../21_capstone_analytics_dashboard/)

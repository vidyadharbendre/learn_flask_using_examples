"""
Day 19 — Caching, Rate Limiting and Background Jobs.
====================================================

Real-world scenario
-------------------
A weather dashboard backed by a slow third-party API. Every technique here
exists to answer one of three questions:

============================  =============================================
"Why is this so slow?"        **caching** — do not repeat expensive work
"Why is one client melting    **rate limiting** — bound what anyone can ask
 my server?"
"Why does this request take   **background jobs** — move work off the
 ten seconds?"                 request path
============================  =============================================

What you will learn
-------------------
1. ``@cache.cached`` and ``@cache.memoize`` — and how they differ.
2. **Cache invalidation**, and why the key is the hard part.
3. **HTTP caching**: ``ETag`` and ``Cache-Control`` — the cache you do not run.
4. ``Flask-Limiter``: limits, keys, and the storage backend that makes it real.
5. **Timeouts and retries** for anything across a network.
6. Background jobs with the **202 Accepted** pattern.
7. The **cache stampede**, and what it costs.

How to run
----------
From the repository root::

    source .venv/bin/activate
    export FLASK_APP=19_caching_rate_limiting_and_jobs/wsgi.py
    flask run --port 5019

The measurement that matters
----------------------------
Every claim on this page is a **number**: upstream call counts and elapsed
milliseconds, both reported by the API. "Is my cache working?" is not a matter
of opinion.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

import click
from flask import Flask, jsonify, request
from flask.typing import ResponseReturnValue

from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from .jobs import build_report, runner
from .upstream import UPSTREAM_LATENCY_S, UpstreamError, calls, fetch_weather

cache = Cache()

# The limiter's KEY FUNCTION decides "per what?" — per IP here. In an
# authenticated API, prefer a per-user key: several users behind one office NAT
# share an IP, and limiting them together punishes the innocent.
limiter = Limiter(key_func=get_remote_address)


def create_app(config_name: str = "development") -> Flask:
    """Build the performance-demo application.

    Args:
        config_name: ``"development"`` or ``"testing"``.

    Returns:
        Flask: A configured application.
    """
    app = Flask(__name__)

    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only"),
        TESTING=config_name == "testing",

        # ---- Cache backend --------------------------------------------------
        # "SimpleCache" is a per-process dictionary. That is fine for learning
        # and WRONG for production, for two reasons:
        #   1. With `gunicorn -w 4` you have FOUR independent caches, so your
        #      hit rate collapses and different workers serve different data.
        #   2. It is lost on every restart and deploy.
        # "RedisCache" is shared between workers and machines, and survives a
        # restart. Everything below works identically against it — only
        # CACHE_TYPE changes.
        CACHE_TYPE=os.environ.get("CACHE_TYPE", "SimpleCache"),
        CACHE_DEFAULT_TIMEOUT=30,
        CACHE_KEY_PREFIX="weather:",

        # ---- Rate-limit storage ---------------------------------------------
        # Same story: in-memory storage means each worker enforces its own
        # limit, so `gunicorn -w 4` with "10 per minute" actually allows 40.
        # Redis makes the limit global. Flask-Limiter warns loudly about this
        # in production, and it is right to.
        RATELIMIT_STORAGE_URI=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
        RATELIMIT_HEADERS_ENABLED=True,   # send X-RateLimit-* headers
    )

    cache.init_app(app)
    limiter.init_app(app)

    # A default limit for EVERY route. Opt out per endpoint with
    # @limiter.exempt. A default is the right way round: a new endpoint is
    # protected unless someone deliberately says otherwise.
    limiter.default_limits = ["120 per minute"]

    _register_routes(app)
    _register_commands(app)
    return app


def _register_routes(app: Flask) -> None:
    """Register the demonstration endpoints.

    Args:
        app: The application.
    """

    @app.get("/")
    @limiter.exempt
    def index() -> ResponseReturnValue:
        """Describe the demo endpoints.

        Returns:
            ResponseReturnValue: A map of what to try.
        """
        return jsonify({
            "service": "weather-perf",
            "upstream_latency_ms": int(UPSTREAM_LATENCY_S * 1000),
            "try": {
                "/uncached/<city>": "hits upstream every time",
                "/cached/<city>": "@cache.cached — second call is instant",
                "/memoized/<city>": "@cache.memoize — caches the FUNCTION",
                "/etag/<city>": "HTTP caching: 304 when unchanged",
                "/limited": "5 per minute, then 429",
                "/resilient/<city>": "timeout + retry against a flaky upstream",
                "/reports (POST)": "202 Accepted + a job id to poll",
                "/jobs/<id>": "poll a background job",
                "/stats": "upstream call counts — the proof",
                "/cache/clear (POST)": "invalidate everything",
            },
        })

    # -------------------------------------------------------------------------
    # 1. No cache — the baseline
    # -------------------------------------------------------------------------
    @app.get("/uncached/<city>")
    def uncached(city: str) -> ResponseReturnValue:
        """Fetch weather with no caching at all.

        Args:
            city: The city to look up.

        Returns:
            ResponseReturnValue: The weather, after a full upstream round trip.
        """
        started = time.perf_counter()
        data = fetch_weather(city)
        return jsonify(**data, took_ms=round((time.perf_counter() - started) * 1000, 1),
                       cached=False)

    # -------------------------------------------------------------------------
    # 2. @cache.cached — caches the RESPONSE, keyed by the request path
    # -------------------------------------------------------------------------
    @app.get("/cached/<city>")
    @cache.cached(timeout=30)
    def cached(city: str) -> ResponseReturnValue:
        """Fetch weather, caching the whole response for 30 seconds.

        Args:
            city: The city to look up.

        Returns:
            ResponseReturnValue: The weather, instantly on a cache hit.

        Note:
            ``@cache.cached`` keys on ``request.path`` **by default — the query
            string is ignored.** That is a genuine footgun: ``?units=f`` and
            ``?units=c`` would share one cache entry and serve each other's
            data.

            When your response varies by query parameter, either set
            ``query_string=True`` or supply an explicit ``key_prefix``. See
            :func:`cached_with_units` below.
        """
        started = time.perf_counter()
        data = fetch_weather(city)
        return jsonify(**data, took_ms=round((time.perf_counter() - started) * 1000, 1),
                       note="Cached for 30s. Call again and watch /stats.")

    @app.get("/cached-units/<city>")
    # query_string=True includes a hash of the query string in the key, so
    # ?units=f and ?units=c get separate entries.
    @cache.cached(timeout=30, query_string=True)
    def cached_with_units(city: str) -> ResponseReturnValue:
        """Fetch weather in the requested units, cached per query string.

        Args:
            city: The city to look up.

        Returns:
            ResponseReturnValue: The weather in the requested units.
        """
        units = request.args.get("units", "c")
        data = fetch_weather(city)
        if units == "f":
            data["temperature_f"] = round(data["temperature_c"] * 9 / 5 + 32, 1)
        return jsonify(**data, units=units)

    # -------------------------------------------------------------------------
    # 3. @cache.memoize — caches the FUNCTION, keyed by its ARGUMENTS
    # -------------------------------------------------------------------------
    @cache.memoize(timeout=30)
    def weather_for(city: str) -> dict[str, Any]:
        """Fetch weather, memoised on the city argument.

        Args:
            city: The city to look up.

        Returns:
            dict[str, Any]: The weather data.

        Note:
            ``cached`` vs ``memoize``:

            - ``@cache.cached`` decorates a **view**, keyed by the request.
            - ``@cache.memoize`` decorates **any function**, keyed by its
              arguments — so several views, a CLI command and a background job
              can all share one cached result.

            Memoize is usually what you actually want, because it caches the
            *expensive thing* rather than one particular presentation of it.
        """
        return fetch_weather(city)

    @app.get("/memoized/<city>")
    def memoized(city: str) -> ResponseReturnValue:
        """Return memoised weather.

        Args:
            city: The city to look up.

        Returns:
            ResponseReturnValue: The weather, plus how long the call took.
        """
        started = time.perf_counter()
        data = weather_for(city)
        return jsonify(**data, took_ms=round((time.perf_counter() - started) * 1000, 1))

    @app.post("/memoized/<city>/invalidate")
    def invalidate(city: str) -> ResponseReturnValue:
        """Drop the memoised entry for one city.

        Args:
            city: The city to invalidate.

        Returns:
            ResponseReturnValue: Confirmation.

        Note:
            **Invalidation is the hard half of caching.** ``delete_memoized``
            takes the function *and the same arguments*, because that is what
            the key was built from. Forget an argument and you clear the wrong
            entry — silently.

            The general rule: invalidate at the moment the underlying data
            changes (in the write path), not on a guess. A cache that is only
            ever cleared by a timeout will serve stale data for exactly that
            long, and that may be fine — as long as you *chose* it.
        """
        cache.delete_memoized(weather_for, city)
        return jsonify(invalidated=city, note="Next call hits upstream again.")

    @app.post("/cache/clear")
    def clear_cache() -> ResponseReturnValue:
        """Clear the entire cache.

        Returns:
            ResponseReturnValue: Confirmation.

        Warning:
            ``cache.clear()`` is a blunt instrument. On a shared Redis it wipes
            every key, including other applications' — and clearing everything
            under load invites a **stampede**: every client misses at once and
            they all hit your upstream together.
        """
        cache.clear()
        return jsonify(cleared=True)

    # -------------------------------------------------------------------------
    # 4. HTTP caching — the cache you do not have to run
    # -------------------------------------------------------------------------
    @app.get("/etag/<city>")
    def etag(city: str) -> ResponseReturnValue:
        """Serve weather with an ``ETag`` and conditional-request support.

        Args:
            city: The city to look up.

        Returns:
            ResponseReturnValue: ``200`` with a body, or ``304`` with none.

        Note:
            This is the cheapest cache available, because **the client keeps
            it**. The flow:

            1. You return a body plus ``ETag: "abc123"``.
            2. The client re-requests with ``If-None-Match: "abc123"``.
            3. If nothing changed you return **304 Not Modified** with an empty
               body — saving the bandwidth entirely.

            Browsers, CDNs and reverse proxies all honour this automatically.
            It composes with your server-side cache rather than replacing it:
            server cache saves *computation*, HTTP cache saves *transfer*.

            ``Cache-Control`` decides who may store it:

            - ``public`` — CDNs and proxies may cache it too
            - ``private`` — only the end client (use for per-user data)
            - ``no-store`` — never cache (use for anything sensitive)

            Getting ``private`` versus ``public`` wrong on a personalised page
            is how one user's data ends up served to another from a CDN.
        """
        data = weather_for(city)
        payload = json.dumps(data, sort_keys=True)
        version = hashlib.sha256(payload.encode()).hexdigest()[:16]

        if request.if_none_match and version in request.if_none_match:
            return "", 304, {"ETag": f'"{version}"', "Cache-Control": "public, max-age=30"}

        response = jsonify(**data)
        response.set_etag(version)
        response.headers["Cache-Control"] = "public, max-age=30"
        return response

    # -------------------------------------------------------------------------
    # 5. Rate limiting
    # -------------------------------------------------------------------------
    @app.get("/limited")
    @limiter.limit("5 per minute")
    def limited() -> ResponseReturnValue:
        """An endpoint capped at five calls per minute per IP.

        Returns:
            ResponseReturnValue: A counter, until the limit is reached.

        Note:
            Rate limiting is not only about abuse. It protects you from a
            client with a retry loop, a misconfigured cron job, and your own
            load test. The limits worth setting first are on the endpoints that
            are **expensive** (search, export, report generation) or
            **security-sensitive** (login, password reset, token issue — Day 13
            exercise 1).
        """
        return jsonify(ok=True, message="Within the limit.",
                       hint="Call this six times in a minute.")

    @app.get("/expensive")
    # Several limits at once: a burst allowance AND a sustained cap. This is
    # usually what you want — brief bursts are normal, sustained load is not.
    @limiter.limit("3 per 10 seconds;20 per hour")
    def expensive() -> ResponseReturnValue:
        """A costly endpoint with both a burst and a sustained limit.

        Returns:
            ResponseReturnValue: The result.
        """
        return jsonify(result=weather_for("Bengaluru"), limits="3/10s and 20/hour")

    @app.errorhandler(429)
    def rate_limited(error: Any) -> ResponseReturnValue:
        """Answer a rate-limited request helpfully.

        Args:
            error: The ``RateLimitExceeded`` exception.

        Returns:
            ResponseReturnValue: A 429 explaining the limit.

        Note:
            **Always tell the client when to try again.** A 429 with no
            ``Retry-After`` invites a tight retry loop, which makes the problem
            worse. Flask-Limiter sets the header; this body explains it in
            terms a developer reading logs can act on.
        """
        return jsonify(error={
            "code": "rate_limited",
            "message": "Too many requests.",
            "limit": str(getattr(error, "description", "")),
            "retry_after_seconds": getattr(error, "retry_after", None),
        }), 429

    # -------------------------------------------------------------------------
    # 6. Resilience: timeouts and retries
    # -------------------------------------------------------------------------
    @app.get("/resilient/<city>")
    def resilient(city: str) -> ResponseReturnValue:
        """Call a flaky upstream with bounded retries and a cached fallback.

        Args:
            city: The city to look up.

        Returns:
            ResponseReturnValue: Fresh data, stale data, or a 503.

        Note:
            Three rules for calling anything across a network:

            1. **Bound the retries.** Retrying forever turns a brief upstream
               blip into your own outage, and a retry storm can prevent the
               upstream from recovering at all.
            2. **Back off between attempts.** Immediate retries arrive while the
               service is still overloaded. Real systems add *jitter* too, so a
               thousand clients do not retry in lockstep.
            3. **Only retry what is safe to repeat.** ``GET`` is idempotent
               (Day 11); retrying a payment ``POST`` charges twice.

            Serving **stale data on failure** is often better than an error —
            weather from two minutes ago beats no weather. That is a product
            decision, and it should be a deliberate one.
        """
        fail_rate = request.args.get("fail_rate", default=0.6, type=float)
        attempts = 0
        last_error = ""

        for attempt in range(3):          # bounded
            attempts = attempt + 1
            try:
                data = fetch_weather(city, fail_rate=fail_rate)
                cache.set(f"last_good:{city}", data, timeout=600)
                return jsonify(**data, attempts=attempts, source="upstream")
            except UpstreamError as error:
                last_error = str(error)
                if attempt < 2:
                    time.sleep(0.05 * (2 ** attempt))    # exponential backoff

        stale = cache.get(f"last_good:{city}")
        if stale is not None:
            return jsonify(**stale, attempts=attempts, source="stale-cache",
                           warning="Upstream is failing; serving the last good value.")

        return jsonify(error={
            "code": "upstream_unavailable", "message": last_error, "attempts": attempts,
        }), 503

    # -------------------------------------------------------------------------
    # 7. Background jobs
    # -------------------------------------------------------------------------
    @app.post("/reports")
    def create_report() -> ResponseReturnValue:
        """Queue a slow multi-city report and return immediately.

        Returns:
            ResponseReturnValue: ``202 Accepted`` with a job id and poll URL.

        Note:
            **202 Accepted** is the correct status: the work has been accepted
            but is not finished, so there is no resource to return yet. The
            ``Location`` header points at where the client should poll.

            Doing this inline would occupy a worker for
            ``len(cities) × 350ms``. With four workers, a handful of concurrent
            report requests would make the entire site unresponsive.
        """
        body = request.get_json(silent=True) or {}
        cities = body.get("cities") or ["Bengaluru", "Mumbai", "Delhi", "Chennai"]
        if not isinstance(cities, list) or not all(isinstance(c, str) for c in cities):
            return jsonify(error="cities must be a list of strings."), 422
        if len(cities) > 10:
            return jsonify(error="At most 10 cities per report."), 422

        job = runner.submit("build_report", build_report, cities)
        # jsonify() accepts EITHER one positional value OR keyword arguments,
        # never both — `jsonify(some_dict, extra=1)` raises TypeError. Merge
        # into a single dict instead.
        return jsonify({**job.to_dict(), "poll": f"/jobs/{job.id}"}), 202, {
            "Location": f"/jobs/{job.id}"
        }

    @app.get("/jobs/<job_id>")
    @limiter.exempt          # polling must not be rate limited into failure
    def job_status(job_id: str) -> ResponseReturnValue:
        """Report the status of a background job.

        Args:
            job_id: The identifier returned by ``POST /reports``.

        Returns:
            ResponseReturnValue: ``200`` with the job, or ``404``.
        """
        job = runner.get(job_id)
        if job is None:
            return jsonify(error="No such job."), 404
        return jsonify(job.to_dict())

    @app.get("/jobs")
    @limiter.exempt
    def list_jobs() -> ResponseReturnValue:
        """List recent jobs.

        Returns:
            ResponseReturnValue: ``200`` with the most recent jobs.
        """
        return jsonify(data=[job.to_dict() for job in runner.recent()])

    # -------------------------------------------------------------------------
    # 8. Measurement
    # -------------------------------------------------------------------------
    @app.get("/stats")
    @limiter.exempt
    def stats() -> ResponseReturnValue:
        """Report how many times the upstream was actually called.

        Returns:
            ResponseReturnValue: Per-city call counts and the total.

        Note:
            This is the whole point of the day. A cache either reduces this
            number or it does not, and *measuring* beats assuming — a
            misconfigured key (see ``@cache.cached``'s query-string default)
            produces a cache that looks fine and never hits.
        """
        return jsonify(
            upstream_calls=dict(calls.counts),
            upstream_failures=dict(calls.failures),
            total_calls=calls.total(),
            cache_type=app.config["CACHE_TYPE"],
            note="Cached endpoints should not increase this on repeat calls.",
        )

    @app.post("/stats/reset")
    @limiter.exempt
    def reset_stats() -> ResponseReturnValue:
        """Reset the upstream call counters.

        Returns:
            ResponseReturnValue: Confirmation.
        """
        calls.reset()
        return jsonify(reset=True)


def _register_commands(app: Flask) -> None:
    """Attach CLI commands.

    Args:
        app: The application.
    """

    @click.command("benchmark")
    def benchmark() -> None:
        """Measure cached versus uncached response times.

        Prints the numbers that justify every line of caching code.
        """
        with app.test_client() as client:
            client.post("/stats/reset")
            client.post("/cache/clear")

            def timed(path: str) -> float:
                """Time one request.

                Args:
                    path: The URL to fetch.

                Returns:
                    float: Elapsed milliseconds.
                """
                started = time.perf_counter()
                client.get(path)
                return (time.perf_counter() - started) * 1000

            click.echo("\n  uncached (upstream every time):")
            for _ in range(3):
                click.echo(f"    {timed('/uncached/Bengaluru'):7.1f} ms")

            click.echo("\n  cached (first call warms it):")
            for index in range(3):
                label = "MISS" if index == 0 else "HIT "
                click.echo(f"    {timed('/cached/Bengaluru'):7.1f} ms  {label}")

            total = client.get("/stats").get_json()["total_calls"]
            click.echo(f"\n  upstream calls for 6 requests: {total}")
            click.echo("  (3 uncached + 1 cache miss = 4; the 2 cache hits cost nothing)\n")

    app.cli.add_command(benchmark)


__all__ = ["create_app", "cache", "limiter"]

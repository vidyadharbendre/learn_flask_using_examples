"""
Day 20 — Docker and Production Deployment.
==========================================

Real-world scenario
-------------------
The application works on your laptop. Now it has to survive being deployed:
behind a proxy, in a container, restarted at will, serving real traffic.

Why ``app.run()`` is not a production server
--------------------------------------------
The Werkzeug development server is **explicitly documented as not for
production**. It is single-process by default, has no request timeouts, no
graceful shutdown, no worker recycling, and it prints a warning every time you
start it. It exists to reload your code when you save a file.

A production WSGI server (gunicorn, uWSGI, waitress) gives you:

- **multiple workers**, so one slow request does not block everyone;
- **timeouts**, so a hung request is killed rather than occupying a worker;
- **graceful reload**, so a deploy does not drop in-flight requests;
- **worker recycling**, which papers over slow memory leaks.

What you will learn
-------------------
1. Gunicorn: workers, threads, timeouts, and how many of each.
2. A **Dockerfile** that is small, cached well, and runs as a non-root user.
3. ``docker compose`` wiring app + Postgres + Redis + nginx.
4. ``ProxyFix`` — without it, every client IP in your logs is the proxy's.
5. **Security headers**, and which ones actually matter.
6. **Graceful shutdown** and zero-downtime deploys.
7. Running **migrations** as part of a deploy, exactly once.

How to run
----------
Locally, with a real WSGI server::

    source .venv/bin/activate
    cd 20_docker_and_production_deploy
    gunicorn --config gunicorn.conf.py wsgi:app

With Docker::

    docker compose up --build
"""

from __future__ import annotations

import os
import socket
import time
from typing import Any

from flask import Flask, jsonify, request
from flask.typing import ResponseReturnValue
from werkzeug.middleware.proxy_fix import ProxyFix

STARTED_AT = time.time()


def create_app() -> Flask:
    """Build the production-shaped application.

    Returns:
        Flask: A configured application.
    """
    app = Flask(__name__)

    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-not-for-production"),
        # Behind TLS-terminating nginx, cookies must still be marked Secure —
        # the browser's connection is HTTPS even though Flask sees HTTP.
        SESSION_COOKIE_SECURE=os.environ.get("APP_ENV") == "production",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PREFERRED_URL_SCHEME="https" if os.environ.get("APP_ENV") == "production" else "http",
        JSON_SORT_KEYS=False,
    )

    # -------------------------------------------------------------------------
    # ProxyFix: trust the proxy's forwarded headers — carefully
    # -------------------------------------------------------------------------
    # Behind nginx, every request reaches Flask from 127.0.0.1 over plain HTTP.
    # Without ProxyFix:
    #   * request.remote_addr is the PROXY's address, so every log line, rate
    #     limit (Day 19) and audit trail records the same IP for everybody;
    #   * request.scheme is "http", so url_for(_external=True) generates http://
    #     links on an https:// site, and secure-cookie logic misfires.
    #
    # ProxyFix rewrites those from X-Forwarded-For / X-Forwarded-Proto.
    #
    # SECURITY: the counts say how many proxies you actually run. Anyone can
    # SEND an X-Forwarded-For header, so trusting more hops than exist lets a
    # client spoof its own IP — and spoof its way around your rate limits and
    # IP allow-lists. Set these to the real number, never higher.
    #
    # HOW THE COUNT WORKS. X-Forwarded-For is appended to left-to-right, so the
    # ORIGINAL client is leftmost and the nearest proxy is rightmost:
    #
    #     X-Forwarded-For: 203.0.113.45, 10.0.0.1
    #                      ^client        ^your CDN/proxy
    #
    # x_for=N takes the Nth value FROM THE RIGHT. Verified:
    #     x_for=1  ->  remote_addr = 10.0.0.1      (one hop back)
    #     x_for=2  ->  remote_addr = 203.0.113.45  (the real client)
    #
    # So the count must equal the number of proxies that actually append to the
    # header. One nginx: x_for=1. CDN in front of nginx: x_for=2. Too low and
    # you log your own proxy; too high and a client can inject a fake address
    # and become anyone it likes.
    if os.environ.get("TRUST_PROXY", "").lower() in {"1", "true", "yes"}:
        app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
            app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=0
        )

    _register_routes(app)
    _register_security_headers(app)
    return app


def _register_routes(app: Flask) -> None:
    """Register the demonstration endpoints.

    Args:
        app: The application.
    """

    @app.get("/")
    def index() -> ResponseReturnValue:
        """Report which worker served this request.

        Returns:
            ResponseReturnValue: Process and host details.

        Note:
            Refresh this repeatedly under gunicorn and watch ``pid`` change:
            that is load being spread across workers. Under ``flask run`` it
            never changes, because there is only one.

            It also demonstrates why **in-process state is a lie** in
            production. A counter, a cache or a session held in a module-level
            variable is per-worker — which is why Days 06 and 19 insisted on
            shared backends.
        """
        return jsonify({
            "service": "shipit",
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "uptime_s": round(time.time() - STARTED_AT, 1),
            "env": os.environ.get("APP_ENV", "development"),
            "served_by": os.environ.get("SERVER_SOFTWARE", "unknown"),
        })

    @app.get("/whoami")
    def whoami() -> ResponseReturnValue:
        """Show what the app believes about the client and the connection.

        Returns:
            ResponseReturnValue: Address, scheme and forwarding headers.

        Note:
            Compare ``remote_addr`` with ``X-Forwarded-For`` when running behind
            nginx. With ``TRUST_PROXY`` unset they differ; with it set,
            ``remote_addr`` becomes the real client address.
        """
        return jsonify({
            "remote_addr": request.remote_addr,
            "scheme": request.scheme,
            "host": request.host,
            "is_secure": request.is_secure,
            "forwarded_for": request.headers.get("X-Forwarded-For"),
            "forwarded_proto": request.headers.get("X-Forwarded-Proto"),
            "trust_proxy": os.environ.get("TRUST_PROXY", "unset"),
        })

    @app.get("/slow")
    def slow() -> ResponseReturnValue:
        """Sleep, to demonstrate worker occupancy and gunicorn's timeout.

        Returns:
            ResponseReturnValue: How long it slept.

        Note:
            Request more seconds than gunicorn's ``timeout`` and the worker is
            **killed and replaced**; the client gets a truncated response. That
            is the correct behaviour: a hung request must not hold a worker
            forever. It is also why long work belongs in a background job
            (Day 19), not in a request.
        """
        seconds = min(request.args.get("s", default=1, type=int), 60)
        time.sleep(seconds)
        return jsonify(slept_s=seconds, pid=os.getpid())

    @app.get("/healthz")
    def healthz() -> ResponseReturnValue:
        """Liveness probe — no dependency checks (Day 18 §11).

        Returns:
            ResponseReturnValue: Always ``200`` while the process runs.
        """
        return jsonify(status="ok", pid=os.getpid())

    @app.get("/readyz")
    def readyz() -> ResponseReturnValue:
        """Readiness probe — dependencies belong here.

        Returns:
            ResponseReturnValue: ``200`` when ready, ``503`` when not.
        """
        checks: dict[str, Any] = {"app": True}
        ready = all(checks.values())
        return jsonify(status="ready" if ready else "not-ready", checks=checks), (
            200 if ready else 503
        )


def _register_security_headers(app: Flask) -> None:
    """Attach security headers to every response.

    Args:
        app: The application.

    Note:
        These can also be set in nginx. Setting them **in the application** has
        one advantage: they travel with the app, so they are present in
        development, in tests, and if somebody deploys it without the proxy.
        Setting them in both places is fine — the proxy simply overwrites.
    """

    @app.after_request
    def add_headers(response: Any) -> Any:
        """Add the headers.

        Args:
            response: The outgoing response.

        Returns:
            Any: The same response, with headers added.
        """
        # Stop a browser guessing the content type. Without it, a JSON response
        # containing user text can be sniffed as HTML and executed (Day 16).
        response.headers.setdefault("X-Content-Type-Options", "nosniff")

        # Refuse to be embedded in an iframe, which defeats clickjacking.
        response.headers.setdefault("X-Frame-Options", "DENY")

        # Do not leak the full URL (which may contain ids or tokens) to other
        # sites in the Referer header.
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )

        # Turn off browser features this app never uses.
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )

        if os.environ.get("APP_ENV") == "production":
            # HSTS: after the first visit, the browser refuses plain HTTP for
            # this domain entirely.
            #
            # WARNING: this is STICKY. A browser that has seen it will not talk
            # HTTP to your domain for `max-age` seconds, whatever you do — so
            # if your certificate expires, the site is unreachable rather than
            # merely insecure. Start with a short max-age, and only add
            # `preload` when you are certain, because removal takes months.
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )

            # CSP is the strongest defence against XSS and the easiest to get
            # wrong. This one is deliberately strict because the app serves only
            # JSON. A real HTML app needs a policy tuned to its assets — build
            # it in Report-Only mode first, watch the violations, then enforce.
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
            )

        # Attempting to hide the server software here does NOT work: gunicorn
        # writes its own `Server: gunicorn/23.0.0` at the WSGI layer, after
        # this hook has run. Verified by curl -I against a live gunicorn.
        #
        # Version disclosure is a minor issue (it tells an attacker which CVEs
        # to try first), and the real fix belongs at the proxy, which is the
        # last thing to touch the response:
        #
        #     server_tokens off;
        #     proxy_hide_header Server;
        #
        # See nginx/default.conf. The lesson generalises: a header set by
        # something LATER in the chain wins, so know what your proxy adds,
        # removes and overwrites.
        return response


__all__ = ["create_app"]

"""
Day 20 — Gunicorn configuration.
================================

Why a config file rather than a long command line: it is version-controlled,
commented, and identical in every environment. A twelve-flag ``gunicorn``
invocation buried in a Dockerfile is where deployment knowledge goes to die.
"""

from __future__ import annotations

import multiprocessing
import os

# -----------------------------------------------------------------------------
# Where to listen
# -----------------------------------------------------------------------------
# 0.0.0.0 inside a container is correct and safe: the container's network
# namespace is isolated, and only the ports you publish are reachable. On a bare
# host it means "every interface", which is usually not what you want — bind to
# 127.0.0.1 and let nginx be the only thing exposed.
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")

# -----------------------------------------------------------------------------
# Workers: how much concurrency
# -----------------------------------------------------------------------------
# The usual starting point is (2 x CPU cores) + 1. The reasoning: while one
# worker waits on I/O, another can use the CPU, and the +1 absorbs jitter.
#
# It is a STARTING POINT, not a law. Measure. The right number depends on
# whether your workload is CPU-bound (fewer workers) or I/O-bound (more), and
# on how much memory each worker costs — every worker is a full copy of your
# application, so 16 workers of a 300 MB app needs 5 GB.
workers = int(os.environ.get("GUNICORN_WORKERS", (multiprocessing.cpu_count() * 2) + 1))

# Threads per worker. The default sync worker handles ONE request at a time;
# threads let a worker overlap I/O waits. Good for a database-heavy Flask app,
# irrelevant for a CPU-bound one (the GIL).
threads = int(os.environ.get("GUNICORN_THREADS", 2))

# "sync" is the right default: simple, predictable, and correct for ordinary
# blocking Flask code. Switch to "gevent"/"eventlet" only for very high
# concurrency with long I/O waits — and only if every library you use is
# monkey-patch-safe, which is a real constraint.
worker_class = os.environ.get("GUNICORN_WORKER_CLASS", "sync")

# -----------------------------------------------------------------------------
# Timeouts
# -----------------------------------------------------------------------------
# A worker silent for this long is killed and replaced. This is the safety net
# that stops one hung request occupying a worker forever.
#
# It must be LONGER than your slowest legitimate request and SHORTER than the
# proxy's timeout, or nginx returns 504 while gunicorn is still working — and
# you get a confusing "timeout" with no trace in the app logs.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 30))

# How long to let in-flight requests finish during a graceful restart. This is
# what makes a zero-downtime deploy possible.
graceful_timeout = 30

# Keep-alive should be slightly LONGER than the proxy's, so the proxy closes
# idle connections rather than gunicorn — closing from this side can produce
# spurious 502s.
keepalive = 5

# -----------------------------------------------------------------------------
# Worker recycling
# -----------------------------------------------------------------------------
# Restart each worker after this many requests. It is a pragmatic mitigation for
# slow memory leaks — in your code or in a dependency — that turns a gradual
# out-of-memory kill into an invisible restart. `jitter` staggers them so they
# do not all recycle at once.
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", 1000))
max_requests_jitter = 100

# -----------------------------------------------------------------------------
# Preloading
# -----------------------------------------------------------------------------
# preload_app=True imports the application ONCE in the master, then forks. It
# saves memory (copy-on-write) and speeds up start-up.
#
# The catch: anything created at import time is SHARED ACROSS FORKS. Database
# connections and random seeds must be created per worker, or workers will
# fight over one connection. Leave it off unless you have checked.
preload_app = os.environ.get("GUNICORN_PRELOAD", "false").lower() == "true"

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
# "-" means stdout/stderr. In a container that is exactly right: the runtime
# collects the streams, and writing to files inside a container just fills the
# layer and disappears on restart.
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")

# Include the real client IP (via X-Forwarded-For) and the response time.
access_log_format = (
    '%({x-forwarded-for}i)s %(h)s "%(r)s" %(s)s %(b)s %(D)sµs "%(a)s"'
)


def when_ready(server: object) -> None:
    """Log once when the master is ready to accept connections.

    Args:
        server: The gunicorn arbiter.
    """
    print(f"[gunicorn] ready: {workers} workers x {threads} threads on {bind}", flush=True)


def worker_int(worker: object) -> None:
    """Log a worker interrupted by SIGINT/SIGQUIT.

    Args:
        worker: The gunicorn worker.
    """
    print(f"[gunicorn] worker {getattr(worker, 'pid', '?')} interrupted", flush=True)

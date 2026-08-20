# =============================================================================
# Day 20 — Dockerfile
# =============================================================================
# A MULTI-STAGE build. The first stage has a compiler and build tools; the final
# image has neither. That matters for size and for security: a compiler in a
# production image is a tool an attacker can use.
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: build the dependencies
# -----------------------------------------------------------------------------
# Pin the FULL version, not `python:3` or even `python:3.11`. A floating tag
# means today's build and next month's build are different images, and
# "it worked last week" becomes unanswerable.
FROM python:3.11.7-slim AS builder

# PYTHONDONTWRITEBYTECODE: no .pyc files, which are useless in a container.
# PYTHONUNBUFFERED: send stdout/stderr straight out, so `docker logs` shows
#   output immediately instead of when a buffer happens to flush. Without it,
#   logs from a crashing container are frequently lost entirely.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# ---- LAYER CACHING: copy requirements FIRST, alone --------------------------
# Docker caches each instruction and invalidates every later one when an input
# changes. Copying requirements.txt by itself means `pip install` is only re-run
# when DEPENDENCIES change — not on every source edit.
#
# Do `COPY . .` before `pip install` and you reinstall every package on every
# one-character change, turning a 2-second build into a 2-minute one.
COPY requirements.txt .

# Build wheels once here; the final stage installs them without a compiler.
RUN pip wheel --wheel-dir /wheels -r requirements.txt


# -----------------------------------------------------------------------------
# Stage 2: the runtime image
# -----------------------------------------------------------------------------
FROM python:3.11.7-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production \
    TRUST_PROXY=1

# ---- Run as a NON-ROOT user -------------------------------------------------
# Containers run as root by default. A container escape, or a bug that lets an
# attacker write files, is far more dangerous with root. Creating an unprivileged
# user costs one line and removes an entire class of escalation.
#
# --no-create-home and --shell /usr/sbin/nologin: this account exists to run one
# process, not to be logged into.
RUN groupadd --system --gid 1001 appuser \
 && useradd --system --uid 1001 --gid appuser \
            --no-create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

# Install from the wheels built in stage 1 — no compiler in this image.
COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels /wheels/* \
 && rm -rf /wheels

# Application code last: it changes most often, so it invalidates the fewest
# cached layers. Order your COPY instructions from least- to most-frequently
# changed.
COPY --chown=appuser:appuser shipit/ ./shipit/
COPY --chown=appuser:appuser wsgi.py gunicorn.conf.py ./

USER appuser

EXPOSE 8000

# A container-level health check. The orchestrator uses it to decide whether
# this container is working; compose and Kubernetes both consume it.
# --start-period gives the app time to boot before failures count against it.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

# EXEC form (a JSON array), not shell form. In shell form the process runs as a
# child of /bin/sh, which does NOT forward signals — so `docker stop` waits ten
# seconds and then SIGKILLs your app, killing in-flight requests. Exec form makes
# gunicorn PID 1, so it receives SIGTERM and shuts down gracefully.
CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]

# Day 21 — production image (the Day 20 pattern, applied to the capstone).
FROM python:3.11.7-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
# Requirements alone, first: the dependency layer stays cached across code edits.
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

FROM python:3.11.7-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 APP_ENV=production

# Non-root: containers run as root by default, and a bug that writes files is
# far more dangerous with it.
RUN groupadd --system --gid 1001 appuser \
 && useradd --system --uid 1001 --gid appuser --no-create-home \
            --shell /usr/sbin/nologin appuser

WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels /wheels/* && rm -rf /wheels

# Code last: it changes most often, so it invalidates the fewest layers.
COPY --chown=appuser:appuser analytics/ ./analytics/
COPY --chown=appuser:appuser migrations/ ./migrations/
COPY --chown=appuser:appuser wsgi.py gunicorn.conf.py ./

RUN mkdir -p instance && chown appuser:appuser instance
USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

# EXEC form: shell form would not forward SIGTERM, so `docker stop` would
# SIGKILL and drop in-flight requests.
CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]

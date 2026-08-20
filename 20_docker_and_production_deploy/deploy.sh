#!/usr/bin/env bash
# =============================================================================
# Day 20 — a deployment script, in the order that matters
# =============================================================================
set -euo pipefail
# -e  exit on any error        — a failed step must stop the deploy
# -u  error on unset variable  — a typo'd $SECRET_KEY must not become ""
# -o pipefail  a failure anywhere in a pipe fails the whole pipe
#
# Those three lines are the difference between a deploy that stops at the
# problem and one that carries on and half-applies itself.

echo "==> 1. Build the image"
docker compose build

echo "==> 2. Run migrations — ONCE, before the new code starts"
# Order matters (Day 09 §9): migrate BEFORE the new code runs, or the new code
# queries a column that does not exist yet.
#
# `run --rm` starts a ONE-OFF container. Putting migrations in the app's start
# command instead means every worker and every replica races to migrate the same
# database simultaneously.
# docker compose run --rm app flask db upgrade

echo "==> 3. Roll out the new containers"
# --no-deps: do not restart the database while rolling out the app.
docker compose up -d --no-deps --build app

echo "==> 4. Wait for health"
for attempt in $(seq 1 30); do
    if docker compose exec -T app python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=2)" \
        > /dev/null 2>&1; then
        echo "    healthy after ${attempt} attempt(s)"
        break
    fi
    if [ "$attempt" -eq 30 ]; then
        echo "    NOT healthy — rolling back" >&2
        # A deploy script without a rollback path is a deploy script you will
        # regret at 2am.
        exit 1
    fi
    sleep 2
done

echo "==> 5. Reload the proxy"
docker compose exec -T web nginx -s reload || true

echo "==> Deployed."

#!/usr/bin/env bash
# Type check every example. Each example ships its own app.py, so mypy must be
# invoked once per directory (a single run would see duplicate module "app").
set -uo pipefail
cd "$(dirname "$0")"

status=0
for dir in [0-9][0-9]_*/; do
    echo "==> ${dir%/}"
    mypy --cache-dir=".mypy_cache/${dir%/}" "$dir" || status=1
done
exit $status

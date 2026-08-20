#!/usr/bin/env bash
# =============================================================================
# verify.sh — check that every day actually runs on this machine
# =============================================================================
# Run this after `pip install -r requirements.txt` to confirm your environment
# is working, and any time you want to be sure nothing has broken.
#
#     ./verify.sh            # every day
#     ./verify.sh 08         # just day 08
#     ./verify.sh --tests    # also run the three pytest suites
#
# It performs each day's documented setup (init-db / db upgrade / seed), then
# asks the app for one page and checks the status code.
# =============================================================================
set -uo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
FLASK=".venv/bin/flask"

if [ ! -x "$PY" ]; then
    echo "  No virtualenv found. Run:"
    echo "    python3 -m venv .venv && source .venv/bin/activate"
    echo "    pip install -r requirements.txt"
    exit 1
fi

ONLY="${1:-}"
RUN_TESTS=0
[ "$ONLY" = "--tests" ] && { RUN_TESTS=1; ONLY=""; }

pass=0
fail=0
green() { printf "  \033[32m✓\033[0m %s\n" "$1"; }
red()   { printf "  \033[31m✗\033[0m %s\n" "$1"; }

# day : folder : module : url : setup
SINGLE=(
  "01:01_hello_world:app:/:"
  "02:02_routing_and_templates:app:/:"
  "03:03_jinja_templates_and_static:app:/:"
  "04:04_forms_and_request_handling:app:/:"
  "05:05_flask_wtf_and_validation:app:/:"
  "06:06_sessions_cookies_and_flash:app:/:"
  "07:07_project_expense_tracker:app:/:"
  "08:08_database_with_sqlalchemy:app:/:initdb"
  "09:09_migrations_with_flask_migrate:app:/:migrate"
)

PACKAGE=(
  "10:10_blueprints_and_app_factory:inventory:/:seed"
  "11:11_rest_api_fundamentals:bookstore:/api/v1/books:seed"
  "12:12_pydantic_validation_and_schemas:catalogue:/api/v1/books:seed"
  "13:13_authentication_with_flask_login:portal:/:seed"
  "14:14_project_task_manager:taskman:/:seed"
  "15:15_jwt_auth_and_rbac:fleet:/:seed"
  "16:16_file_uploads_and_media:vault:/:"
  "17:17_testing_with_pytest:bookings:/api/rooms:seed"
  "18:18_config_logging_and_errors:observe:/:"
  "19:19_caching_rate_limiting_and_jobs:perf:/:"
  "20:20_docker_and_production_deploy:shipit:/:"
  "21:21_capstone_analytics_dashboard:analytics:/:seed"
)

echo ""
echo "  $($PY --version), Flask $($PY -c 'import importlib.metadata as m; print(m.version("flask"))' 2>/dev/null)"
echo ""

for entry in "${SINGLE[@]}"; do
    IFS=: read -r day folder module url setup <<< "$entry"
    [ -n "$ONLY" ] && [ "$ONLY" != "$day" ] && continue

    case "$setup" in
      initdb)
        $FLASK --app "$folder/app.py" init-db >/dev/null 2>&1
        $FLASK --app "$folder/app.py" seed    >/dev/null 2>&1 ;;
      migrate)
        FLASK_APP="$folder/app.py" $FLASK db upgrade -d "$folder/migrations" >/dev/null 2>&1
        $FLASK --app "$folder/app.py" seed    >/dev/null 2>&1 ;;
    esac

    code=$($PY -c "
import sys; sys.path.insert(0, '$folder')
from $module import app
print(app.test_client().get('$url').status_code)" 2>/dev/null | tail -1)

    if [ "$code" = "200" ] || [ "$code" = "302" ]; then
        green "Day $day  $folder  ($url -> $code)"; pass=$((pass+1))
    else
        red   "Day $day  $folder  ($url -> ${code:-error})"; fail=$((fail+1))
    fi
done

for entry in "${PACKAGE[@]}"; do
    IFS=: read -r day folder module url setup <<< "$entry"
    [ -n "$ONLY" ] && [ "$ONLY" != "$day" ] && continue

    if [ -n "$setup" ]; then
        ( cd "$folder" && FLASK_APP=wsgi.py ../.venv/bin/flask "$setup" >/dev/null 2>&1 )
    fi

    code=$( cd "$folder" && ../.venv/bin/python -c "
from $module import create_app
print(create_app().test_client().get('$url').status_code)" 2>/dev/null | tail -1)

    if [ "$code" = "200" ] || [ "$code" = "302" ] || [ "$code" = "401" ]; then
        green "Day $day  $folder  ($url -> $code)"; pass=$((pass+1))
    else
        red   "Day $day  $folder  ($url -> ${code:-error})"; fail=$((fail+1))
    fi
done

if [ "$RUN_TESTS" = "1" ]; then
    echo ""
    echo "  Test suites:"
    for folder in 14_project_task_manager 17_testing_with_pytest 21_capstone_analytics_dashboard; do
        out=$( cd "$folder" && ../.venv/bin/python -m pytest -q 2>&1 | tail -1 )
        if echo "$out" | grep -qE "failed|error"; then
            red "$folder: $out"; fail=$((fail+1))
        else
            green "$folder: $out"; pass=$((pass+1))
        fi
    done
fi

echo ""
if [ "$fail" -eq 0 ]; then
    echo "  All $pass check(s) passed. Your environment is ready — start with Day 01."
else
    echo "  $pass passed, $fail failed."
    echo "  If a database day failed, try that day's README section 3 commands by hand."
fi
echo ""
[ "$fail" -eq 0 ]

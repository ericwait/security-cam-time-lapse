# ==============================================================================
# justfile — the project's operational vocabulary (Practices Playbook Pillars
# I and V)
#
# Single source of truth for how anyone — human, CI, or agent — operates this
# project. Keep the twelve canonical verb names stable.
# ==============================================================================

set shell := ["bash", "-euo", "pipefail", "-c"]

compose := "docker compose"
health_url := "http://localhost:8000/api/health"

# List every recipe with its doc comment (run with no args).
default:
    @just --list

# Validate .env exists and contains every key declared in .env.example.
[private]
check-env:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -f .env ]; then
        echo "ERROR: .env is missing. Copy .env.example to .env and populate secrets first." >&2
        exit 1
    fi
    missing=0
    while IFS='=' read -r key _; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        if ! grep -Eq "^${key}=.+" .env; then
            echo "ERROR: required secret '${key}' is missing or empty in .env" >&2
            missing=1
        fi
    done < .env.example
    [ "$missing" -eq 0 ]

# Bootstrap a ready-to-use dev environment (idempotent): env, services, schema, seed.
dev: check-env
    {{compose}} up -d --build
    {{compose}} exec -T app uv run alembic upgrade head
    {{compose}} exec -T app uv run python -m app.cli seed-dev
    @echo "Dev stack ready -> http://localhost:5173 (API: http://localhost:8000)"

# Start the configured services in the background (no schema/seed steps).
up: check-env
    {{compose}} up -d

# Stop services, preserving data volumes.
down:
    {{compose}} stop

# DESTRUCTIVE: tear down and rebuild from scratch (asks for typed confirmation).
reset:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "This will permanently destroy local containers, volumes, and ./data contents." >&2
    read -r -p "Type 'yes' to continue: " confirmation
    if [ "$confirmation" != "yes" ]; then
        echo "Aborted — no changes made." >&2
        exit 1
    fi
    {{compose}} down -v --remove-orphans
    rm -rf ./data
    just dev

# Run the automated test suite (all tiers, or pass 'unit' / 'integration').
test tier="all":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{tier}}" in
        unit)        {{compose}} run --rm app uv run pytest tests/unit ;;
        integration) {{compose}} run --rm app uv run pytest tests/integration ;;
        all)         {{compose}} run --rm app uv run pytest tests/unit tests/integration && {{compose}} run --rm web npm test -- --run ;;
        *)           echo "Unknown tier '{{tier}}' (expected: all, unit, integration)" >&2; exit 1 ;;
    esac

# Run the end-to-end tier against a running stack.
e2e:
    {{compose}} run --rm e2e npx playwright test

# Static analysis, check-only — never mutates files.
lint:
    {{compose}} run --rm app uv run ruff check backend tests
    {{compose}} run --rm app uv run ruff format --check backend tests
    {{compose}} run --rm web npm run lint

# Auto-format the codebase in place (idempotent).
fmt:
    {{compose}} run --rm app uv run ruff check --fix backend tests
    {{compose}} run --rm app uv run ruff format backend tests
    {{compose}} run --rm web npm run fmt

# Static type checking, check-only.
typecheck:
    {{compose}} run --rm app uv run mypy backend tests
    {{compose}} run --rm web npm run typecheck

# Apply pending schema migrations (no-op when already up to date).
migrate:
    {{compose}} run --rm app uv run alembic upgrade head

# Verify SQLAlchemy models and committed Alembic migrations are in sync.
schema-drift:
    {{compose}} run --rm app uv run alembic check

# Hit the health endpoint and pretty-print the response.
health:
    curl -fsS {{health_url}} | python3 -m json.tool

# Print the SemVer computed from git history.
version:
    #!/usr/bin/env bash
    set -euo pipefail
    if command -v dotnet-gitversion >/dev/null 2>&1; then
        dotnet-gitversion /showvariable SemVer
        exit 0
    fi
    branch="$(git rev-parse --abbrev-ref HEAD)"
    last_tag="$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || true)"
    if [ -z "$last_tag" ]; then
        base="0.1.0"
        distance="$(git rev-list --count HEAD)"
    else
        base="${last_tag#v}"
        distance="$(git rev-list --count "${last_tag}..HEAD")"
    fi
    if [ "$branch" = "main" ] && [ "$distance" -eq 0 ] && [ -n "$last_tag" ]; then
        echo "$base"
    else
        echo "${base}-alpha.${distance}"
    fi

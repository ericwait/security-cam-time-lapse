> Status: adopted | Audience: new contributors and agents bootstrapping a
> checkout | See also: [Architecture.md](Architecture.md),
> [Testing.md](Testing.md), [../README.md](../README.md)

# Getting started

## Prerequisites

Install on the host (via MacPorts):

```bash
sudo port install just pre-commit gh
```

Plus a running Docker Engine with Compose v2 (`docker info` and
`docker compose version`). Nothing else is required on the host — Python, `uv`,
Node, and `ffmpeg` all run inside containers.

## Quick start

```bash
git clone https://github.com/ericwait/security-cam-time-lapse.git
cd security-cam-time-lapse
cp .env.example .env
# Generate unique local secrets — never share or commit these values:
openssl rand -hex 32   # paste into APP_SECRET_KEY in .env
openssl rand -hex 16   # paste into BOOTSTRAP_ADMIN_PASSWORD in .env
just dev
just health
```

`just health` should report the application and database healthy. If it does
not, see Troubleshooting below before doing anything else.

## What `just dev` does

Idempotent — safe to re-run at any time, including on an already-running stack:

1. Validates `.env` against `.env.example` (fails loudly if a required variable
   is missing or empty).
2. Starts local services via `docker compose` (backend API/scheduler, frontend
   dev server, and a synthetic mock-camera source for local development).
3. Applies pending Alembic schema migrations to SQLite.
4. Seeds development users and synthetic sample captures if the database is
   empty.
5. Prints the local Web UI URL and bootstrap login instructions.

## Install the commit hooks

One-time, per clone:

```bash
pre-commit install
```

Without this, `just lint` still catches everything CI catches — but you will
find out at push time instead of commit time.

## Resetting

```bash
just reset
```

Destructive: drops local containers and development data under `./data/` and
rebuilds from scratch via `just dev`. Asks for typed confirmation before doing
anything irreversible.

## Troubleshooting

- **`just: command not found` or `pre-commit: command not found`:** Install host
  prerequisites (`sudo port install just pre-commit`), then run
  `pre-commit install`.
- **Docker daemon unreachable:** Start OrbStack / Docker Desktop and confirm
  `docker info` succeeds before running `just dev`.

## Where to go next

- Architecture and storage layout: [Architecture.md](Architecture.md).
- Running and writing tests: [Testing.md](Testing.md).
- Full documentation map: [../README.md](../README.md).
- Agent-facing conventions and gotchas: `../../CLAUDE.md` at the repository
  root.

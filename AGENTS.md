# CLAUDE.md — Project Context

## What This Repository Is

**Security Cam Time-Lapse** — a self-hosted container that captures full-resolution still images from security cameras on a schedule and lets `Admin` and `Viewer` users interactively scrub or export dynamic timelapses in a web UI.

**Status:** In development (Phase 1 — Core capture, playback & export).

## Goal & Guiding Principle

Preserve full-resolution visual history automatically and make any slice of time effortlessly playable, without asking viewers to wait on video encoding or operators to babysit storage. Every feature and UI obligation must pass the stakeholder contract and failure test in [`docs/design/Philosophy.md`](docs/design/Philosophy.md).

## Repository Map

```text
backend/                FastAPI app, capture/render workers, SQLAlchemy models, Alembic migrations
frontend/               React + Vite + TypeScript web UI (frame sequence player + admin console)
tests/                  Three test tiers: unit/ (pure), integration/ (SQLite + API), e2e/ (Playwright + axe)
docs/design/            The what & why — domain, roles, playback modes (implementation-agnostic)
docs/development/       The how — stack architecture, setup, testing, guardian suites
docs/decisions/         Discoverability index for inline (decision YYYY-MM-DD, ...) stamps
ops/                    Platform bootstrap and operational scripts
justfile                Canonical commands — the entry point for every operation
```

## Commands

| Task | Command |
| --- | --- |
| Bootstrap & run locally (idempotent) | `just dev` |
| Start / stop configured stack | `just up` / `just down` |
| Run tests (all / one tier) | `just test` / `just test unit` / `just e2e` |
| Lint + format check / auto-format | `just lint` / `just fmt` |
| Type check (Python + TypeScript) | `just typecheck` |
| Apply migrations / check drift | `just migrate` / `just schema-drift` |
| Health check / computed SemVer | `just health` / `just version` |
| Reset local environment | `just reset` — destructive; asks for confirmation |

## Key Design Decisions — and Non-Goals

- **Hybrid playback + ephemeral exports:** Browser plays lightweight ~1080p WebP previews immediately for instant scrubbing, while full-resolution originals live untouched in `YYYY/MM/DD` folders for zoom and on-demand FFmpeg MP4 exports that expire via TTL cache. See [`docs/design/Timelapse-Modes-and-Storage.md`](docs/design/Timelapse-Modes-and-Storage.md).
- **Web-UI camera management & encrypted secrets:** Admins manage RTSP and HTTP(S) cameras, credentials, and intervals in the UI; camera passwords are encrypted at rest in SQLite via `APP_SECRET_KEY`. See [`docs/development/Architecture.md`](docs/development/Architecture.md).
- **Datastore parity:** Tests run against real Alembic-migrated SQLite files in WAL mode — never mocks or unmigrated shortcuts. See [`docs/development/Testing.md`](docs/development/Testing.md).
- **Auth & roles:** Multi-user sessions with `Admin` (cameras, schedules, users, settings) and `Viewer` (browse, scrub, and export across all enabled cameras). See [`docs/design/Stakeholders/Admin.md`](docs/design/Stakeholders/Admin.md).
- **Non-goals (Phase 1):** Continuous 24/7 NVR video recording, motion/AI object detection, permanent server-side saved-video libraries, per-viewer camera ACL matrices, and Mode 3 recurring daily/sunset windows (deferred to Phase 2).

## Current State / Active Work

Phase 0 engineering substrate (FastAPI + React/Vite + SQLite WAL + Alembic + non-root Docker image, pinned lockfiles, and green unit/integration/e2e+axe `just` gates) is complete on `develop` (`0.1.0-alpha`). Active focus: **Phase 1 — Core capture, playback & export** (starting with Slice 1: multi-user auth, bootstrap onboarding, and `test_guardian_role_boundaries.py`). See [`docs/Implementation-Plan.md`](docs/Implementation-Plan.md).

## Conventions

- **Branching:** `develop` is the integration branch (`feature/*`, `fix/*`, `docs/*` branch off `develop` and PR back into it); releases PR `develop` → `main`; `hotfix/*` branches off `main`, PRs to `main`, and mandatorily back-merges into `develop`. Merges use **merge commits, never squash**, so `GitVersion.yml` can compute SemVer `(decision 2026-11-18, bootstrap)`.
- **CI gates:** Fast tier (lint, types, schema-drift, unit, integration, version, ops-validate) runs on every PR; full gate (e2e + axe a11y + Trivy image scan) runs on PRs into `main`.
- **Testing & guardians:** Acceptance-first (`e2e`/`integration` → `unit` → code → `just schema-drift`). Update guardian suites (`test_guardian_frame_selection.py`, `test_guardian_secret_encryption.py`, `test_guardian_role_boundaries.py`) in the same commit whenever guarded logic changes.
- **Format / lint:** `just fmt` and `just lint` (`ruff` + `eslint`/`prettier`); install hooks once per clone via `pre-commit install`. Pin hook `rev`s in `.pre-commit-config.yaml` to match `uv.lock` and `package-lock.json`.
- **Schema changes:** Author Alembic revisions for every model change; verified by `just schema-drift`.
- **Changelog:** Keep a Changelog format in `CHANGELOG.md`; required for user-facing changes and gated mechanically by `/release` and `/hotfix`.
- **Docs & decisions:** Keep `docs/design/` implementation-agnostic and `docs/development/` tool-specific. Stamp every load-bearing decision inline `(decision YYYY-MM-DD, #issue)` and add a matching row in `docs/decisions/README.md` in the same commit.
- **Secrets:** Never commit or print secrets. Only secrets live in `.env` (`.env.example` is the contract); `gitleaks` blocks leaks at commit time.
- **Agent-file sync:** `CLAUDE.md`, `GEMINI.md`, and `AGENTS.md` carry identical guidance and must be updated together in the same commit.
- **License:** Zero-Clause BSD (`0BSD`), chosen deliberately for unrestricted open-source reuse `(decision 2026-11-18, bootstrap)`.

## Definition of Done

- [ ] Tests pass at every tier the change touches (`just test`, and `just e2e` when UI workflows change)
- [ ] Lint, format, and types clean (`just lint && just typecheck`)
- [ ] Schema-drift check clean (`just schema-drift`), if models changed
- [ ] Docs and decision index updated, if behavior, structure, or decisions changed
- [ ] `CHANGELOG.md` updated under `[Unreleased]`, if user-facing
- [ ] Accessibility check (`axe`) clean, if UI changed

## Boundaries

Ask before, never assume: creating remotes, pushing or opening PRs, tagging or releasing, deploying, destructive data operations, anything touching production cameras/volumes, and adding new top-level dependencies. Never print secrets or `.env` values. Commit freely on local branches at logical boundaries; never push without an explicit ask. On failure: stop, surface the raw error, and do not auto-recover past it.

## Gotchas — traps that have cost time

- **Host CLI availability:** `just` and `pre-commit` are host prerequisites via MacPorts (`sudo port install just pre-commit gh`); Python, `uv`, Node, and `ffmpeg` run inside containers via `docker compose` so host Python/Node versions (e.g. Python 3.15 bleeding-edge wheels) cannot break builds.
- **DST wall-clock traps:** Mode 2 queries ("12:00 PM Jan 1 – Jun 1") cross Daylight Saving Time. Always store UTC instants alongside the camera's IANA timezone name — never fixed UTC offsets — and test changes against `test_guardian_frame_selection.py`.

## Maintaining This File

| When… | Update… |
| --- | --- |
| A milestone lands | Current State + `docs/Implementation-Plan.md` |
| Something non-obvious costs >30 minutes | Gotchas — same day |
| The tree or commands change | Repository Map / Commands |
| A convention changes | Conventions — and its enforcement hook |
| Any of the above | `GEMINI.md` and `AGENTS.md`, in the same commit |

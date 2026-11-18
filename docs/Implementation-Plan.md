> Status: Phase 0 in progress | Audience: contributors and agents planning next
> work | See also: [docs/README.md](README.md), [../CHANGELOG.md](../CHANGELOG.md)

<!--
PLAN-DOC LIFECYCLE — read before editing.
Keep forward work only; cut a phase once it ships and record one reverse-chronological
evidence line in Progress log below. Keep one phase in progress at a time.
-->

# Implementation plan

## Phase 0 — Engineering substrate

Stand up the runnable container, database migration pipeline, and quality gates
so every subsequent feature lands against real verification.

- **Containerized FastAPI + React/Vite skeleton.** Minimal backend (`/api/health`
  reporting status, computed version, and SQLite connectivity), Alembic initial
  setup, and Vite TypeScript shell packaged via `Dockerfile` and
  `docker-compose.yml`.
  Exit criterion: `just dev` starts the stack cleanly and `just health` exits 0
  with valid JSON.
- **Task runner + lockfiles + linters.** Pin `uv.lock` and
  `frontend/package-lock.json`; wire `just lint`, `just fmt`, `just typecheck`,
  `just migrate`, `just schema-drift`, `just test`, and `just version`.
  Exit criterion: every `just` fast-tier command exits 0 on a clean checkout,
  and `.pre-commit-config.yaml` hook revisions match the lockfiles.
- **Env contract + secret scanner.** `.env.example` validated on startup;
  `gitleaks` enforced in pre-commit and CI.
  Exit criterion: committing a synthetic secret is blocked by `pre-commit`, and
  missing `.env` keys fail `just dev` with a clear message.

## Verification

Phase 0 complete when:

- [ ] `just dev` brings up a working stack from a clean clone and `just health`
      returns HTTP 200 JSON with version + DB status.
- [ ] `just lint`, `just typecheck`, `just schema-drift`, and `just test` exit
      0 locally and in `.github/workflows/ci.yml`.
- [ ] `pre-commit run --all-files` passes including `gitleaks`.
- [ ] Built production Docker image starts as a non-root user (`id -u != 0`).

## Backlog

- **Multi-user auth & bootstrap onboarding:** `Admin` and `Viewer` accounts,
  session authentication, `BOOTSTRAP_ADMIN_PASSWORD` seeding + first-run UI
  setup wizard, and `test_guardian_role_boundaries.py`.
- **Web-UI camera management & encrypted secret store:** Admin CRUD for cameras
  (RTSP stream and HTTP(S) snapshot adapters), Fernet credential encryption at
  rest (`test_guardian_secret_encryption.py`), and live "Test Capture" endpoint.
- **Scheduled capture engine & dual-tier storage:** Per-camera interval
  scheduler writing untouched full-res originals (`YYYY/MM/DD`) + 1080p WebP
  previews, indexed in SQLite with UTC + camera IANA timezone metadata.
- **Mode 1 & Mode 2 query engine + browser frame player:** Frame-manifest API
  with `test_guardian_frame_selection.py`, interactive React canvas/sequence
  player (timeline scrubbing, FPS control, full-res zoom/download).
- **On-demand FFmpeg MP4 export & ephemeral TTL cache:** Background full-res MP4
  render jobs with progress polling, browser download, and automatic 24-hour TTL
  cache reaper.
- **End-to-end & accessibility gate:** Playwright + `@axe-core/playwright`
  covering Admin camera setup and Viewer Mode 1 / Mode 2 playback & export.
- **Phase 2 — Mode 3 recurring daily window / solar-event timelapses:**
  Multi-frame daily window queries (e.g., sunset each day across dates),
  astronomical sun-time calculation per camera location, and optional burst
  capture schedules around solar events.

## Progress log

- **2026-11-18** — Repository seeded from `ericwait/project-seed-kit` under
  `0BSD` license; domain philosophy, Admin/Viewer stakeholder contracts,
  dual-tier storage & hybrid playback design, and CI/task-runner scaffolding
  established on `main` and `develop`.

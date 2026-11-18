> Status: Phase 1 in progress | Audience: contributors and agents planning next
> work | See also: [docs/README.md](README.md), [../CHANGELOG.md](../CHANGELOG.md)

<!--
PLAN-DOC LIFECYCLE — read before editing.
Keep forward work only; cut a phase once it ships and record one reverse-chronological
evidence line in Progress log below. Keep one phase in progress at a time.
-->

# Implementation plan

## Phase 1 — Core capture, playback & export

Deliver the end-to-end Phase 1 stakeholder contract across vertical slices,
starting with authentication and role boundaries:

- **Slice 1 — Multi-user auth & bootstrap onboarding:** `Admin` and `Viewer`
  accounts, signed session authentication, `BOOTSTRAP_ADMIN_PASSWORD` seeding +
  first-run UI setup wizard, Admin user management endpoints/UI, and
  `test_guardian_role_boundaries.py`.
- **Slice 2 — Web-UI camera management & encrypted secret store:** Admin CRUD
  for cameras (RTSP stream, HTTP(S) snapshot, and dev synthetic adapters),
  Fernet credential encryption at rest (`test_guardian_secret_encryption.py`),
  and live "Test Capture" endpoint.
- **Slice 3 — Scheduled capture engine, dual-tier storage & synthetic dev seed:**
  Per-camera interval scheduler writing untouched full-res originals
  (`YYYY/MM/DD`) + 1080p WebP previews, indexed in SQLite with UTC + camera IANA
  timezone metadata, plus multi-month synthetic sky/clock seed history in
  `just dev`.
- **Slice 4 — Mode 1 & Mode 2 query engine + browser frame player:**
  Frame-manifest API supporting both local wall-clock and fixed standard-time
  offset Mode 2 DST evaluation (`test_guardian_frame_selection.py`), plus
  interactive React canvas/sequence player (timeline scrubbing, FPS control,
  full-res zoom/download).
- **Slice 5 — On-demand FFmpeg MP4 export & ephemeral TTL cache:** Background
  H.264 (`yuv420p`) MP4 render jobs with selectable FPS (`10`/`24`/`30`/`60`),
  output resolution (`Native`/`4K`/`1080p`, default `Native`), optional
  burned-in timestamp watermark, progress polling, browser download, and
  automatic 24-hour TTL cache reaper.

## Verification

Phase 1 complete when:

- [ ] `Admin` and `Viewer` workflows pass unit, integration, and guardian suites
      (`test_guardian_role_boundaries.py`,
      `test_guardian_secret_encryption.py`,
      `test_guardian_frame_selection.py`).
- [ ] `just dev` seeds a playable multi-month synthetic camera spanning DST, and
      both browser scrubbing and FFmpeg MP4 export work out of the box.
- [ ] `just e2e` passes Playwright + `@axe-core/playwright` across Admin camera
      setup and Viewer Mode 1 / Mode 2 playback & export.
- [ ] `just lint`, `just typecheck`, `just schema-drift`, and `just test` exit 0.

## Backlog

- **Phase 2 — Mode 3 recurring daily window / solar-event timelapses:**
  Multi-frame daily window queries (e.g., sunset each day across dates),
  astronomical sun-time calculation per camera location, and optional burst
  capture schedules around solar events.

## Progress log

- **2026-11-18** — Phase 0 engineering substrate shipped (`feature/phase-0-substrate`):
  containerized FastAPI + React/Vite + SQLite WAL + Alembic pipeline, pinned
  `uv.lock` and `package-lock.json` lockfiles, `pre-commit` (`gitleaks` +
  `ruff`), non-root single-container runtime image (`uid=1000`), and green
  `just dev`/`health`/`lint`/`typecheck`/`schema-drift`/`test`/`e2e` (+ `axe`)
  gates.
- **2026-11-18** — Repository seeded from `ericwait/project-seed-kit` under
  `0BSD` license; domain philosophy, Admin/Viewer stakeholder contracts,
  dual-tier storage & hybrid playback design, and CI/task-runner scaffolding
  established on `main` and `develop`.

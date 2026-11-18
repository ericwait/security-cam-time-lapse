> Status: Phase 1 complete — Phase 2 planned | Audience: contributors and agents
> planning next work | See also: [docs/README.md](README.md),
> [../CHANGELOG.md](../CHANGELOG.md)

<!--
PLAN-DOC LIFECYCLE — read before editing.
Keep forward work only; cut a phase once it ships and record one reverse-chronological
evidence line in Progress log below. Keep one phase in progress at a time.
-->

# Implementation plan

## Phase 2 — Mode 3 recurring daily window / solar-event timelapses

Extend selection and scheduling beyond single daily instants so viewers can
watch multi-frame daily windows (such as sunset each evening across seasons):

- **Camera coordinates & astronomical event solver:** Optional latitude,
  longitude, and elevation per camera to compute civil dawn, sunrise, solar
  noon, sunset, and civil dusk in the camera's IANA timezone without external
  network dependencies.
- **Mode 3 query engine & guardian coverage:** Multi-frame daily window queries
  (both fixed local/standard clock spans and `solar_event ± offset_minutes`
  windows across a date range), extending `test_guardian_frame_selection.py`.
- **Event-window burst capture schedules:** Optional higher-frequency capture
  windows around daily or solar events while keeping normal baseline intervals
  outside the window.
- **Multi-day clip transitions in player & MP4 exporter:** Day-boundary markers
  in the browser sequence scrubber and optional subtle crossfade / day-title
  cards in rendered MP4 exports.

## Verification

Phase 2 complete when:

- [ ] Mode 3 fixed-window and solar-event queries pass unit, integration, and
      updated `test_guardian_frame_selection.py` suites across DST and polar/high-latitude
      edge cases.
- [ ] `just e2e` passes Playwright + `@axe-core/playwright` for Mode 3 browsing
      and export.
- [ ] `just lint`, `just typecheck`, `just schema-drift`, and `just test` exit 0.

## Backlog

- **Archive retention & cold-storage export tools:** Optional operator-triggered
  archive bundle export and integrity verification CLI (`python -m app.cli verify-archive`).

## Progress log

- **2026-11-18** — Phase 1 core capture, playback & export shipped (`develop`):
  multi-user `Admin`/`Viewer` session auth + first-run bootstrap wizard, Web-UI
  camera CRUD (RTSP, HTTP Basic/Digest/Bearer, and synthetic adapters) with
  HKDF-Fernet credential encryption at rest, scheduled dual-tier `YYYY/MM/DD`
  original JPEG + 1080p WebP capture engine, DST-spanning synthetic seed
  history, Mode 1 & Mode 2 (`wall_clock` + `standard_offset`) interactive React
  sequence player with full-res inspection, on-demand H.264 (`yuv420p`) FFmpeg
  MP4 export (`10`/`24`/`30`/`60` FPS, `Native`/`4K`/`1080p`, historical
  `drawtext` timestamp watermark) with 24h TTL cache reaping, all three guardian
  suites (`99/99` Python tests at `93%` coverage, `5/5` Vitest tests), and full
  Playwright + `@axe-core/playwright` E2E verification (`3/3` specs, `0` a11y
  violations).
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

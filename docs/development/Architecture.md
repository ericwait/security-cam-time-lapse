> Status: adopted | Audience: contributors and agents implementing backend,
> frontend, or container infrastructure | See also: [Getting-Started.md](Getting-Started.md),
> [Testing.md](Testing.md), [../design/Timelapse-Modes-and-Storage.md](../design/Timelapse-Modes-and-Storage.md)

# Architecture

## Stack overview

The application ships as a single multi-stage, non-root Docker image packaging
both the backend API/scheduler and the compiled web UI
`(decision 2026-11-18, bootstrap)`:

- **Backend (`backend/`):** Python 3.13 + FastAPI + Uvicorn, managed with `uv`
  and locked via `uv.lock`.
- **Datastore:** SQLite in WAL mode (`SQLAlchemy` + `Alembic` migrations),
  stored on the persistent `/data/db` volume. Because SQLite is embedded, unit
  and integration tests run against the exact same SQLite engine as production.
- **Capture & video pipeline:** `ffmpeg` for RTSP main-stream keyframe extraction
  and on-demand H.264 (`yuv420p`) MP4 rendering (supporting native/4K/1080p
  scaling, 10/24/30/60 FPS, and `drawtext` timestamp watermarks); `httpx` for
  HTTP(S) camera snapshot endpoints (Basic/Digest/Bearer auth); `Pillow` for
  generating 1080p WebP previews alongside untouched full-resolution originals.
- **Synthetic development camera:** `just dev` (`python -m app.cli seed-dev`)
  provides a built-in synthetic test camera adapter and seeds multi-month
  sky/clock progression frames (spanning a DST transition) so Mode 1, Mode 2,
  scrubbing, and MP4 export work out of the box without live cameras
  `(decision 2026-11-18, bootstrap)`.
- **Scheduler:** In-process async scheduler (`APScheduler` / `asyncio` tasks)
  driving per-camera capture intervals and periodic render-cache TTL cleanup.
- **Frontend (`frontend/`):** TypeScript + React + Vite, built into static
  assets served directly by the backend container in production and proxied via
  Vite's dev server during local development.

## Secrets and credential encryption

Following Playbook Pillar II, `.env` holds only machine-local secrets — never
behavioral toggles `(decision 2026-11-18, bootstrap)`:

- `APP_SECRET_KEY` signs authentication sessions and derives the symmetric
  encryption key (`cryptography.fernet`) used to encrypt camera RTSP/HTTP
  passwords at rest in SQLite. Camera secrets are decrypted only in memory at
  capture time and are never returned in API responses or logs.
- `BOOTSTRAP_ADMIN_PASSWORD` seeds an initial `admin` user when starting
  against an empty database (used by `just dev` and headless deployments); if
  omitted on a fresh deployment, the Web UI presents a one-time initial admin
  setup screen.

## Persistent volume layout (`/data`)

```text
/data/
  db/
    timelapse.sqlite3                    # SQLite database (WAL mode)
  photos/
    <camera_slug>/<YYYY>/<MM>/<DD>/      # Untouched full-resolution originals
  previews/
    <camera_slug>/<YYYY>/<MM>/<DD>/      # 1080p WebP previews for instant UI playback
  cache/
    renders/                             # Ephemeral MP4 exports (reaped after 24h TTL)
```

`photos/`, `previews/`, and `cache/renders/` are the default container paths;
`Admin` users can point each tier individually to any mounted container volume
path via **Admin Console → Storage & Settings**, which verifies write access and
automatically migrates existing files without rewriting relative database paths
`(decision 2026-11-18, storage-paths)`.

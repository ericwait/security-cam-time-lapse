<!--
Policy (Practices Playbook Pillar VII): every release updates this file, under
the section for that version, with whichever of the six subsections apply.
This is not a request — the release flow gates on it mechanically: a release
whose diff doesn't touch this file fails the preflight. Keep entries terse and
user-facing; implementation detail belongs in commit messages, not here.

Format: Keep a Changelog (https://keepachangelog.com/en/1.1.0/).
Versioning: SemVer, computed from git history — never hand-edited here.
-->

# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- Configurable container storage paths for full-resolution photos, WebP previews, and ephemeral MP4 renders in **Admin Console → Storage & Settings**, with write-permission validation and automatic existing-file migration.
- Multi-user `Admin` and `Viewer` session authentication, `BOOTSTRAP_ADMIN_PASSWORD` initial seeding, first-run Web UI admin onboarding wizard, and self-service password rotation.
- Admin Console in the Web UI for managing RTSP, HTTP(S) (`Basic`/`Digest`/`Bearer`), and synthetic cameras with HKDF-Fernet encrypted credentials at rest, live connection preview testing, manual capture triggers, user management, system defaults, and host volume capacity telemetry.
- Scheduled dual-tier capture pipeline preserving untouched full-resolution JPEG originals under `<camera>/<YYYY>/<MM>/<DD>/` alongside 1080p WebP previews, plus deterministic DST-spanning synthetic courtyard camera history seeded by `just dev`.
- Interactive Timelapse Studio supporting Mode 1 (continuous window) and Mode 2 (daily time-of-day slice with local wall-clock vs. fixed standard-time offset DST evaluation), instant WebP sequence scrubbing, variable playback speed, and single-frame full-resolution zoom/download.
- On-demand H.264 (`yuv420p`) FFmpeg MP4 video export with selectable FPS (`10`/`24`/`30`/`60`), output resolution (`Native`/`4K`/`1080p`), optional per-frame historical timestamp watermark, live render progress, and automatic ephemeral TTL cache cleanup.
- Containerized FastAPI + React/Vite + SQLite (WAL) application substrate with `/api/health` readiness reporting, Alembic schema migrations, and non-root single-image runtime packaging.

### Changed

### Fixed

- Added native Reolink HTTP/HTTPS CGI (`cmd=Login` / `cmd=Snap` / `cmd=Logout`) snapshot negotiation, self-signed TLS support for private LAN camera IPs, automatic `/Preview_01_main` RTSP fallback for bare camera hosts, and inline camera connection error feedback in the Admin Console.

### Deprecated

### Removed

### Known Issues

<!-- ============================================================
     EXAMPLE RELEASE BLOCK — delete me once the first real release
     replaces this with an actual entry.
     ============================================================ -->

## [0.1.0] - YYYY-MM-DD

### Added

- [FILL: first user-facing capability shipped in this release]

### Changed

### Fixed

### Deprecated

### Removed

### Known Issues

- [FILL: anything known-broken or out of scope, shipped anyway and disclosed]

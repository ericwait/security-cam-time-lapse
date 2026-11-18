# Security Cam Time-Lapse

Self-hosted container application that captures full-resolution still images
from security cameras on a configurable schedule and plays them back dynamically
in a web interface — instantly in the browser or exported on demand as an MP4.

## Key capabilities

- **Multi-camera scheduled capture:** Connect cameras via RTSP streams (full-res
  keyframe extraction) or HTTP(S) snapshot endpoints, managed directly from the
  Web UI with encrypted-at-rest credentials.
- **Archive-grade storage + instant scrubbing:** Untouched full-resolution
  originals are stored in a clean `YYYY/MM/DD` directory tree for long-term
  retention alongside lightweight ~1080p previews so browser playback and
  timeline scrubbing start immediately.
- **Dynamic timelapse modes:**
  1. **Continuous Window:** Play every image captured between two timestamps.
  2. **Daily Time-of-Day Slice:** Play one image per day closest to a target
     wall-clock time (for example, `12:00 PM` from January 1 to June 1 within a
     `±15m` tolerance window).
  3. **Recurring Daily / Solar Window *(Phase 2)*:** Play multi-frame daily
     windows across dates (such as watching every sunset across a season).
- **On-demand MP4 export:** Render full-resolution downloadable MP4 videos via
  `ffmpeg`, backed by an automatic TTL render cache so temporary exports never
  fill your disk.
- **Role-based multi-user access:** `Admin` accounts configure cameras,
  schedules, users, and settings; `Viewer` accounts browse, play, zoom, and
  export timelapses across all enabled cameras.

## Quick start (development)

Prerequisites: Docker (with Compose v2), [`just`](https://github.com/casey/just),
and [`pre-commit`](https://pre-commit.com/).

```bash
cp .env.example .env
# Populate APP_SECRET_KEY and BOOTSTRAP_ADMIN_PASSWORD in .env (see comments):
openssl rand -hex 32
pre-commit install
just dev
just health
```

Run `just --list` to see every canonical operational command.

## Documentation

Start at [`docs/README.md`](docs/README.md):

- **Design (what & why):** [`Philosophy.md`](docs/design/Philosophy.md),
  [`Admin.md`](docs/design/Stakeholders/Admin.md),
  [`Viewer.md`](docs/design/Stakeholders/Viewer.md),
  [`Timelapse-Modes-and-Storage.md`](docs/design/Timelapse-Modes-and-Storage.md)
- **Development (how):** [`Architecture.md`](docs/development/Architecture.md),
  [`Getting-Started.md`](docs/development/Getting-Started.md),
  [`Testing.md`](docs/development/Testing.md)
- **Roadmap & Decisions:** [`Implementation-Plan.md`](docs/Implementation-Plan.md),
  [`decisions/README.md`](docs/decisions/README.md)
- **Contributing & Agent Context:** [`CONTRIBUTING.md`](CONTRIBUTING.md),
  [`CLAUDE.md`](CLAUDE.md)

## License

Released under the [BSD Zero Clause License (`0BSD`)](LICENSE) — free to use,
modify, and distribute for any purpose with zero restrictions.

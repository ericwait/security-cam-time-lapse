<!--
This index does not replace inline decision records — it exists only so a
decision can be found without knowing which design doc holds it. Keep the
table below in the same commit as the inline stamp it points to; a decision
recorded here but not there (or vice versa) is a broken link waiting to be
noticed the hard way.
-->

# Decisions index

This project has no separate decision-log format. Decisions are recorded
**inline, where they're made** — in the design or architecture doc discussing
the tradeoff, at the exact point of relevance — stamped like this:

```text
(decision YYYY-MM-DD, #issue)
```

When a later decision replaces an earlier one, the new entry says so
explicitly, in place:

```text
(decision YYYY-MM-DD, #issue) This supersedes the 2026-11-18 decision to
<what changed and why>.
```

## Rule

Adding an inline decision stamp means adding a row below, in the same commit.
An index row with no matching stamp, or a stamp with no matching row, means
someone skipped half the rule.

## Index

| Date | Decision (one line) | Recorded in | Status |
| --- | --- | --- | --- |
| 2026-11-18 | License the repository under Zero-Clause BSD (`0BSD`) for unrestricted use and derivatives | [../../CLAUDE.md](../../CLAUDE.md) | active |
| 2026-11-18 | Adopt `develop` integration branch + `main` release branch with merge-commit history for GitVersion | [../../CLAUDE.md](../../CLAUDE.md) | active |
| 2026-11-18 | Manage cameras, credentials, and schedules directly in the Web UI rather than static config files | [../design/Stakeholders/Admin.md](../design/Stakeholders/Admin.md) | active |
| 2026-11-18 | Use multi-user accounts with `Admin` (full management) and `Viewer` (browse/play/export all enabled cameras) roles | [../design/Stakeholders/Admin.md](../design/Stakeholders/Admin.md), [../design/Stakeholders/Viewer.md](../design/Stakeholders/Viewer.md) | active |
| 2026-11-18 | Support both RTSP stream keyframe capture and HTTP(S) snapshot endpoints per camera | [../design/Timelapse-Modes-and-Storage.md](../design/Timelapse-Modes-and-Storage.md) | active |
| 2026-11-18 | Store untouched full-res originals in `YYYY/MM/DD` folders alongside 1080p previews for smooth browser scrubbing | [../design/Timelapse-Modes-and-Storage.md](../design/Timelapse-Modes-and-Storage.md) | active |
| 2026-11-18 | Use hybrid playback (instant browser sequence player + on-demand server MP4 export) with ephemeral TTL render caching only | [../design/Timelapse-Modes-and-Storage.md](../design/Timelapse-Modes-and-Storage.md) | active |
| 2026-11-18 | Select at most one frame per day in Mode 2 (closest within `±tolerance`, skipping missing days) and defer Mode 3 (recurring daily/sunset windows) to Phase 2 | [../design/Timelapse-Modes-and-Storage.md](../design/Timelapse-Modes-and-Storage.md) | active |
| 2026-11-18 | Package Python (FastAPI + SQLite + Alembic + FFmpeg) and TypeScript (React + Vite) in a single non-root Docker image | [../development/Architecture.md](../development/Architecture.md) | active |
| 2026-11-18 | Restrict `.env` to secrets only and encrypt camera credentials at rest in SQLite using `APP_SECRET_KEY` | [../development/Architecture.md](../development/Architecture.md) | active |
| 2026-11-18 | Support both local wall-clock time and a fixed standard-time offset toggle for Mode 2 queries across DST transitions | [../design/Timelapse-Modes-and-Storage.md](../design/Timelapse-Modes-and-Storage.md) | active |
| 2026-11-18 | Default video exports to native camera resolution with selectable FPS (`10`/`24`/`30`/`60`), output resolution (`Native`/`4K`/`1080p`), and optional timestamp watermark | [../design/Timelapse-Modes-and-Storage.md](../design/Timelapse-Modes-and-Storage.md) | active |
| 2026-11-18 | Seed a synthetic dev camera and multi-month DST-spanning sample frames in `just dev` so playback and export work without live cameras | [../development/Architecture.md](../development/Architecture.md) | active |

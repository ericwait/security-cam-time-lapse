> Status: adopted | Audience: anyone working on capture, indexing, query math,
> or playback | See also: [Philosophy.md](Philosophy.md),
> [Stakeholders/Admin.md](Stakeholders/Admin.md),
> [Stakeholders/Viewer.md](Stakeholders/Viewer.md)

# Timelapse modes and storage

## Capture contract

Each configured camera captures full-sensor-resolution still images on a
regular schedule (typically every 15–60 minutes, retained indefinitely). A
camera may acquire its image from either a live video stream (extracting a
full-resolution keyframe) or an HTTP(S) still-image snapshot endpoint, selected
per camera `(decision 2026-11-18, bootstrap)`.

Captures record both the exact UTC instant and the camera's configured IANA
timezone so wall-clock queries remain unambiguous across Daylight Saving Time
transitions.

## Dual-tier storage and archive durability

Full-resolution security camera stills (often 4K / 8–12 MP, several megabytes
each) are too heavy to stream raw at 30 frames per second over a browser
connection, yet downsampling originals would destroy the archival value of the
capture.

To satisfy both requirements without compromise
`(decision 2026-11-18, bootstrap)`:

1. **Untouched full-resolution originals** are written to a human-navigable
   folder hierarchy (`<camera-slug>/<YYYY>/<MM>/<DD>/<timestamp>.jpg`) and
   retained indefinitely. If this application is ever retired, the photo
   archive on disk remains clean and self-explanatory.
2. **Lightweight preview frames** (~1080p compressed stills) are generated at
   capture time alongside the original so the browser player can scrub and play
   hundreds of frames smoothly.
3. **Full-resolution originals** are served whenever a user pauses/zooms into a
   single frame, downloads a still, or renders a downloadable timelapse video.

## Playback and ephemeral video export

Interactive exploration and shareable video export have different latency
requirements, so the system uses a hybrid playback model
`(decision 2026-11-18, bootstrap)`:

- **Instant browser sequence player:** Queries return an ordered manifest of
  matching frames. The web interface plays preview frames immediately on a
  canvas/sequence player with variable frame rate, timeline scrubbing, and
  single-frame full-resolution zoom — zero server encoding wait.
- **On-demand rendered video export:** Any query can be rendered on the server
  from the full-resolution originals into a downloadable video file. Exports
  default to native full camera resolution with universal browser/device
  compatibility, and let users choose frame rate (`10`, `24`, `30`, or `60`
  FPS), output resolution (`Native`, `4K`, or `1080p`), and an optional
  burned-in timestamp watermark `(decision 2026-11-18, bootstrap)`. Rendered
  videos live solely in an **ephemeral TTL cache** (for example, expiring
  automatically after 24 hours) rather than a permanent saved-video library, so
  exports never silently consume unbounded disk space
  `(decision 2026-11-18, bootstrap)`.

## Timelapse selection modes

### Mode 1 — Continuous window (Phase 1)

Selects **every captured image** for a camera whose timestamp falls inclusively
between `start_timestamp` and `end_timestamp`, ordered chronologically.

- *Example:* Watch everything captured on the driveway camera between Friday
  6:00 PM and Sunday 12:00 PM.

### Mode 2 — Daily time-of-day slice (Phase 1)

Selects **at most one image per calendar day** between `start_date` and
`end_date` (inclusive, evaluated in the camera's local IANA timezone): the
capture closest to the requested `target_time` within a configurable tolerance
window (`±tolerance_minutes`, default `±15` minutes), skipping any day that has
no capture inside the tolerance window `(decision 2026-11-18, bootstrap)`.

- *Example:* Noon (`12:00`) between January 1 and June 1 — producing one midday
  frame per day across five months so seasonal change plays smoothly without
  night/day flicker.
- *Tie-breaking:* If two captures on the same day are equidistant from
  `target_time`, the earlier capture wins deterministically.
- *DST reference mode:* Queries support evaluating `target_time` either in
  **local wall-clock time** (default, e.g., `12:00` winter standard time then
  `12:00` summer daylight time) or against a **fixed standard-time offset** for
  the camera's timezone so outdoor sun position does not jump by an hour across
  Daylight Saving Time transitions `(decision 2026-11-18, bootstrap)`.
- *DST edge cases:* In wall-clock mode, on spring-forward days where a
  wall-clock hour does not exist (e.g., `02:30`), only captures whose local
  wall-clock distance falls within tolerance match; on fall-back days where a
  wall-clock hour repeats, the first occurrence is preferred unless otherwise
  specified.

### Mode 3 — Recurring daily window / solar event (Phase 2)

Selects **multiple images per day** within a recurring daily time window or
astronomical event window (such as `sunset - 20m` to `sunset + 20m`) across a
date range, stitching each day's mini-sequence into a multi-day event
timelapse.

Mode 3 is intentionally deferred to **Phase 2** so Phase 1 establishes capture,
storage, and Modes 1–2 cleanly before designing variable/solar capture cadences
and multi-clip transitions `(decision 2026-11-18, bootstrap)`.

> Status: adopted | Audience: anyone designing playback, browsing, or export
> features | See also: [../Philosophy.md](../Philosophy.md),
> [Admin.md](Admin.md)

# Viewer

An authenticated household member or invited user who explores historical
camera imagery and watches or downloads timelapses.

## Domain of awareness

- Which camera view they want to inspect and the time span or recurring time of
  day they are curious about (for example, watching a garden grow at noon from
  January through June, or reviewing yesterday's snowfall).
- Their own account password.

## Data this role keeps current

- Only their own account credentials (password updates) and ephemeral playback
  query parameters during a browsing session. Because `Viewer` is a consumption
  role, imposing persistent data-entry obligations on it violates the
  [design failure test](../Philosophy.md#the-design-failure-test).

## Value this role receives

- Instant, interactive timelapse playback in the browser across all enabled
  cameras — with frame scrubbing, playback speed control, and full-resolution
  still inspection — without waiting for a video file to encode first.
- One-click export of any timelapse query to a standard downloadable video file
  for sharing or offline viewing `(decision 2026-11-18, bootstrap)`.

## Not expected to

- Configure or troubleshoot cameras, capture intervals, or credentials.
- Manage user accounts or system settings.
- Know or care how images are captured, indexed, or cached on disk.

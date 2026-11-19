> Status: adopted | Audience: anyone designing a feature that touches
> administration | See also: [../Philosophy.md](../Philosophy.md),
> [Viewer.md](Viewer.md)

# Admin

The household or site operator responsible for connecting security cameras,
setting capture schedules, managing user access, and keeping long-term image
capture healthy.

## Domain of awareness

- Which cameras exist on the network, their stream or snapshot endpoints, and
  their authentication credentials.
- Whether scheduled captures are succeeding or failing per camera, and how much
  storage capacity remains on the host volume.
- Which people should have `Admin` versus `Viewer` accounts.

## Data this role keeps current

- Camera definitions, connection URLs, encrypted credentials, enabled/disabled
  state, and capture intervals — all managed directly through the Web UI rather
  than static configuration files on disk `(decision 2026-11-18, bootstrap)`.
- User accounts and their assigned role (`Admin` or `Viewer`)
  `(decision 2026-11-18, bootstrap)`.
- System-level settings (storage directory paths for full-res photos, previews,
  and ephemeral render cache with automatic file relocation, default timezone,
  render cache TTL, and tolerance defaults) `(decision 2026-11-18, storage-paths)`.

## Value this role receives

- Confidence that full-resolution visual history is being captured reliably on
  schedule without manual intervention, backed by clear per-camera health
  status and immediate "test capture" verification when adding or editing a
  camera.
- Every playback capability a [Viewer](Viewer.md) receives, plus self-contained
  browser administration with no SSH or container restarts needed to add a
  camera or invite a family member.

## Not expected to

- SSH into the host or edit configuration files to add, update, or pause a
  camera.
- Manually clean up temporary rendered timelapse videos or manage preview
  thumbnails.
- Curate per-camera ACL matrices for each viewer; all enabled cameras are
  visible to authenticated viewers `(decision 2026-11-18, bootstrap)`.

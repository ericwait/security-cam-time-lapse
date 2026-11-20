> Status: adopted | Audience: operators and contributors configuring external
> drives, NAS mounts, or storage migrations | See also:
> [Architecture.md](Architecture.md), [Getting-Started.md](Getting-Started.md),
> [../design/Timelapse-Modes-and-Storage.md](../design/Timelapse-Modes-and-Storage.md)

# Storage setup & migration

By default, all four persistent tiers live inside the `timelapse_data` Docker
named volume mounted at `/data`:

| Tier | Default container path | Purpose | Recommended storage medium |
| --- | --- | --- | --- |
| **SQLite database** | `/data/db/timelapse.sqlite3` | Users, encrypted camera credentials, frame index (`WAL` mode) | **Local SSD / host disk** (never SMB/NFS — SQLite WAL file locking is unsafe on network shares) |
| **Full-res originals** | `/data/photos` | Untouched `<camera>/<YYYY>/<MM>/<DD>/*.jpg` archive | **External HDD / SSD / NAS share** (largest footprint) |
| **WebP previews** | `/data/previews` | Lightweight ~1080p WebP sequence frames for instant scrubbing | **Local SSD** (keeps timeline scrubbing instant even when a NAS spins down) or external drive |
| **MP4 render cache** | `/data/cache/renders` | Ephemeral on-demand FFmpeg MP4 exports (reaped after TTL) | **Local SSD** or scratch disk |

Moving `photos`, `previews`, or `renders` to another drive or NAS is a two-step
process:

1. **Attach the host/NAS folder into the `app` container** via Docker Compose.
2. **Point the desired tier(s) to that container path in the Web UI** — existing
   files migrate automatically with zero database edits.

---

## Option A — Mount an external drive or NAS & live-migrate via Web UI (recommended)

This keeps `/data/db/timelapse.sqlite3` safely on local disk while moving bulky
captures to your external drive or NAS, and automatically migrates any frames
you have already captured.

### 1. Attach the host or NAS path in Docker Compose

#### Running from a git checkout (`just dev` / `just up`)

Create a `docker-compose.override.yml` file in the repository root. It is
gitignored so your personal host/NAS path will never dirty `git status` or be
committed, and Docker Compose merges it automatically whenever you run `just up`
or `just dev`:

```yaml
# docker-compose.override.yml (gitignored)
services:
  app:
    volumes:
      - /Volumes/MyNAS/SecurityCams:/mnt/archive
```

*(Replace `/Volumes/MyNAS/SecurityCams` with any existing folder on your Mac,
Linux host, external SSD, or mounted NAS volume.)*

#### Running a standalone single-container production deployment

If you run the compiled `runtime` target directly without the dev `web`
container, mount both `/data` (for SQLite) and your external archive path on the
single service:

```yaml
services:
  app:
    build:
      context: .
      dockerfile: Dockerfile
      target: runtime
    env_file:
      - .env
    ports:
      - "8000:8000"
    volumes:
      - timelapse_data:/data
      - /Volumes/MyNAS/SecurityCams:/mnt/archive
    restart: unless-stopped

volumes:
  timelapse_data:
```

### 2. Check filesystem permissions (`uid=1000`)

The container runs as non-root `appuser` (`uid=1000`, `gid=1000`):

- **macOS (OrbStack / Docker Desktop):** Bind-mounted host folders and mounted
  `/Volumes/...` shares automatically map to your macOS user — no `chown`
  needed as long as your Mac user can write to the folder.
- **Linux hosts / NFS mounts:** Ensure UID `1000` can write to the host folder
  before migrating:
  ```bash
  sudo mkdir -p /mnt/nas/security-cams
  sudo chown -R 1000:1000 /mnt/nas/security-cams
  ```

### 3. Apply the mount

Run:

```bash
just up
```

`docker compose up -d` recreates the `app` container in ~2 seconds with
`/mnt/archive` attached while keeping your existing `timelapse_data` volume,
users, cameras, and captured frames intact.

### 4. Update paths & auto-migrate in the Web UI

1. Sign in as an `Admin` and open **Admin Console → Storage & Settings**.
2. Under **Container Storage Directories**, update whichever tier(s) you want to
   relocate — for example:
   - **Full-Res Photos Directory:** `/mnt/archive/photos`
   - *(Optional)* **WebP Previews Directory:** `/mnt/archive/previews` (or leave
     at `/data/previews` to keep browser scrubbing on fast local storage)
   - *(Optional)* **MP4 Render Cache Directory:** `/mnt/archive/renders` (or
     leave at `/data/cache/renders`)
   - *Rule:* The three directories must be distinct, non-overlapping paths (e.g.
     do not set two tiers to `/mnt/archive` or nest one inside another).
3. Click **Save Settings**.

#### What happens automatically when you click Save Settings

- **Pre-flight safety & write checks:** The backend blocks restricted system
  paths, SQLite database directories, and overlapping tier paths (`422`),
  checks that no MP4 export render is currently mid-flight if `renders_dir` is
  changing (`409`), and writes/deletes a temporary probe file inside each target
  directory (`400` if the mount is missing or read-only).
- **Automatic cross-volume migration with rollback:** Every existing file in
  each changed tier is moved (`shutil.move`) to the new container path while
  preserving `<camera_slug>/<YYYY>/<MM>/<DD>/...` subdirectories. If an I/O
  error occurs mid-copy (for example, the target disk fills up), already-moved
  files are automatically rolled back to the original directory and settings
  remain unchanged.
- **Zero broken links:** Because SQLite stores paths relative to each tier root,
  every historical frame preview, full-resolution download, and cached MP4
  continues working immediately without rewriting database rows.
- **Updated capacity telemetry:** The **Volume Storage Capacity** card
  immediately switches its disk total/used/free readout to the filesystem
  backing **Full-Res Photos Directory** and displays a confirmation banner with
  the exact number of migrated files.

> **Moving again later (or reverting):** To move to a bigger drive later — or
> move back to `/data` via **Reset Paths to `/data` Defaults** — keep both the
> old and new mounts attached in `docker-compose.override.yml`, save the new
> path in **Storage & Settings** so files migrate across, and then remove the
> old mount line afterward.

---

## Option B — Move the entire `/data` volume (including SQLite) to a local host folder

If you prefer all of `/data` (including `/data/db/timelapse.sqlite3`) to live in
a visible folder on a **local** disk (APFS/ext4 — **do not** place SQLite WAL on
SMB/NFS network shares):

1. Stop the running containers (preserving volumes — **never** run `just reset`):
   ```bash
   just down
   ```
2. Create the target local folder and copy the existing Docker named volume into
   it:
   ```bash
   mkdir -p /path/to/local/timelapse-data
   docker run --rm \
     -v security-cam-time-lapse_timelapse_data:/from:ro \
     -v /path/to/local/timelapse-data:/to \
     alpine cp -a /from/. /to/
   ```
   *(On Linux hosts, ensure `chown -R 1000:1000 /path/to/local/timelapse-data`.)*
3. Override `/data` in `docker-compose.override.yml`:
   ```yaml
   services:
     app:
       volumes:
         - /path/to/local/timelapse-data:/data
   ```
4. Start the stack back up and verify health:
   ```bash
   just up
   just health
   ```

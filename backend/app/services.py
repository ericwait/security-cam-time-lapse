"""Core application services for preferences, bootstrap, capture, selection, and renders."""

import json
import os
import posixpath
import shutil
import threading
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, cast

import httpx
from fastapi import HTTPException
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.capture import (
    AuthType,
    CaptureError,
    CaptureSourceConfig,
    SourceType,
    fetch_frame_bytes,
    store_dual_tier_capture,
)
from app.config import Settings
from app.deps import coerce_utc, ensure_utc
from app.exporter import (
    ExportError,
    ExportFrameInput,
    ExportOptions,
    find_expired_renders,
    render_timelapse_mp4,
    unlink_render_artifact,
)
from app.models import Camera, CapturedFrame, RenderJob, SystemSetting, User
from app.schemas import (
    CameraAdminOut,
    CameraViewerOut,
    ContinuousTimelapseQuery,
    DailySliceTimelapseQuery,
    FrameOut,
    RenderJobOut,
    SystemPreferencesOut,
    SystemPreferencesUpdate,
    UserOut,
    UserRole,
)
from app.security import (
    MIN_PASSWORD_LENGTH,
    SecretDecryptionError,
    decrypt_secret,
    hash_password,
    scrub_secrets,
    slugify_camera_name,
    validate_iana_timezone,
)
from app.selection import (
    format_frame_local_timestamp,
    select_continuous_frames,
    select_daily_slice_frames,
)

SYSTEM_PREFERENCES_KEY: Final[str] = "system.preferences"
DEFAULT_TIMEZONE: Final[str] = "America/Chicago"
DEFAULT_RENDER_CACHE_TTL_HOURS: Final[int] = 24
DEFAULT_TOLERANCE_MINUTES: Final[int] = 15
DEFAULT_PHOTOS_DIR: Final[str] = "/data/photos"
DEFAULT_PREVIEWS_DIR: Final[str] = "/data/previews"
DEFAULT_RENDERS_DIR: Final[str] = "/data/cache/renders"

_FORBIDDEN_STORAGE_PREFIXES: Final[tuple[str, ...]] = (
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib64",
    "/proc",
    "/root",
    "/run",
    "/sbin",
    "/sys",
    "/usr",
    "/var/lib",
    "/var/log",
    "/var/run",
    "/var/spool",
    "/workspace",
    "/home/appuser/.venv",
)
_FORBIDDEN_EXACT_STORAGE_PATHS: Final[frozenset[str]] = frozenset(
    {"/", "/var", "/private", "/private/var"}
)
_STORAGE_MIGRATION_LOCK: Final[threading.RLock] = threading.RLock()


@dataclass(frozen=True, slots=True)
class EffectiveStoragePaths:
    """Resolved container storage directories for photos, previews, and render cache."""

    photos_dir: Path
    previews_dir: Path
    renders_dir: Path


@dataclass(frozen=True, slots=True)
class _AwareFrameAdapter:
    """UTC-normalized adapter satisfying SelectableFrame for SQLite ORM rows."""

    id: int
    captured_at_utc: datetime
    orm_frame: CapturedFrame


@dataclass(frozen=True, slots=True)
class _AwareRenderAdapter:
    """UTC-normalized adapter satisfying ExpirableRenderRecord for SQLite ORM rows."""

    id: str
    expires_at: datetime
    output_rel_path: str | None
    orm_job: RenderJob


def user_to_out(user: User) -> UserOut:
    """Serialize a User ORM model into UserOut."""
    return UserOut(
        id=user.id,
        username=user.username,
        role=cast(UserRole, user.role),
        is_active=user.is_active,
        created_at=ensure_utc(user.created_at),
    )


def frame_to_out(frame: CapturedFrame) -> FrameOut:
    """Serialize a CapturedFrame ORM model into FrameOut with local wall-clock label."""
    utc_dt = ensure_utc(frame.captured_at_utc)
    return FrameOut(
        id=frame.id,
        camera_id=frame.camera_id,
        captured_at_utc=utc_dt,
        local_timestamp=format_frame_local_timestamp(utc_dt, frame.camera_timezone),
        local_date=frame.local_date,
        width=frame.width,
        height=frame.height,
        size_bytes=frame.size_bytes,
        preview_url=f"/api/frames/{frame.id}/preview",
        original_url=f"/api/frames/{frame.id}/original",
        download_url=f"/api/frames/{frame.id}/original?download=1",
    )


def render_job_to_out(job: RenderJob) -> RenderJobOut:
    """Serialize a RenderJob ORM model into RenderJobOut."""
    download_url = f"/api/exports/{job.id}/download" if job.status == "completed" else None
    return RenderJobOut(
        id=job.id,
        camera_id=job.camera_id,
        mode=job.mode,
        fps=job.fps,
        resolution_preset=job.resolution_preset,
        watermark_timestamp=job.watermark_timestamp,
        status=job.status,
        frame_count=job.frame_count,
        processed_frames=job.processed_frames,
        output_size_bytes=job.output_size_bytes,
        error_message=job.error_message,
        created_at=ensure_utc(job.created_at),
        completed_at=ensure_utc(job.completed_at) if job.completed_at is not None else None,
        expires_at=ensure_utc(job.expires_at),
        download_url=download_url,
    )


def build_camera_stats_map(
    session: Session,
) -> dict[int, tuple[int, datetime | None, datetime | None]]:
    """Aggregate frame counts and earliest/latest capture instants per camera."""
    rows = session.execute(
        select(
            CapturedFrame.camera_id,
            func.count(CapturedFrame.id),
            func.min(CapturedFrame.captured_at_utc),
            func.max(CapturedFrame.captured_at_utc),
        ).group_by(CapturedFrame.camera_id)
    ).all()
    stats: dict[int, tuple[int, datetime | None, datetime | None]] = {}
    for camera_id, count_val, min_ts, max_ts in rows:
        earliest = coerce_utc(min_ts) if min_ts is not None else None
        latest = coerce_utc(max_ts) if max_ts is not None else None
        stats[int(camera_id)] = (int(count_val), earliest, latest)
    return stats


def camera_to_admin_out(camera: Camera, *, frame_count: int) -> CameraAdminOut:
    """Serialize a Camera ORM model for Admin views without exposing secrets."""
    return CameraAdminOut(
        id=camera.id,
        name=camera.name,
        slug=camera.slug,
        source_type=cast(SourceType, camera.source_type),
        source_url=camera.source_url,
        auth_type=cast(AuthType, camera.auth_type),
        auth_username=camera.auth_username,
        has_credentials=bool(camera.encrypted_secret),
        timezone=camera.timezone,
        interval_seconds=camera.interval_seconds,
        is_enabled=camera.is_enabled,
        frame_count=frame_count,
        last_capture_at=(
            ensure_utc(camera.last_capture_at) if camera.last_capture_at is not None else None
        ),
        last_capture_status=camera.last_capture_status,
        last_capture_error=camera.last_capture_error,
        created_at=ensure_utc(camera.created_at),
        updated_at=ensure_utc(camera.updated_at),
    )


def camera_to_viewer_out(
    camera: Camera,
    *,
    frame_count: int,
    earliest_capture_at: datetime | None,
    latest_capture_at: datetime | None,
) -> CameraViewerOut:
    """Serialize an enabled Camera ORM model for Viewer playback views."""
    return CameraViewerOut(
        id=camera.id,
        name=camera.name,
        slug=camera.slug,
        timezone=camera.timezone,
        interval_seconds=camera.interval_seconds,
        frame_count=frame_count,
        earliest_capture_at=earliest_capture_at,
        latest_capture_at=latest_capture_at,
    )


def _resolve_stored_dir(raw_val: object, fallback: str) -> str:
    if isinstance(raw_val, str):
        cleaned = raw_val.strip()
        if cleaned and "\x00" not in cleaned and cleaned.startswith("/"):
            return posixpath.normpath("/" + cleaned.lstrip("/"))
    return fallback


def get_system_preferences(
    session: Session,
    settings: Settings | None = None,
) -> SystemPreferencesOut:
    """Load global system preferences and active storage paths from SQLite with safe defaults."""
    default_photos = str(settings.photos_dir) if settings is not None else DEFAULT_PHOTOS_DIR
    default_previews = str(settings.previews_dir) if settings is not None else DEFAULT_PREVIEWS_DIR
    default_renders = str(settings.renders_dir) if settings is not None else DEFAULT_RENDERS_DIR

    setting = session.get(SystemSetting, SYSTEM_PREFERENCES_KEY)
    if setting is None:
        return SystemPreferencesOut(
            default_timezone=DEFAULT_TIMEZONE,
            render_cache_ttl_hours=DEFAULT_RENDER_CACHE_TTL_HOURS,
            default_tolerance_minutes=DEFAULT_TOLERANCE_MINUTES,
            photos_dir=default_photos,
            previews_dir=default_previews,
            renders_dir=default_renders,
            migrated_files_count=0,
        )

    try:
        raw = json.loads(setting.value_json)
    except json.JSONDecodeError:
        raw = {}

    if not isinstance(raw, dict):
        raw = {}

    raw_tz = raw.get("default_timezone", DEFAULT_TIMEZONE)
    try:
        resolved_tz = validate_iana_timezone(str(raw_tz))
    except ValueError:
        resolved_tz = DEFAULT_TIMEZONE

    raw_ttl = raw.get("render_cache_ttl_hours", DEFAULT_RENDER_CACHE_TTL_HOURS)
    resolved_ttl = (
        int(raw_ttl)
        if isinstance(raw_ttl, int) and not isinstance(raw_ttl, bool) and 1 <= raw_ttl <= 720
        else DEFAULT_RENDER_CACHE_TTL_HOURS
    )

    raw_tol = raw.get("default_tolerance_minutes", DEFAULT_TOLERANCE_MINUTES)
    resolved_tol = (
        int(raw_tol)
        if isinstance(raw_tol, int) and not isinstance(raw_tol, bool) and 0 <= raw_tol <= 720
        else DEFAULT_TOLERANCE_MINUTES
    )

    resolved_photos = _resolve_stored_dir(raw.get("photos_dir"), default_photos)
    resolved_previews = _resolve_stored_dir(raw.get("previews_dir"), default_previews)
    resolved_renders = _resolve_stored_dir(raw.get("renders_dir"), default_renders)

    return SystemPreferencesOut(
        default_timezone=resolved_tz,
        render_cache_ttl_hours=resolved_ttl,
        default_tolerance_minutes=resolved_tol,
        photos_dir=resolved_photos,
        previews_dir=resolved_previews,
        renders_dir=resolved_renders,
        migrated_files_count=0,
    )


def get_effective_storage_paths(
    session: Session,
    settings: Settings,
) -> EffectiveStoragePaths:
    """Resolve the active photos, previews, and render cache directories."""
    prefs = get_system_preferences(session, settings)
    return EffectiveStoragePaths(
        photos_dir=Path(prefs.photos_dir),
        previews_dir=Path(prefs.previews_dir),
        renders_dir=Path(prefs.renders_dir),
    )


def _validate_storage_path_safety(raw_path: str, settings: Settings | None) -> Path:
    norm_str = posixpath.normpath("/" + raw_path.strip().lstrip("/"))
    norm_path = Path(norm_str)
    try:
        resolved = Path(raw_path).resolve()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid storage directory path '{raw_path}': {exc}",
        ) from None

    if (
        str(norm_path) in _FORBIDDEN_EXACT_STORAGE_PATHS
        or str(resolved) in _FORBIDDEN_EXACT_STORAGE_PATHS
    ):
        raise HTTPException(
            status_code=422,
            detail=f"Storage directory '{raw_path}' cannot target a restricted system root",
        )

    for prefix in _FORBIDDEN_STORAGE_PREFIXES:
        prefix_path = Path(prefix)
        if norm_path == prefix_path or norm_path.is_relative_to(prefix_path):
            raise HTTPException(
                status_code=422,
                detail=f"Storage directory '{raw_path}' cannot be inside system path '{prefix}'",
            )
        if resolved == prefix_path or resolved.is_relative_to(prefix_path):
            raise HTTPException(
                status_code=422,
                detail=f"Storage directory '{raw_path}' cannot be inside system path '{prefix}'",
            )
        with suppress(OSError, RuntimeError):
            resolved_prefix = prefix_path.resolve()
            if resolved == resolved_prefix or resolved.is_relative_to(resolved_prefix):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Storage directory '{raw_path}' cannot be inside system path '{prefix}'"
                    ),
                )

    if settings is not None:
        db_file = settings.db_path.resolve()
        db_dir = db_file.parent
        if db_file.is_relative_to(resolved) or resolved.is_relative_to(db_dir):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Storage directory '{raw_path}' must not overlap or contain the SQLite "
                    f"database directory '{db_dir}'"
                ),
            )

    return resolved


def _validate_non_overlapping_paths(
    photos_resolved: Path,
    previews_resolved: Path,
    renders_resolved: Path,
) -> None:
    tiers = (photos_resolved, previews_resolved, renders_resolved)
    for idx, path_a in enumerate(tiers):
        for path_b in tiers[idx + 1 :]:
            if path_a == path_b or path_a.is_relative_to(path_b) or path_b.is_relative_to(path_a):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Photos, previews, and render cache directories must be "
                        "distinct non-overlapping paths"
                    ),
                )


def _probe_directory_writable(path: Path) -> None:
    probe_file = path / f".write_probe_{uuid.uuid4().hex}.tmp"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe_file.write_bytes(b"ok")
        probe_file.unlink()
    except OSError as exc:
        with suppress(OSError):
            probe_file.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"Storage directory '{path}' is not writable inside the container: {exc}",
        ) from None


def _collect_migratable_files(root_dir: Path) -> list[Path]:
    if not root_dir.is_dir():
        return []
    files: list[Path] = []
    for candidate in sorted(root_dir.rglob("*")):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        if candidate.name.endswith(".tmp") or candidate.name.startswith(".write_probe_"):
            continue
        files.append(candidate)
    return files


def _prune_empty_child_dirs(root_dir: Path) -> None:
    if not root_dir.is_dir():
        return
    subdirs = [item for item in root_dir.rglob("*") if item.is_dir() and not item.is_symlink()]
    for subdir in sorted(subdirs, key=lambda p: len(p.parts), reverse=True):
        if subdir == root_dir:
            continue
        with suppress(OSError):
            subdir.rmdir()


def _migrate_storage_tiers(tier_pairs: list[tuple[Path, Path]]) -> int:
    plans: list[tuple[Path, Path, list[Path]]] = []
    for old_dir, new_dir in tier_pairs:
        if old_dir.resolve() == new_dir.resolve() or not old_dir.exists():
            continue
        plans.append((old_dir, new_dir, _collect_migratable_files(old_dir)))

    moved_records: list[tuple[Path, Path]] = []
    for old_dir, new_dir, files in plans:
        try:
            for src in files:
                rel = src.relative_to(old_dir)
                dst = new_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                moved_records.append((dst, src))
        except OSError as exc:
            for moved_dst, orig_src in reversed(moved_records):
                with suppress(OSError):
                    orig_src.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(moved_dst), str(orig_src))
            raise HTTPException(
                status_code=500,
                detail=f"Failed to migrate existing files to '{new_dir}': {exc}",
            ) from None

    for old_dir, _new_dir, _files in plans:
        _prune_empty_child_dirs(old_dir)

    return len(moved_records)


def update_system_preferences(
    session: Session,
    payload: SystemPreferencesUpdate,
    settings: Settings | None = None,
) -> SystemPreferencesOut:
    """Validate, migrate storage files if relocated, and persist global system preferences."""
    with _STORAGE_MIGRATION_LOCK:
        current = get_system_preferences(session, settings)
        next_timezone = (
            payload.default_timezone
            if payload.default_timezone is not None
            else current.default_timezone
        )
        next_ttl = (
            payload.render_cache_ttl_hours
            if payload.render_cache_ttl_hours is not None
            else current.render_cache_ttl_hours
        )
        next_tolerance = (
            payload.default_tolerance_minutes
            if payload.default_tolerance_minutes is not None
            else current.default_tolerance_minutes
        )
        next_photos_str = payload.photos_dir or current.photos_dir
        next_previews_str = payload.previews_dir or current.previews_dir
        next_renders_str = payload.renders_dir or current.renders_dir

        path_updates = (payload.photos_dir, payload.previews_dir, payload.renders_dir)
        any_path_supplied = any(val is not None for val in path_updates)
        migrated_count = 0

        if any_path_supplied:
            photos_resolved = _validate_storage_path_safety(next_photos_str, settings)
            previews_resolved = _validate_storage_path_safety(next_previews_str, settings)
            renders_resolved = _validate_storage_path_safety(next_renders_str, settings)
            _validate_non_overlapping_paths(
                photos_resolved,
                previews_resolved,
                renders_resolved,
            )

            old_photos = Path(current.photos_dir)
            old_previews = Path(current.previews_dir)
            old_renders = Path(current.renders_dir)
            new_photos = Path(next_photos_str)
            new_previews = Path(next_previews_str)
            new_renders = Path(next_renders_str)

            if old_renders.resolve() != renders_resolved:
                running_renders = int(
                    session.scalar(
                        select(func.count())
                        .select_from(RenderJob)
                        .where(RenderJob.status == "running")
                    )
                    or 0
                )
                if running_renders > 0:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Wait for active MP4 export to finish before relocating the "
                            "render cache directory"
                        ),
                    )

            _probe_directory_writable(new_photos)
            _probe_directory_writable(new_previews)
            _probe_directory_writable(new_renders)

            migrated_count = _migrate_storage_tiers(
                [
                    (old_photos, new_photos),
                    (old_previews, new_previews),
                    (old_renders, new_renders),
                ]
            )

        merged = SystemPreferencesOut(
            default_timezone=next_timezone,
            render_cache_ttl_hours=next_ttl,
            default_tolerance_minutes=next_tolerance,
            photos_dir=next_photos_str,
            previews_dir=next_previews_str,
            renders_dir=next_renders_str,
            migrated_files_count=migrated_count,
        )

        now = datetime.now(UTC)
        serialized = json.dumps(merged.model_dump(exclude={"migrated_files_count"}))
        existing = session.get(SystemSetting, SYSTEM_PREFERENCES_KEY)
        if existing is None:
            session.add(
                SystemSetting(
                    key=SYSTEM_PREFERENCES_KEY,
                    value_json=serialized,
                    updated_at=now,
                )
            )
        else:
            existing.value_json = serialized
            existing.updated_at = now

        session.commit()
        return merged


def ensure_bootstrap_admin(
    session: Session,
    bootstrap_password: str | None,
) -> User | None:
    """Create the initial admin account when users is empty and bootstrap password is set."""
    if bootstrap_password is None or len(bootstrap_password) < MIN_PASSWORD_LENGTH:
        return None

    bind = session.get_bind()
    if not inspect(bind).has_table("users"):
        return None

    existing_count = session.scalar(select(func.count()).select_from(User)) or 0
    if existing_count > 0:
        return None

    now = datetime.now(UTC)
    admin = User(
        username="admin",
        password_hash=hash_password(bootstrap_password),
        role="admin",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return admin


def generate_unique_camera_slug(session: Session, name: str) -> str:
    """Derive a URL-safe camera slug from name, deduplicating with numeric suffixes."""
    base_slug = slugify_camera_name(name)[:108].rstrip("-")
    candidate = base_slug
    suffix = 2
    while session.scalar(select(Camera.id).where(Camera.slug == candidate)) is not None:
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
    return candidate


def decrypt_camera_secret(settings: Settings, camera: Camera) -> str | None:
    """Decrypt a camera's stored Fernet credential token in memory when configured."""
    if camera.auth_type == "none" or not camera.encrypted_secret:
        return None
    return decrypt_secret(settings.secret_key, camera.encrypted_secret)


def build_capture_source_config(camera: Camera, *, secret: str | None) -> CaptureSourceConfig:
    """Construct an immutable CaptureSourceConfig from a Camera ORM row."""
    return CaptureSourceConfig(
        camera_name=camera.name,
        camera_slug=camera.slug,
        source_type=cast(SourceType, camera.source_type),
        source_url=camera.source_url,
        auth_type=cast(AuthType, camera.auth_type),
        auth_username=camera.auth_username,
        secret=secret,
        timezone=camera.timezone,
    )


def execute_camera_capture(
    session: Session,
    settings: Settings,
    camera: Camera,
    *,
    captured_at_utc: datetime | None = None,
    http_transport: httpx.BaseTransport | None = None,
) -> CapturedFrame:
    """Acquire, dual-tier store, and index a single frame for camera with redacted errors."""
    decrypted_secret: str | None = None
    effective_utc = (
        ensure_utc(captured_at_utc) if captured_at_utc is not None else datetime.now(UTC)
    )
    paths = get_effective_storage_paths(session, settings)
    try:
        decrypted_secret = decrypt_camera_secret(settings, camera)
        source = build_capture_source_config(camera, secret=decrypted_secret)
        image_bytes = fetch_frame_bytes(
            source,
            captured_at_utc=effective_utc,
            http_transport=http_transport,
        )
        stored = store_dual_tier_capture(
            photos_dir=paths.photos_dir,
            previews_dir=paths.previews_dir,
            camera_slug=camera.slug,
            captured_at_utc=effective_utc,
            camera_timezone=camera.timezone,
            image_bytes=image_bytes,
        )
    except (CaptureError, SecretDecryptionError, ValueError, OSError) as exc:
        sanitized_error = scrub_secrets(str(exc), [decrypted_secret])
        now = datetime.now(UTC)
        camera.last_capture_status = "error"
        camera.last_capture_error = sanitized_error
        camera.updated_at = now
        session.commit()
        raise CaptureError(sanitized_error) from None
    finally:
        decrypted_secret = None

    now = datetime.now(UTC)
    existing_frame = session.scalar(
        select(CapturedFrame).where(
            CapturedFrame.camera_id == camera.id,
            CapturedFrame.original_rel_path == stored.original_rel_path,
        )
    )
    if existing_frame is not None:
        frame = existing_frame
    else:
        frame = CapturedFrame(
            camera_id=camera.id,
            captured_at_utc=stored.captured_at_utc,
            camera_timezone=stored.camera_timezone,
            local_date=stored.local_date,
            original_rel_path=stored.original_rel_path,
            preview_rel_path=stored.preview_rel_path,
            width=stored.width,
            height=stored.height,
            size_bytes=stored.size_bytes,
            created_at=now,
        )
        session.add(frame)

    camera.last_capture_at = stored.captured_at_utc
    camera.last_capture_status = "ok"
    camera.last_capture_error = None
    camera.updated_at = now
    session.commit()
    session.refresh(frame)
    return frame


def verify_camera_access(camera: Camera | None, user: User) -> Camera:
    """Verify camera exists and enforce disabled-camera 403 boundary for Viewers."""
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    if not camera.is_enabled and user.role != "admin":
        raise HTTPException(status_code=403, detail="Camera is disabled")
    return camera


def resolve_contained_file(root_dir: Path, rel_path: str) -> Path:
    """Resolve rel_path within root_dir, rejecting path traversal and missing files."""
    if not rel_path or not rel_path.strip():
        raise HTTPException(status_code=404, detail="File not found")

    resolved_root = root_dir.resolve()
    candidate = (resolved_root / rel_path).resolve()
    if candidate == resolved_root or not candidate.is_relative_to(resolved_root):
        raise HTTPException(status_code=403, detail="Invalid artifact path")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return candidate


def select_camera_frames(
    session: Session,
    camera: Camera,
    query: ContinuousTimelapseQuery | DailySliceTimelapseQuery,
) -> list[CapturedFrame]:
    """Load candidate frames for camera and execute exact Mode 1 or Mode 2 selection."""
    if isinstance(query, ContinuousTimelapseQuery):
        min_local_date = (query.start_timestamp - timedelta(days=2)).strftime("%Y-%m-%d")
        max_local_date = (query.end_timestamp + timedelta(days=2)).strftime("%Y-%m-%d")
    else:
        min_local_date = (query.start_date - timedelta(days=2)).isoformat()
        max_local_date = (query.end_date + timedelta(days=2)).isoformat()

    stmt = (
        select(CapturedFrame)
        .where(
            CapturedFrame.camera_id == camera.id,
            CapturedFrame.local_date >= min_local_date,
            CapturedFrame.local_date <= max_local_date,
        )
        .order_by(CapturedFrame.captured_at_utc.asc(), CapturedFrame.id.asc())
    )
    rows = session.scalars(stmt).all()
    adapters = [
        _AwareFrameAdapter(
            id=row.id,
            captured_at_utc=ensure_utc(row.captured_at_utc),
            orm_frame=row,
        )
        for row in rows
    ]

    if isinstance(query, ContinuousTimelapseQuery):
        matched = select_continuous_frames(
            adapters,
            start_timestamp=query.start_timestamp,
            end_timestamp=query.end_timestamp,
        )
    else:
        tolerance = (
            query.tolerance_minutes
            if query.tolerance_minutes is not None
            else get_system_preferences(session).default_tolerance_minutes
        )
        matched = select_daily_slice_frames(
            adapters,
            start_date=query.start_date,
            end_date=query.end_date,
            target_time=query.target_time,
            camera_timezone=camera.timezone,
            tolerance_minutes=tolerance,
            dst_mode=query.dst_mode,
        )

    return [item.orm_frame for item in matched]


def reap_expired_render_jobs(
    session: Session,
    settings: Settings,
    *,
    now_utc: datetime | None = None,
) -> int:
    """Unlink expired cached MP4 exports and transition their RenderJob rows to expired."""
    resolved_now = ensure_utc(now_utc) if now_utc is not None else datetime.now(UTC)
    candidates = session.scalars(
        select(RenderJob).where(
            (RenderJob.status != "expired") | RenderJob.output_rel_path.is_not(None)
        )
    ).all()
    if not candidates:
        return 0

    adapters = [
        _AwareRenderAdapter(
            id=job.id,
            expires_at=ensure_utc(job.expires_at),
            output_rel_path=job.output_rel_path,
            orm_job=job,
        )
        for job in candidates
    ]
    expired = find_expired_renders(adapters, now_utc=resolved_now)
    if not expired:
        return 0

    paths = get_effective_storage_paths(session, settings)
    reaped_count = 0
    for item in expired:
        with suppress(ValueError):
            unlink_render_artifact(paths.renders_dir, item.output_rel_path)
        job = item.orm_job
        if job.status != "expired" or job.output_rel_path is not None:
            reaped_count += 1
        job.status = "expired"
        job.output_rel_path = None
        job.output_size_bytes = None

    session.commit()
    return reaped_count


def run_render_job(
    session_factory: sessionmaker[Session],
    settings: Settings,
    job_id: str,
    frame_inputs: list[ExportFrameInput],
    options: ExportOptions,
) -> None:
    """Execute FFmpeg MP4 rendering for job_id and persist progress and completion state."""
    with _STORAGE_MIGRATION_LOCK, session_factory() as session:
        job = session.get(RenderJob, job_id)
        if job is None:
            return
        paths = get_effective_storage_paths(session, settings)
        job.status = "running"
        session.commit()

    output_rel_path = f"{job_id}.mp4"
    output_path = paths.renders_dir / output_rel_path
    last_reported = 0

    def _on_progress(processed: int, _total: int) -> None:
        nonlocal last_reported
        if processed <= last_reported:
            return
        last_reported = processed
        with session_factory() as progress_session:
            active_job = progress_session.get(RenderJob, job_id)
            if active_job is not None and active_job.status == "running":
                active_job.processed_frames = processed
                progress_session.commit()

    try:
        result = render_timelapse_mp4(
            frame_inputs,
            output_path=output_path,
            options=options,
            on_progress=_on_progress,
        )
    except (ExportError, ValueError, OSError) as exc:
        output_path.unlink(missing_ok=True)
        with session_factory() as err_session:
            failed_job = err_session.get(RenderJob, job_id)
            if failed_job is not None:
                failed_job.status = "failed"
                failed_job.error_message = str(exc)
                failed_job.completed_at = datetime.now(UTC)
                err_session.commit()
        return

    with session_factory() as done_session:
        completed_job = done_session.get(RenderJob, job_id)
        if completed_job is None:
            output_path.unlink(missing_ok=True)
            return
        completed_job.status = "completed"
        completed_job.processed_frames = result.frame_count
        completed_job.output_rel_path = output_rel_path
        completed_job.output_size_bytes = result.size_bytes
        completed_job.completed_at = datetime.now(UTC)
        completed_job.error_message = None
        done_session.commit()


def compute_directory_bytes(directory: Path) -> int:
    """Sum regular file byte sizes recursively under directory."""
    if not directory.is_dir():
        return 0
    total = 0
    for root, _dirs, filenames in os.walk(directory):
        root_path = Path(root)
        for filename in filenames:
            file_path = root_path / filename
            try:
                if not file_path.is_symlink():
                    total += file_path.stat().st_size
            except OSError:
                continue
    return total

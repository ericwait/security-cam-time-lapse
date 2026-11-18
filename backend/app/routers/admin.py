"""Admin endpoints for users, cameras, live capture probes, settings, and storage."""

import base64
import shutil
from contextlib import suppress
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.capture import CaptureError, CaptureSourceConfig, probe_camera_capture
from app.config import Settings
from app.deps import (
    get_db_session,
    get_http_transport,
    get_settings,
    require_admin_user,
)
from app.exporter import unlink_render_artifact
from app.models import Camera, CapturedFrame, RenderJob, User, UserSession
from app.schemas import (
    AdminUserCreate,
    AdminUserUpdate,
    CameraAdminOut,
    CameraConnectionTestRequest,
    CameraCreate,
    CameraProbeOut,
    CameraUpdate,
    FrameOut,
    StorageStatsOut,
    SystemPreferencesOut,
    SystemPreferencesUpdate,
    UserOut,
)
from app.security import (
    SecretDecryptionError,
    encrypt_secret,
    hash_password,
    scrub_secrets,
)
from app.services import (
    build_camera_stats_map,
    build_capture_source_config,
    camera_to_admin_out,
    compute_directory_bytes,
    decrypt_camera_secret,
    execute_camera_capture,
    frame_to_out,
    generate_unique_camera_slug,
    get_system_preferences,
    reap_expired_render_jobs,
    update_system_preferences,
    user_to_out,
)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_user)],
)


def _count_active_admins(session: Session) -> int:
    stmt = (
        select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))
    )
    return int(session.scalar(stmt) or 0)


@router.get("/users", response_model=list[UserOut])
def list_users(
    session: Annotated[Session, Depends(get_db_session)],
) -> list[UserOut]:
    """List all provisioned user accounts ordered by ID."""
    users = session.scalars(select(User).order_by(User.id.asc())).all()
    return [user_to_out(user) for user in users]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: AdminUserCreate,
    session: Annotated[Session, Depends(get_db_session)],
) -> UserOut:
    """Create a new Admin or Viewer user account."""
    existing_id = session.scalar(select(User.id).where(User.username == payload.username))
    if existing_id is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Username {payload.username!r} is already taken",
        )

    now = datetime.now(UTC)
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Username {payload.username!r} is already taken",
        ) from None

    session.refresh(user)
    return user_to_out(user)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: AdminUserUpdate,
    session: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_admin_user)],
) -> UserOut:
    """Update a user's role, active status, or password with last-admin protection."""
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    next_role = payload.role if payload.role is not None else target.role
    next_active = payload.is_active if payload.is_active is not None else target.is_active

    if target.id == current_user.id:
        if next_role != "admin":
            raise HTTPException(status_code=400, detail="Cannot demote your own admin account")
        if not next_active:
            raise HTTPException(status_code=400, detail="Cannot deactivate your own admin account")

    if (
        target.role == "admin"
        and target.is_active
        and (next_role != "admin" or not next_active)
        and _count_active_admins(session) <= 1
    ):
        raise HTTPException(
            status_code=400,
            detail="Cannot demote or deactivate the last active admin",
        )

    target.role = next_role
    target.is_active = next_active
    if payload.password is not None:
        target.password_hash = hash_password(payload.password)
    target.updated_at = datetime.now(UTC)

    if not target.is_active:
        session.execute(delete(UserSession).where(UserSession.user_id == target.id))

    session.commit()
    session.refresh(target)
    return user_to_out(target)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    session: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_admin_user)],
) -> None:
    """Delete a user account while preventing self-deletion or removing the last Admin."""
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    if target.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own admin account")

    if target.role == "admin" and target.is_active and _count_active_admins(session) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last active admin")

    session.execute(delete(UserSession).where(UserSession.user_id == target.id))
    session.delete(target)
    session.commit()


@router.get("/cameras", response_model=list[CameraAdminOut])
def list_admin_cameras(
    session: Annotated[Session, Depends(get_db_session)],
) -> list[CameraAdminOut]:
    """List all configured cameras with operational telemetry and redacted credentials."""
    cameras = session.scalars(select(Camera).order_by(Camera.id.asc())).all()
    stats = build_camera_stats_map(session)
    return [
        camera_to_admin_out(camera, frame_count=stats.get(camera.id, (0, None, None))[0])
        for camera in cameras
    ]


@router.post("/cameras", response_model=CameraAdminOut, status_code=201)
def create_camera(
    payload: CameraCreate,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CameraAdminOut:
    """Register a new RTSP, HTTP(S), or synthetic camera with encrypted secret storage."""
    if payload.slug is not None:
        existing_id = session.scalar(select(Camera.id).where(Camera.slug == payload.slug))
        if existing_id is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Camera slug {payload.slug!r} already exists",
            )
        slug = payload.slug
    else:
        try:
            slug = generate_unique_camera_slug(session, payload.name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    if payload.auth_type == "none":
        auth_username: str | None = None
        encrypted_secret: str | None = None
    else:
        auth_username = (
            payload.auth_username.strip()
            if payload.auth_username and payload.auth_username.strip()
            else None
        )
        encrypted_secret = (
            encrypt_secret(settings.secret_key, payload.secret) if payload.secret else None
        )

    now = datetime.now(UTC)
    camera = Camera(
        name=payload.name,
        slug=slug,
        source_type=payload.source_type,
        source_url=payload.source_url,
        auth_type=payload.auth_type,
        auth_username=auth_username,
        encrypted_secret=encrypted_secret,
        timezone=payload.timezone,
        interval_seconds=payload.interval_seconds,
        is_enabled=payload.is_enabled,
        created_at=now,
        updated_at=now,
    )
    session.add(camera)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Camera slug {slug!r} already exists",
        ) from None

    session.refresh(camera)
    return camera_to_admin_out(camera, frame_count=0)


@router.post("/cameras/test-connection", response_model=CameraProbeOut)
def test_camera_connection(
    payload: CameraConnectionTestRequest,
    http_transport: Annotated[httpx.BaseTransport | None, Depends(get_http_transport)],
) -> CameraProbeOut:
    """Probe a candidate camera connection in memory and return an inline WebP preview."""
    cleaned_name = payload.name.strip() or "Connection Test"
    secret = payload.secret if payload.auth_type != "none" else None
    username = (
        payload.auth_username.strip()
        if payload.auth_type != "none" and payload.auth_username and payload.auth_username.strip()
        else None
    )
    source = CaptureSourceConfig(
        camera_name=cleaned_name,
        camera_slug="connection-test",
        source_type=payload.source_type,
        source_url=payload.source_url,
        auth_type=payload.auth_type,
        auth_username=username,
        secret=secret,
        timezone=payload.timezone,
    )
    try:
        preview_bytes, width, height = probe_camera_capture(
            source,
            http_transport=http_transport,
        )
    except CaptureError as exc:
        detail = scrub_secrets(str(exc), [secret])
        raise HTTPException(status_code=502, detail=detail) from None

    encoded = base64.b64encode(preview_bytes).decode("ascii")
    return CameraProbeOut(
        ok=True,
        width=width,
        height=height,
        preview_data_url=f"data:image/webp;base64,{encoded}",
    )


@router.patch("/cameras/{camera_id}", response_model=CameraAdminOut)
def update_camera(
    camera_id: int,
    payload: CameraUpdate,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CameraAdminOut:
    """Update a camera's schedule, source URL, or encrypted credentials."""
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    if payload.name is not None:
        camera.name = payload.name
    if payload.source_type is not None:
        camera.source_type = payload.source_type
    if payload.source_url is not None:
        camera.source_url = payload.source_url
    if payload.timezone is not None:
        camera.timezone = payload.timezone
    if payload.interval_seconds is not None:
        camera.interval_seconds = payload.interval_seconds
    if payload.is_enabled is not None:
        camera.is_enabled = payload.is_enabled

    effective_auth = payload.auth_type if payload.auth_type is not None else camera.auth_type
    camera.auth_type = effective_auth
    if effective_auth == "none":
        camera.auth_username = None
        camera.encrypted_secret = None
    else:
        if "auth_username" in payload.model_fields_set:
            camera.auth_username = (
                payload.auth_username.strip()
                if payload.auth_username and payload.auth_username.strip()
                else None
            )
        if payload.secret:
            camera.encrypted_secret = encrypt_secret(settings.secret_key, payload.secret)

    camera.updated_at = datetime.now(UTC)
    session.commit()
    session.refresh(camera)

    frame_count = int(
        session.scalar(
            select(func.count())
            .select_from(CapturedFrame)
            .where(CapturedFrame.camera_id == camera.id)
        )
        or 0
    )
    return camera_to_admin_out(camera, frame_count=frame_count)


@router.delete("/cameras/{camera_id}", status_code=204)
def delete_camera(
    camera_id: int,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Delete a camera and remove its archived photos, previews, and cached renders."""
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    render_paths = session.scalars(
        select(RenderJob.output_rel_path).where(RenderJob.camera_id == camera.id)
    ).all()
    for rel_path in render_paths:
        with suppress(ValueError):
            unlink_render_artifact(settings.renders_dir, rel_path)

    slug = camera.slug
    session.delete(camera)
    session.commit()

    shutil.rmtree(settings.photos_dir / slug, ignore_errors=True)
    shutil.rmtree(settings.previews_dir / slug, ignore_errors=True)


@router.post("/cameras/{camera_id}/test-capture", response_model=CameraProbeOut)
def test_saved_camera_capture(
    camera_id: int,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    http_transport: Annotated[httpx.BaseTransport | None, Depends(get_http_transport)],
) -> CameraProbeOut:
    """Probe a persisted camera using its stored encrypted credentials in memory."""
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    decrypted_secret: str | None = None
    try:
        decrypted_secret = decrypt_camera_secret(settings, camera)
        source = build_capture_source_config(camera, secret=decrypted_secret)
        preview_bytes, width, height = probe_camera_capture(
            source,
            http_transport=http_transport,
        )
    except (CaptureError, SecretDecryptionError) as exc:
        detail = scrub_secrets(str(exc), [decrypted_secret])
        raise HTTPException(status_code=502, detail=detail) from None
    finally:
        decrypted_secret = None

    encoded = base64.b64encode(preview_bytes).decode("ascii")
    return CameraProbeOut(
        ok=True,
        width=width,
        height=height,
        preview_data_url=f"data:image/webp;base64,{encoded}",
    )


@router.post("/cameras/{camera_id}/capture-now", response_model=FrameOut, status_code=201)
def capture_camera_now(
    camera_id: int,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    http_transport: Annotated[httpx.BaseTransport | None, Depends(get_http_transport)],
) -> FrameOut:
    """Trigger an immediate capture, dual-tier persistence, and index insertion."""
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    try:
        frame = execute_camera_capture(
            session,
            settings,
            camera,
            http_transport=http_transport,
        )
    except CaptureError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None

    return frame_to_out(frame)


@router.get("/settings", response_model=SystemPreferencesOut)
def get_admin_settings(
    session: Annotated[Session, Depends(get_db_session)],
) -> SystemPreferencesOut:
    """Return global operational defaults."""
    return get_system_preferences(session)


@router.put("/settings", response_model=SystemPreferencesOut)
def put_admin_settings(
    payload: SystemPreferencesUpdate,
    session: Annotated[Session, Depends(get_db_session)],
) -> SystemPreferencesOut:
    """Update global operational defaults."""
    return update_system_preferences(session, payload)


@router.get("/storage", response_model=StorageStatsOut)
def get_storage_stats(
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StorageStatsOut:
    """Return filesystem capacity and byte breakdown across photos, previews, and renders."""
    reap_expired_render_jobs(session, settings)
    disk = shutil.disk_usage(settings.data_dir)
    total_frames = int(session.scalar(select(func.count()).select_from(CapturedFrame)) or 0)
    active_renders = int(
        session.scalar(
            select(func.count())
            .select_from(RenderJob)
            .where(RenderJob.status.in_(("queued", "running", "completed")))
        )
        or 0
    )
    return StorageStatsOut(
        disk_total_bytes=disk.total,
        disk_used_bytes=disk.used,
        disk_free_bytes=disk.free,
        photos_bytes=compute_directory_bytes(settings.photos_dir),
        previews_bytes=compute_directory_bytes(settings.previews_dir),
        renders_bytes=compute_directory_bytes(settings.renders_dir),
        total_frames=total_frames,
        active_renders=active_renders,
    )

"""Authenticated Viewer and Admin endpoints for cameras, playback, frames, and exports."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, cast
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.deps import ensure_utc, get_db_session, get_settings, require_authenticated_user
from app.exporter import ExportFrameInput, ExportOptions
from app.models import Camera, CapturedFrame, RenderJob, User
from app.schemas import (
    CameraViewerOut,
    ContinuousTimelapseQuery,
    DailySliceTimelapseQuery,
    ExportCreateRequest,
    RenderJobOut,
    TimelapseQueryResponse,
)
from app.selection import format_frame_local_timestamp
from app.services import (
    build_camera_stats_map,
    camera_to_viewer_out,
    frame_to_out,
    get_effective_storage_paths,
    get_system_preferences,
    reap_expired_render_jobs,
    render_job_to_out,
    resolve_contained_file,
    run_render_job,
    select_camera_frames,
    verify_camera_access,
)

router = APIRouter(
    prefix="/api",
    tags=["timelapse"],
    dependencies=[Depends(require_authenticated_user)],
)


@router.get("/cameras", response_model=list[CameraViewerOut])
def list_enabled_cameras(
    session: Annotated[Session, Depends(get_db_session)],
) -> list[CameraViewerOut]:
    """List all enabled cameras with frame counts and capture range bounds."""
    cameras = session.scalars(
        select(Camera).where(Camera.is_enabled.is_(True)).order_by(Camera.id.asc())
    ).all()
    stats = build_camera_stats_map(session)
    results: list[CameraViewerOut] = []
    for camera in cameras:
        count, earliest, latest = stats.get(camera.id, (0, None, None))
        results.append(
            camera_to_viewer_out(
                camera,
                frame_count=count,
                earliest_capture_at=earliest,
                latest_capture_at=latest,
            )
        )
    return results


@router.post("/timelapse/query", response_model=TimelapseQueryResponse)
def query_timelapse_frames(
    payload: Annotated[
        ContinuousTimelapseQuery | DailySliceTimelapseQuery,
        Body(discriminator="mode"),
    ],
    session: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_authenticated_user)],
) -> TimelapseQueryResponse:
    """Select an ordered sequence of frames for Mode 1 continuous or Mode 2 daily slice."""
    camera = verify_camera_access(session.get(Camera, payload.camera_id), current_user)
    selected = select_camera_frames(session, camera, payload)
    return TimelapseQueryResponse(
        camera_id=camera.id,
        camera_name=camera.name,
        camera_timezone=camera.timezone,
        mode=payload.mode,
        frame_count=len(selected),
        frames=[frame_to_out(frame) for frame in selected],
    )


@router.get("/frames/{frame_id}/preview")
def get_frame_preview(
    frame_id: int,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(require_authenticated_user)],
) -> FileResponse:
    """Serve the lightweight ~1080p WebP preview image for interactive scrubbing."""
    frame = session.get(CapturedFrame, frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="Frame not found")

    verify_camera_access(session.get(Camera, frame.camera_id), current_user)
    paths = get_effective_storage_paths(session, settings)
    file_path = resolve_contained_file(paths.previews_dir, frame.preview_rel_path)
    return FileResponse(file_path, media_type="image/webp")


@router.get("/frames/{frame_id}/original")
def get_frame_original(
    frame_id: int,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(require_authenticated_user)],
    download: bool = False,
) -> FileResponse:
    """Serve the untouched full-resolution original JPEG for zoom or download."""
    frame = session.get(CapturedFrame, frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="Frame not found")

    camera = verify_camera_access(session.get(Camera, frame.camera_id), current_user)
    paths = get_effective_storage_paths(session, settings)
    file_path = resolve_contained_file(paths.photos_dir, frame.original_rel_path)
    filename = f"{camera.slug}-{Path(frame.original_rel_path).name}" if download else None
    return FileResponse(file_path, media_type="image/jpeg", filename=filename)


@router.post("/exports", response_model=RenderJobOut, status_code=202)
def create_export_job(
    payload: ExportCreateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(require_authenticated_user)],
) -> RenderJobOut:
    """Queue an on-demand ephemeral MP4 timelapse export from full-resolution originals."""
    camera = verify_camera_access(session.get(Camera, payload.query.camera_id), current_user)
    selected = select_camera_frames(session, camera, payload.query)
    if not selected:
        raise HTTPException(
            status_code=400,
            detail="No captured frames match the requested timelapse query",
        )

    prefs = get_system_preferences(session, settings)
    paths = get_effective_storage_paths(session, settings)
    export_inputs: list[ExportFrameInput] = []
    for frame in selected:
        orig_path = resolve_contained_file(paths.photos_dir, frame.original_rel_path)
        timestamp_label = format_frame_local_timestamp(
            ensure_utc(frame.captured_at_utc),
            camera.timezone,
        )
        export_inputs.append(
            ExportFrameInput(
                image_path=orig_path,
                timestamp_label=timestamp_label,
            )
        )

    ttl_hours = prefs.render_cache_ttl_hours
    now = datetime.now(UTC)
    job_id = str(uuid4())
    options = ExportOptions(
        fps=payload.fps,
        resolution_preset=payload.resolution_preset,
        watermark_timestamp=payload.watermark_timestamp,
    )

    job = RenderJob(
        id=job_id,
        camera_id=camera.id,
        created_by_user_id=current_user.id,
        mode=payload.query.mode,
        query_params_json=json.dumps(payload.query.model_dump(mode="json")),
        fps=payload.fps,
        resolution_preset=payload.resolution_preset,
        watermark_timestamp=payload.watermark_timestamp,
        status="queued",
        frame_count=len(export_inputs),
        processed_frames=0,
        output_rel_path=None,
        output_size_bytes=None,
        error_message=None,
        created_at=now,
        completed_at=None,
        expires_at=now + timedelta(hours=ttl_hours),
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    session_factory = cast(sessionmaker[Session], request.app.state.session_factory)
    background_tasks.add_task(
        run_render_job,
        session_factory,
        settings,
        job.id,
        export_inputs,
        options,
    )
    return render_job_to_out(job)


@router.get("/exports/{job_id}", response_model=RenderJobOut)
def get_export_job(
    job_id: str,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(require_authenticated_user)],
) -> RenderJobOut:
    """Return progress, status, and download URL for an ephemeral MP4 export job."""
    job = session.get(RenderJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found")

    verify_camera_access(session.get(Camera, job.camera_id), current_user)
    now_utc = datetime.now(UTC)
    if ensure_utc(job.expires_at) <= now_utc and job.status != "expired":
        reap_expired_render_jobs(session, settings, now_utc=now_utc)
        session.refresh(job)

    return render_job_to_out(job)


@router.get("/exports/{job_id}/download")
def download_export_job(
    job_id: str,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(require_authenticated_user)],
) -> FileResponse:
    """Stream a completed MP4 export or return 410 Gone once its TTL expires."""
    job = session.get(RenderJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found")

    camera = verify_camera_access(session.get(Camera, job.camera_id), current_user)
    now_utc = datetime.now(UTC)
    if ensure_utc(job.expires_at) <= now_utc or job.status == "expired":
        if job.status != "expired" or job.output_rel_path is not None:
            reap_expired_render_jobs(session, settings, now_utc=now_utc)
        raise HTTPException(
            status_code=410,
            detail="Export has expired from the ephemeral render cache",
        )

    if job.status != "completed":
        raise HTTPException(status_code=409, detail="Export is not yet completed")

    if not job.output_rel_path:
        raise HTTPException(
            status_code=410,
            detail="Export has expired from the ephemeral render cache",
        )

    paths = get_effective_storage_paths(session, settings)
    resolved_root = paths.renders_dir.resolve()
    candidate = (resolved_root / job.output_rel_path).resolve()
    if candidate == resolved_root or not candidate.is_relative_to(resolved_root):
        raise HTTPException(status_code=403, detail="Invalid artifact path")

    if not candidate.is_file():
        job.status = "expired"
        job.output_rel_path = None
        job.output_size_bytes = None
        session.commit()
        raise HTTPException(
            status_code=410,
            detail="Export has expired from the ephemeral render cache",
        )

    filename = f"{camera.slug}-{job.mode}-{job.fps}fps.mp4"
    return FileResponse(candidate, media_type="video/mp4", filename=filename)

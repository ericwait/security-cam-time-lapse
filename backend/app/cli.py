"""Administrative CLI including idempotent Phase 1 development dataset seeding."""

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.capture import store_dual_tier_capture
from app.config import Settings, load_settings
from app.db import create_session_factory, create_sqlite_engine
from app.models import Camera, CapturedFrame, SystemSetting, User
from app.security import MIN_PASSWORD_LENGTH, hash_password
from app.services import (
    SYSTEM_PREFERENCES_KEY,
    get_effective_storage_paths,
    get_system_preferences,
)
from app.synthetic import render_synthetic_frame

SEED_SETTING_KEY: Final[str] = "substrate.seeded"
SYNTHETIC_CAMERA_SLUG: Final[str] = "front-courtyard"
SYNTHETIC_CAMERA_NAME: Final[str] = "Front Courtyard (Synthetic)"
SYNTHETIC_CAMERA_TIMEZONE: Final[str] = "America/Chicago"
SEED_FRAME_WIDTH: Final[int] = 1280
SEED_FRAME_HEIGHT: Final[int] = 720


def _build_seed_timestamps_utc() -> list[datetime]:
    """Construct deterministic daily DST-spanning and single-day hourly UTC instants."""
    timestamps: set[datetime] = set()
    start_day = date(2026, 2, 25)
    for day_offset in range(30):
        current_day = start_day + timedelta(days=day_offset)
        y, m, d = current_day.year, current_day.month, current_day.day
        timestamps.add(datetime(y, m, d, 17, 0, tzinfo=UTC))
        timestamps.add(datetime(y, m, d, 18, 0, tzinfo=UTC))

    chicago_tz = ZoneInfo(SYNTHETIC_CAMERA_TIMEZONE)
    for local_hour in range(6, 18):
        local_dt = datetime(2026, 3, 26, local_hour, 0, tzinfo=chicago_tz)
        timestamps.add(local_dt.astimezone(UTC))

    return sorted(timestamps)


def _seed_admin_user(session: Session, bootstrap_password: str | None, now: datetime) -> None:
    if bootstrap_password is None or len(bootstrap_password) < MIN_PASSWORD_LENGTH:
        return
    existing = session.scalar(select(User).where(User.username == "admin"))
    if existing is not None:
        return
    session.add(
        User(
            username="admin",
            password_hash=hash_password(bootstrap_password),
            role="admin",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )


def _seed_synthetic_camera_and_frames(
    session: Session,
    settings: Settings,
    now: datetime,
) -> None:
    camera = session.scalar(select(Camera).where(Camera.slug == SYNTHETIC_CAMERA_SLUG))
    if camera is None:
        camera = Camera(
            name=SYNTHETIC_CAMERA_NAME,
            slug=SYNTHETIC_CAMERA_SLUG,
            source_type="synthetic",
            source_url=f"synthetic://{SYNTHETIC_CAMERA_SLUG}",
            auth_type="none",
            auth_username=None,
            encrypted_secret=None,
            timezone=SYNTHETIC_CAMERA_TIMEZONE,
            interval_seconds=900,
            is_enabled=True,
            created_at=now,
            updated_at=now,
        )
        session.add(camera)
        session.flush()

    existing_frames = int(
        session.scalar(
            select(func.count())
            .select_from(CapturedFrame)
            .where(CapturedFrame.camera_id == camera.id)
        )
        or 0
    )
    if existing_frames > 0:
        return

    paths = get_effective_storage_paths(session, settings)
    for directory in (paths.photos_dir, paths.previews_dir, paths.renders_dir):
        directory.mkdir(parents=True, exist_ok=True)

    seed_timestamps = _build_seed_timestamps_utc()
    for ts_utc in seed_timestamps:
        image_bytes = render_synthetic_frame(
            camera_name=camera.name,
            captured_at_utc=ts_utc,
            camera_timezone=camera.timezone,
            width=SEED_FRAME_WIDTH,
            height=SEED_FRAME_HEIGHT,
        )
        stored = store_dual_tier_capture(
            photos_dir=paths.photos_dir,
            previews_dir=paths.previews_dir,
            camera_slug=camera.slug,
            captured_at_utc=ts_utc,
            camera_timezone=camera.timezone,
            image_bytes=image_bytes,
        )
        session.add(
            CapturedFrame(
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
        )

    camera.last_capture_at = seed_timestamps[-1]
    camera.last_capture_status = "ok"
    camera.last_capture_error = None
    camera.updated_at = now


def seed_dev(settings: Settings | None = None) -> None:
    """Seed Phase 1 operational metadata, bootstrap admin, and synthetic dataset idempotently."""
    resolved_settings = settings if settings is not None else load_settings()
    resolved_settings.ensure_storage_dirs()

    engine = create_sqlite_engine(resolved_settings.db_path)
    session_factory = create_session_factory(engine)
    now = datetime.now(UTC)
    payload = json.dumps({"phase": 1})

    try:
        inspector = inspect(engine)
        has_users = inspector.has_table("users")
        has_cameras = inspector.has_table("cameras")
        has_frames = inspector.has_table("captured_frames")

        with session_factory() as session:
            existing = session.get(SystemSetting, SEED_SETTING_KEY)
            if existing is None:
                session.add(
                    SystemSetting(
                        key=SEED_SETTING_KEY,
                        value_json=payload,
                        updated_at=now,
                    )
                )
            else:
                existing.value_json = payload
                existing.updated_at = now

            if session.get(SystemSetting, SYSTEM_PREFERENCES_KEY) is None:
                prefs = get_system_preferences(session, resolved_settings)
                session.add(
                    SystemSetting(
                        key=SYSTEM_PREFERENCES_KEY,
                        value_json=json.dumps(prefs.model_dump(exclude={"migrated_files_count"})),
                        updated_at=now,
                    )
                )

            if has_users:
                _seed_admin_user(session, resolved_settings.bootstrap_admin_password, now)

            if has_cameras and has_frames:
                _seed_synthetic_camera_and_frames(session, resolved_settings, now)

            session.commit()
    finally:
        engine.dispose()

    print(f"Seeded development substrate -> {resolved_settings.db_path}")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Security Cam Time-Lapse administrative CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "seed-dev",
        help="Idempotently seed the local development database",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point returning a process exit code."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "seed-dev":
        seed_dev()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

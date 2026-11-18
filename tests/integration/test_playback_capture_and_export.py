"""End-to-end API integration tests for capture, Mode 1/2 DST queries, and MP4 TTL exports."""

import shutil
import time
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any, Final

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.deps import ensure_utc
from app.main import create_app
from app.models import Camera, CapturedFrame
from app.services import execute_camera_capture, reap_expired_render_jobs

DUMMY_ADMIN_PASS: Final[str] = "test-only-dummy-admin-0000"


def _poll_export_until_done(client: TestClient, job_id: str) -> dict[str, Any]:
    """Wait for an asynchronous FastAPI BackgroundTask render job to reach terminal state."""
    deadline = time.monotonic() + 20.0
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        resp = client.get(f"/api/exports/{job_id}")
        assert resp.status_code == 200
        payload: dict[str, Any] = resp.json()
        latest = payload
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Render job {job_id} timed out: {latest}")


def test_capture_lifecycle_dual_tier_media_serving_and_dst_selection_modes(
    migrated_settings: Settings,
) -> None:
    app = create_app(migrated_settings, enable_scheduler=False)
    with TestClient(app) as client:
        login_resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": DUMMY_ADMIN_PASS},
        )
        assert login_resp.status_code == 200

        cam_resp = client.post(
            "/api/admin/cameras",
            json={
                "name": "West Orchard",
                "slug": "west-orchard",
                "source_type": "synthetic",
                "source_url": "synthetic://west-orchard",
                "auth_type": "none",
                "timezone": "America/Chicago",
                "interval_seconds": 900,
                "is_enabled": True,
            },
        )
        assert cam_resp.status_code == 201
        camera_id: int = cam_resp.json()["id"]

        # Live capture-now writes untouched full-res JPEG + 1080p WebP preview on disk
        cap_resp = client.post(f"/api/admin/cameras/{camera_id}/capture-now")
        assert cap_resp.status_code == 201
        live_frame = cap_resp.json()
        live_frame_id: int = live_frame["id"]
        assert live_frame["width"] == 2560
        assert live_frame["height"] == 1440

        session_factory: sessionmaker[Session] = app.state.session_factory
        with session_factory() as session:
            orm_live_frame = session.get(CapturedFrame, live_frame_id)
            assert orm_live_frame is not None
            orig_disk_path = migrated_settings.photos_dir / orm_live_frame.original_rel_path
            prev_disk_path = migrated_settings.previews_dir / orm_live_frame.preview_rel_path
            assert orig_disk_path.is_file()
            assert prev_disk_path.is_file()
            expected_orig_bytes = orig_disk_path.read_bytes()

        # Verify WebP preview endpoint (<= 1920x1080)
        preview_resp = client.get(f"/api/frames/{live_frame_id}/preview")
        assert preview_resp.status_code == 200
        assert preview_resp.headers["content-type"].startswith("image/webp")
        with Image.open(BytesIO(preview_resp.content)) as preview_img:
            assert preview_img.format == "WEBP"
            assert preview_img.width <= 1920
            assert preview_img.height <= 1080
            assert (preview_img.width, preview_img.height) == (1920, 1080)

        # Verify untouched full-resolution JPEG original + attachment header on ?download=1
        orig_inline_resp = client.get(f"/api/frames/{live_frame_id}/original")
        assert orig_inline_resp.status_code == 200
        assert orig_inline_resp.headers["content-type"].startswith("image/jpeg")
        assert orig_inline_resp.content == expected_orig_bytes
        with Image.open(BytesIO(orig_inline_resp.content)) as orig_img:
            assert orig_img.format == "JPEG"
            assert (orig_img.width, orig_img.height) == (2560, 1440)

        orig_dl_resp = client.get(f"/api/frames/{live_frame_id}/original?download=1")
        assert orig_dl_resp.status_code == 200
        assert orig_dl_resp.headers["content-type"].startswith("image/jpeg")
        assert "attachment" in orig_dl_resp.headers.get("content-disposition", "").lower()
        assert orig_dl_resp.content == expected_orig_bytes

        # Seed deterministic captures spanning 2026-03-08 America/Chicago spring-forward DST
        dst_instants = [
            datetime(2026, 3, 7, 18, 0, 0, tzinfo=UTC),  # 12:00 CST (-06:00)
            datetime(2026, 3, 8, 17, 0, 0, tzinfo=UTC),  # 12:00 CDT (-05:00) / 11:00 std
            datetime(2026, 3, 8, 18, 0, 0, tzinfo=UTC),  # 13:00 CDT (-05:00) / 12:00 std
            datetime(2026, 3, 9, 17, 0, 0, tzinfo=UTC),  # 12:00 CDT (-05:00) / 11:00 std
            datetime(2026, 3, 9, 18, 0, 0, tzinfo=UTC),  # 13:00 CDT (-05:00) / 12:00 std
        ]
        with session_factory() as session:
            camera_row = session.get(Camera, camera_id)
            assert camera_row is not None
            for instant in dst_instants:
                execute_camera_capture(
                    session,
                    migrated_settings,
                    camera_row,
                    captured_at_utc=instant,
                )

        # Mode 1: Continuous query over 2026-03-08 window
        cont_resp = client.post(
            "/api/timelapse/query",
            json={
                "camera_id": camera_id,
                "mode": "continuous",
                "start_timestamp": "2026-03-08T16:30:00Z",
                "end_timestamp": "2026-03-08T18:30:00Z",
            },
        )
        assert cont_resp.status_code == 200
        cont_data = cont_resp.json()
        assert cont_data["mode"] == "continuous"
        assert cont_data["frame_count"] == 2
        cont_utcs = [
            ensure_utc(datetime.fromisoformat(f["captured_at_utc"])) for f in cont_data["frames"]
        ]
        assert cont_utcs == [
            datetime(2026, 3, 8, 17, 0, 0, tzinfo=UTC),
            datetime(2026, 3, 8, 18, 0, 0, tzinfo=UTC),
        ]

        # Mode 2 (wall_clock): 12:00 local wall-clock shifts from 18:00Z (CST) to 17:00Z (CDT)
        wall_resp = client.post(
            "/api/timelapse/query",
            json={
                "camera_id": camera_id,
                "mode": "daily_slice",
                "start_date": "2026-03-07",
                "end_date": "2026-03-09",
                "target_time": "12:00:00",
                "tolerance_minutes": 15,
                "dst_mode": "wall_clock",
            },
        )
        assert wall_resp.status_code == 200
        wall_data = wall_resp.json()
        assert wall_data["frame_count"] == 3
        wall_utcs = [
            ensure_utc(datetime.fromisoformat(f["captured_at_utc"])) for f in wall_data["frames"]
        ]
        assert wall_utcs == [
            datetime(2026, 3, 7, 18, 0, 0, tzinfo=UTC),
            datetime(2026, 3, 8, 17, 0, 0, tzinfo=UTC),
            datetime(2026, 3, 9, 17, 0, 0, tzinfo=UTC),
        ]

        # Mode 2 (standard_offset): fixed -06:00 standard offset holds solar 18:00Z steady
        std_resp = client.post(
            "/api/timelapse/query",
            json={
                "camera_id": camera_id,
                "mode": "daily_slice",
                "start_date": "2026-03-07",
                "end_date": "2026-03-09",
                "target_time": "12:00:00",
                "tolerance_minutes": 15,
                "dst_mode": "standard_offset",
            },
        )
        assert std_resp.status_code == 200
        std_data = std_resp.json()
        assert std_data["frame_count"] == 3
        std_utcs = [
            ensure_utc(datetime.fromisoformat(f["captured_at_utc"])) for f in std_data["frames"]
        ]
        assert std_utcs == [
            datetime(2026, 3, 7, 18, 0, 0, tzinfo=UTC),
            datetime(2026, 3, 8, 18, 0, 0, tzinfo=UTC),
            datetime(2026, 3, 9, 18, 0, 0, tzinfo=UTC),
        ]


def test_ffmpeg_mp4_export_and_deterministic_ttl_cache_reaping(
    migrated_settings: Settings,
) -> None:
    assert shutil.which("ffmpeg") is not None, "ffmpeg is required in integration runtime"

    app = create_app(migrated_settings, enable_scheduler=False)
    with TestClient(app) as client:
        login_resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": DUMMY_ADMIN_PASS},
        )
        assert login_resp.status_code == 200

        cam_resp = client.post(
            "/api/admin/cameras",
            json={
                "name": "South Courtyard",
                "slug": "south-courtyard",
                "source_type": "synthetic",
                "source_url": "synthetic://south-courtyard",
                "timezone": "America/Chicago",
                "interval_seconds": 900,
                "is_enabled": True,
            },
        )
        assert cam_resp.status_code == 201
        camera_id: int = cam_resp.json()["id"]

        session_factory: sessionmaker[Session] = app.state.session_factory
        with session_factory() as session:
            camera_row = session.get(Camera, camera_id)
            assert camera_row is not None
            for day in (7, 8, 9):
                execute_camera_capture(
                    session,
                    migrated_settings,
                    camera_row,
                    captured_at_utc=datetime(2026, 3, day, 18, 0, 0, tzinfo=UTC),
                )

        export_resp = client.post(
            "/api/exports",
            json={
                "query": {
                    "camera_id": camera_id,
                    "mode": "daily_slice",
                    "start_date": "2026-03-07",
                    "end_date": "2026-03-09",
                    "target_time": "12:00:00",
                    "tolerance_minutes": 15,
                    "dst_mode": "standard_offset",
                },
                "fps": 10,
                "resolution_preset": "1080p",
                "watermark_timestamp": True,
            },
        )
        assert export_resp.status_code == 202
        job_id: str = export_resp.json()["id"]

        job_status = _poll_export_until_done(client, job_id)
        assert job_status["status"] == "completed"
        assert job_status["frame_count"] == 3
        assert job_status["processed_frames"] == 3
        assert job_status["output_size_bytes"] is not None
        assert job_status["output_size_bytes"] > 0

        cached_mp4_path = migrated_settings.renders_dir / f"{job_id}.mp4"
        assert cached_mp4_path.is_file()

        dl_resp = client.get(f"/api/exports/{job_id}/download")
        assert dl_resp.status_code == 200
        assert dl_resp.headers["content-type"].startswith("video/mp4")
        assert len(dl_resp.content) == cached_mp4_path.stat().st_size
        assert len(dl_resp.content) > 32
        assert dl_resp.content[4:8] == b"ftyp"

        # Before TTL expiration (now + 1h): reaper retains cached MP4
        with session_factory() as session:
            reaped_early = reap_expired_render_jobs(
                session,
                migrated_settings,
                now_utc=datetime.now(UTC) + timedelta(hours=1),
            )
        assert reaped_early == 0
        assert cached_mp4_path.is_file()
        assert client.get(f"/api/exports/{job_id}/download").status_code == 200

        # Deterministic TTL reaping past 24h default TTL (now + 25h, zero wall-clock sleep)
        with session_factory() as session:
            reaped_expired = reap_expired_render_jobs(
                session,
                migrated_settings,
                now_utc=datetime.now(UTC) + timedelta(hours=25),
            )
        assert reaped_expired == 1
        assert not cached_mp4_path.exists()
        assert list(migrated_settings.renders_dir.glob("*.mp4")) == []

        expired_status = client.get(f"/api/exports/{job_id}")
        assert expired_status.status_code == 200
        assert expired_status.json()["status"] == "expired"

        gone_resp = client.get(f"/api/exports/{job_id}/download")
        assert gone_resp.status_code == 410

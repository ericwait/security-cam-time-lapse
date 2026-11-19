"""Integration tests for TimelapseScheduler, Admin settings/storage, and lifecycle edge cases."""

import base64
import os
import shutil
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.deps import SESSION_COOKIE_NAME, ensure_utc
from app.main import create_app
from app.models import Camera, CapturedFrame, RenderJob
from app.scheduler import TimelapseScheduler
from app.security import decrypt_secret
from app.services import execute_camera_capture

DUMMY_ADMIN_PASS: Final[str] = "test-only-dummy-admin-0000"
DUMMY_VIEWER_PASS: Final[str] = "test-only-dummy-viewer-0000"
DUMMY_CAM_SECRET: Final[str] = "cam-dummy-credential-alpha-99"
DUMMY_CAM_ROTATED: Final[str] = "cam-dummy-credential-beta-88"
DUMMY_PASS_ROTATED: Final[str] = "test-only-dummy-rotated-1111"
DUMMY_PASS_WRONG: Final[str] = "test-only-dummy-wrong-9999"


class _ActorClient:
    """Cookie-isolated actor wrapper sharing a single TestClient lifespan."""

    def __init__(self, client: TestClient, session_token: str | None = None) -> None:
        self._client = client
        self.session_token = session_token

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self._client.cookies.clear()
        if self.session_token is not None:
            self._client.cookies.set(SESSION_COOKIE_NAME, self.session_token)
        response: httpx.Response = self._client.request(method, url, **kwargs)
        issued_token = response.cookies.get(SESSION_COOKIE_NAME)
        if issued_token:
            self.session_token = issued_token
        self._client.cookies.clear()
        return response

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)


def _build_sample_jpeg_bytes() -> bytes:
    """Create a deterministic valid RGB JPEG image for HTTP camera probes."""
    buf = BytesIO()
    Image.new("RGB", (640, 360), color=(32, 96, 160)).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_scheduler_tick_captures_fault_isolation_interval_gating_and_disabled_skip(
    migrated_settings: Settings,
) -> None:
    def _broken_upstream_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="camera upstream unavailable")

    broken_transport = httpx.MockTransport(_broken_upstream_handler)
    app = create_app(
        migrated_settings,
        enable_scheduler=False,
        http_transport=broken_transport,
    )

    with TestClient(app) as client:
        login_resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": DUMMY_ADMIN_PASS},
        )
        assert login_resp.status_code == 200

        # Register broken HTTP camera FIRST so Camera.id.asc() proves fault isolation
        broken_resp = client.post(
            "/api/admin/cameras",
            json={
                "name": "Broken Courtyard HTTP",
                "slug": "broken-courtyard-http",
                "source_type": "http",
                "source_url": "http://broken-cam.local/snapshot.jpg",
                "auth_type": "none",
                "timezone": "America/Chicago",
                "interval_seconds": 600,
                "is_enabled": True,
            },
        )
        assert broken_resp.status_code == 201
        broken_id: int = broken_resp.json()["id"]

        healthy_resp = client.post(
            "/api/admin/cameras",
            json={
                "name": "Healthy Roofline",
                "slug": "healthy-roofline",
                "source_type": "synthetic",
                "source_url": "synthetic://healthy-roofline",
                "auth_type": "none",
                "timezone": "America/Chicago",
                "interval_seconds": 600,
                "is_enabled": True,
            },
        )
        assert healthy_resp.status_code == 201
        healthy_id: int = healthy_resp.json()["id"]

        disabled_resp = client.post(
            "/api/admin/cameras",
            json={
                "name": "Disabled Shed",
                "slug": "disabled-shed",
                "source_type": "synthetic",
                "source_url": "synthetic://disabled-shed",
                "auth_type": "none",
                "timezone": "America/Chicago",
                "interval_seconds": 600,
                "is_enabled": False,
            },
        )
        assert disabled_resp.status_code == 201
        disabled_id: int = disabled_resp.json()["id"]

        scheduler: TimelapseScheduler = app.state.scheduler
        t0 = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)

        # 1. First tick: broken camera records error without aborting healthy camera; disabled=0
        captured_first = scheduler.tick_captures(now_utc=t0)
        assert captured_first == 1

        cams_after_first = {c["id"]: c for c in client.get("/api/admin/cameras").json()}
        assert cams_after_first[broken_id]["frame_count"] == 0
        assert cams_after_first[broken_id]["last_capture_status"] == "error"
        assert cams_after_first[broken_id]["last_capture_error"] is not None
        assert "503" in cams_after_first[broken_id]["last_capture_error"]
        assert cams_after_first[broken_id]["last_capture_at"] is None

        assert cams_after_first[healthy_id]["frame_count"] == 1
        assert cams_after_first[healthy_id]["last_capture_status"] == "ok"
        assert cams_after_first[healthy_id]["last_capture_error"] is None
        first_iso: str = cams_after_first[healthy_id]["last_capture_at"]
        assert ensure_utc(datetime.fromisoformat(first_iso)) == t0

        assert cams_after_first[disabled_id]["frame_count"] == 0
        assert cams_after_first[disabled_id]["last_capture_status"] is None
        assert cams_after_first[disabled_id]["last_capture_at"] is None

        # 2. Immediate tick before interval_seconds=600 elapses: healthy camera NOT recaptured
        t_early = t0 + timedelta(seconds=599)
        captured_early = scheduler.tick_captures(now_utc=t_early)
        assert captured_early == 0

        cams_after_early = {c["id"]: c for c in client.get("/api/admin/cameras").json()}
        assert cams_after_early[healthy_id]["frame_count"] == 1
        early_iso: str = cams_after_early[healthy_id]["last_capture_at"]
        assert ensure_utc(datetime.fromisoformat(early_iso)) == t0
        assert cams_after_early[disabled_id]["frame_count"] == 0

        # 3. Advance past interval_seconds=600: healthy camera captures a second frame
        t_due = t0 + timedelta(seconds=605)
        captured_due = scheduler.tick_captures(now_utc=t_due)
        assert captured_due == 1

        cams_after_due = {c["id"]: c for c in client.get("/api/admin/cameras").json()}
        assert cams_after_due[healthy_id]["frame_count"] == 2
        assert cams_after_due[healthy_id]["last_capture_status"] == "ok"
        due_iso: str = cams_after_due[healthy_id]["last_capture_at"]
        assert ensure_utc(datetime.fromisoformat(due_iso)) == t_due
        assert cams_after_due[broken_id]["frame_count"] == 0
        assert cams_after_due[broken_id]["last_capture_status"] == "error"
        assert cams_after_due[disabled_id]["frame_count"] == 0


def test_scheduler_lifespan_starts_and_shuts_down_cleanly(migrated_settings: Settings) -> None:
    app = create_app(migrated_settings, enable_scheduler=True)
    scheduler: TimelapseScheduler = app.state.scheduler
    assert scheduler.running is False

    with TestClient(app) as client:
        assert scheduler.running is True
        scheduler.start()
        assert scheduler.running is True

        health_resp = client.get("/api/health")
        assert health_resp.status_code == 200
        assert health_resp.json()["status"] == "ok"

    assert scheduler.running is False
    scheduler.shutdown()
    assert scheduler.running is False


def test_scheduler_tick_reap_renders_removes_expired_mp4(
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
                "name": "Reaper Patio Cam",
                "slug": "reaper-patio-cam",
                "source_type": "synthetic",
                "source_url": "synthetic://reaper-patio-cam",
                "timezone": "America/Chicago",
                "interval_seconds": 900,
                "is_enabled": True,
            },
        )
        assert cam_resp.status_code == 201
        camera_id: int = cam_resp.json()["id"]

        session_factory: sessionmaker[Session] = app.state.session_factory
        scheduler: TimelapseScheduler = app.state.scheduler
        t_frame_1 = datetime(2026, 5, 2, 14, 0, 0, tzinfo=UTC)
        t_frame_2 = datetime(2026, 5, 2, 14, 15, 0, tzinfo=UTC)
        with session_factory() as session:
            camera_row = session.get(Camera, camera_id)
            assert camera_row is not None
            for instant in (t_frame_1, t_frame_2):
                execute_camera_capture(
                    session,
                    migrated_settings,
                    camera_row,
                    captured_at_utc=instant,
                )

        export_resp = client.post(
            "/api/exports",
            json={
                "query": {
                    "camera_id": camera_id,
                    "mode": "continuous",
                    "start_timestamp": "2026-05-02T13:55:00Z",
                    "end_timestamp": "2026-05-02T14:20:00Z",
                },
                "fps": 10,
                "resolution_preset": "1080p",
                "watermark_timestamp": False,
            },
        )
        assert export_resp.status_code == 202
        job_id: str = export_resp.json()["id"]

        # BackgroundTasks completes synchronously inside TestClient before post() returns
        status_resp = client.get(f"/api/exports/{job_id}")
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "completed"

        mp4_path = migrated_settings.renders_dir / f"{job_id}.mp4"
        assert mp4_path.is_file()

        reap_now = datetime(2026, 5, 2, 16, 0, 0, tzinfo=UTC)
        with session_factory() as session:
            job_row = session.get(RenderJob, job_id)
            assert job_row is not None
            job_row.expires_at = reap_now + timedelta(hours=4)
            session.commit()

        assert scheduler.tick_reap_renders(now_utc=reap_now) == 0
        assert mp4_path.is_file()

        # Move expires_at into the past in SQLite and run tick_reap_renders deterministically
        with session_factory() as session:
            job_row = session.get(RenderJob, job_id)
            assert job_row is not None
            job_row.expires_at = reap_now - timedelta(minutes=30)
            session.commit()

        reaped_count = scheduler.tick_reap_renders(now_utc=reap_now)
        assert reaped_count == 1
        assert not mp4_path.exists()

        with session_factory() as session:
            expired_row = session.get(RenderJob, job_id)
            assert expired_row is not None
            assert expired_row.status == "expired"
            assert expired_row.output_rel_path is None
            assert expired_row.output_size_bytes is None

        after_resp = client.get(f"/api/exports/{job_id}")
        assert after_resp.status_code == 200
        assert after_resp.json()["status"] == "expired"
        assert after_resp.json()["download_url"] is None
        assert client.get(f"/api/exports/{job_id}/download").status_code == 410


def test_admin_settings_persistence_validation_and_storage_telemetry(
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

        defaults_resp = client.get("/api/admin/settings")
        assert defaults_resp.status_code == 200
        expected_defaults = {
            "default_timezone": "America/Chicago",
            "render_cache_ttl_hours": 24,
            "default_tolerance_minutes": 15,
            "photos_dir": str(migrated_settings.photos_dir),
            "previews_dir": str(migrated_settings.previews_dir),
            "renders_dir": str(migrated_settings.renders_dir),
            "migrated_files_count": 0,
        }
        assert defaults_resp.json() == expected_defaults

        updated_payload = {
            "default_timezone": "America/New_York",
            "render_cache_ttl_hours": 12,
            "default_tolerance_minutes": 20,
        }
        expected_updated = {
            **expected_defaults,
            **updated_payload,
        }
        put_resp = client.put("/api/admin/settings", json=updated_payload)
        assert put_resp.status_code == 200
        assert put_resp.json() == expected_updated
        assert client.get("/api/admin/settings").json() == expected_updated

        bad_tz_resp = client.put(
            "/api/admin/settings",
            json={"default_timezone": "Mars/Olympus"},
        )
        assert bad_tz_resp.status_code == 422

        offset_tz_resp = client.put(
            "/api/admin/settings",
            json={"default_timezone": "UTC-5"},
        )
        assert offset_tz_resp.status_code == 422
        assert client.get("/api/admin/settings").json() == expected_updated

        baseline_storage = client.get("/api/admin/storage")
        assert baseline_storage.status_code == 200
        base_stats = baseline_storage.json()
        assert base_stats["disk_total_bytes"] > 0
        assert base_stats["photos_bytes"] == 0
        assert base_stats["previews_bytes"] == 0
        assert base_stats["renders_bytes"] == 0
        assert base_stats["total_frames"] == 0
        assert base_stats["active_renders"] == 0
        assert base_stats["photos_dir"] == str(migrated_settings.photos_dir)
        assert base_stats["previews_dir"] == str(migrated_settings.previews_dir)
        assert base_stats["renders_dir"] == str(migrated_settings.renders_dir)

        cam_resp = client.post(
            "/api/admin/cameras",
            json={
                "name": "Telemetry Harbor",
                "slug": "telemetry-harbor",
                "source_type": "synthetic",
                "source_url": "synthetic://telemetry-harbor",
                "timezone": "America/New_York",
                "interval_seconds": 600,
                "is_enabled": True,
            },
        )
        assert cam_resp.status_code == 201
        camera_id: int = cam_resp.json()["id"]

        cap_resp = client.post(f"/api/admin/cameras/{camera_id}/capture-now")
        assert cap_resp.status_code == 201

        export_resp = client.post(
            "/api/exports",
            json={
                "query": {
                    "camera_id": camera_id,
                    "mode": "continuous",
                    "start_timestamp": "2000-01-01T00:00:00Z",
                    "end_timestamp": "2099-12-31T23:59:59Z",
                },
                "fps": 10,
                "resolution_preset": "1080p",
                "watermark_timestamp": False,
            },
        )
        assert export_resp.status_code == 202
        job_id: str = export_resp.json()["id"]
        assert client.get(f"/api/exports/{job_id}").json()["status"] == "completed"

        storage_resp = client.get("/api/admin/storage")
        assert storage_resp.status_code == 200
        stats = storage_resp.json()
        assert stats["disk_total_bytes"] > 0
        assert stats["disk_used_bytes"] > 0
        assert stats["disk_free_bytes"] >= 0
        assert stats["photos_bytes"] > 0
        assert stats["previews_bytes"] > 0
        assert stats["renders_bytes"] > 0
        assert stats["total_frames"] >= 1
        assert stats["active_renders"] >= 1


def test_camera_secret_lifecycle_connection_probe_and_cascade_delete(
    migrated_settings: Settings,
) -> None:
    sample_jpeg = _build_sample_jpeg_bytes()
    observed_auth_headers: list[str | None] = []

    def _camera_probe_handler(request: httpx.Request) -> httpx.Response:
        observed_auth_headers.append(request.headers.get("authorization"))
        return httpx.Response(
            200,
            content=sample_jpeg,
            headers={"content-type": "image/jpeg"},
        )

    probe_transport = httpx.MockTransport(_camera_probe_handler)
    app = create_app(
        migrated_settings,
        enable_scheduler=False,
        http_transport=probe_transport,
    )

    with TestClient(app) as client:
        login_resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": DUMMY_ADMIN_PASS},
        )
        assert login_resp.status_code == 200

        # 1. In-memory test-connection returns inline WebP preview_data_url without disk writes
        probe_resp = client.post(
            "/api/admin/cameras/test-connection",
            json={
                "name": "East Gate Candidate",
                "source_type": "http",
                "source_url": "http://east-gate.local/snap.jpg",
                "auth_type": "basic",
                "auth_username": "gate-op",
                "secret": DUMMY_CAM_SECRET,
                "timezone": "America/New_York",
            },
        )
        assert probe_resp.status_code == 200
        probe_data = probe_resp.json()
        assert probe_data["ok"] is True
        assert probe_data["width"] == 640
        assert probe_data["height"] == 360
        prefix = "data:image/webp;base64,"
        assert probe_data["preview_data_url"].startswith(prefix)
        decoded_preview = base64.b64decode(probe_data["preview_data_url"].removeprefix(prefix))
        with Image.open(BytesIO(decoded_preview)) as preview_img:
            assert preview_img.format == "WEBP"
            assert (preview_img.width, preview_img.height) == (640, 360)
        assert list(migrated_settings.photos_dir.iterdir()) == []
        assert list(migrated_settings.previews_dir.iterdir()) == []

        # 2. Create persisted HTTP camera with initial encrypted secret
        create_resp = client.post(
            "/api/admin/cameras",
            json={
                "name": "East Gate",
                "slug": "east-gate",
                "source_type": "http",
                "source_url": "http://east-gate.local/snap.jpg",
                "auth_type": "basic",
                "auth_username": "gate-op",
                "secret": DUMMY_CAM_SECRET,
                "timezone": "America/New_York",
                "interval_seconds": 600,
                "is_enabled": True,
            },
        )
        assert create_resp.status_code == 201
        created_cam = create_resp.json()
        camera_id: int = created_cam["id"]
        cam_slug: str = created_cam["slug"]
        assert created_cam["has_credentials"] is True
        assert created_cam["auth_username"] == "gate-op"

        session_factory: sessionmaker[Session] = app.state.session_factory

        # 3. Omitting secret on PATCH preserves existing encrypted secret
        preserve_resp = client.patch(
            f"/api/admin/cameras/{camera_id}",
            json={"interval_seconds": 300},
        )
        assert preserve_resp.status_code == 200
        assert preserve_resp.json()["has_credentials"] is True
        assert preserve_resp.json()["interval_seconds"] == 300

        with session_factory() as session:
            cam_row = session.get(Camera, camera_id)
            assert cam_row is not None
            assert cam_row.encrypted_secret is not None
            initial_ciphertext = cam_row.encrypted_secret
            decrypted_initial = decrypt_secret(migrated_settings.secret_key, initial_ciphertext)
            assert decrypted_initial == DUMMY_CAM_SECRET

        observed_auth_headers.clear()
        assert client.post(f"/api/admin/cameras/{camera_id}/test-capture").status_code == 200
        initial_creds = f"gate-op:{DUMMY_CAM_SECRET}".encode()
        expected_initial_b64 = base64.b64encode(initial_creds).decode("ascii")
        assert observed_auth_headers == [f"Basic {expected_initial_b64}"]

        # 4. Providing new secret on PATCH rotates encrypted secret in SQLite
        rotate_resp = client.patch(
            f"/api/admin/cameras/{camera_id}",
            json={"secret": DUMMY_CAM_ROTATED},
        )
        assert rotate_resp.status_code == 200
        assert rotate_resp.json()["has_credentials"] is True
        assert DUMMY_CAM_ROTATED not in rotate_resp.text

        with session_factory() as session:
            cam_row = session.get(Camera, camera_id)
            assert cam_row is not None
            assert cam_row.encrypted_secret is not None
            rotated_ciphertext = cam_row.encrypted_secret
            assert rotated_ciphertext != initial_ciphertext
            decrypted_rotated = decrypt_secret(migrated_settings.secret_key, rotated_ciphertext)
            assert decrypted_rotated == DUMMY_CAM_ROTATED

        observed_auth_headers.clear()
        assert client.post(f"/api/admin/cameras/{camera_id}/test-capture").status_code == 200
        rotated_creds = f"gate-op:{DUMMY_CAM_ROTATED}".encode()
        expected_rotated_b64 = base64.b64encode(rotated_creds).decode("ascii")
        assert observed_auth_headers == [f"Basic {expected_rotated_b64}"]

        # 5. Switching auth_type to "none" clears credentials and username
        clear_auth_resp = client.patch(
            f"/api/admin/cameras/{camera_id}",
            json={"auth_type": "none"},
        )
        assert clear_auth_resp.status_code == 200
        assert clear_auth_resp.json()["auth_type"] == "none"
        assert clear_auth_resp.json()["has_credentials"] is False
        assert clear_auth_resp.json()["auth_username"] is None

        with session_factory() as session:
            cam_row = session.get(Camera, camera_id)
            assert cam_row is not None
            assert cam_row.encrypted_secret is None
            assert cam_row.auth_username is None

        observed_auth_headers.clear()
        cap_resp = client.post(f"/api/admin/cameras/{camera_id}/capture-now")
        assert cap_resp.status_code == 201
        assert observed_auth_headers == [None]

        cam_photos_dir = migrated_settings.photos_dir / cam_slug
        cam_previews_dir = migrated_settings.previews_dir / cam_slug
        assert cam_photos_dir.is_dir()
        assert cam_previews_dir.is_dir()

        # 6. DELETE camera removes DB rows and both photo/preview directories on disk
        del_resp = client.delete(f"/api/admin/cameras/{camera_id}")
        assert del_resp.status_code == 204
        assert client.get("/api/admin/cameras").json() == []
        assert client.delete(f"/api/admin/cameras/{camera_id}").status_code == 404
        assert not cam_photos_dir.exists()
        assert not cam_previews_dir.exists()

        with session_factory() as session:
            count_stmt = select(func.count()).where(CapturedFrame.camera_id == camera_id)
            assert session.scalar(count_stmt) == 0


def test_multi_admin_handoff_and_password_rotation_revoking_parallel_sessions(
    migrated_settings: Settings,
) -> None:
    app = create_app(migrated_settings, enable_scheduler=False)
    with TestClient(app) as raw_client:
        primary_admin = _ActorClient(raw_client)
        co_session_main = _ActorClient(raw_client)
        co_session_parallel = _ActorClient(raw_client)
        fresh_login_actor = _ActorClient(raw_client)

        primary_login = primary_admin.post(
            "/api/auth/login",
            json={"username": "admin", "password": DUMMY_ADMIN_PASS},
        )
        assert primary_login.status_code == 200
        primary_admin_id: int = primary_login.json()["id"]

        create_co_resp = primary_admin.post(
            "/api/admin/users",
            json={
                "username": "co-admin",
                "password": DUMMY_VIEWER_PASS,
                "role": "admin",
            },
        )
        assert create_co_resp.status_code == 201
        co_admin_id: int = create_co_resp.json()["id"]

        # Open two parallel authenticated sessions for co-admin
        main_login = co_session_main.post(
            "/api/auth/login",
            json={"username": "co-admin", "password": DUMMY_VIEWER_PASS},
        )
        assert main_login.status_code == 200
        parallel_login = co_session_parallel.post(
            "/api/auth/login",
            json={"username": "co-admin", "password": DUMMY_VIEWER_PASS},
        )
        assert parallel_login.status_code == 200
        assert co_session_main.get("/api/admin/users").status_code == 200
        assert co_session_parallel.get("/api/admin/users").status_code == 200

        # 1. Wrong current_password rejected with 400 and preserves parallel session
        wrong_pw_resp = co_session_main.post(
            "/api/auth/password",
            json={
                "current_password": DUMMY_PASS_WRONG,
                "new_password": DUMMY_PASS_ROTATED,
            },
        )
        assert wrong_pw_resp.status_code == 400
        assert co_session_parallel.get("/api/admin/users").status_code == 200

        # 2. Valid password rotation keeps caller session active and revokes parallel session
        valid_pw_resp = co_session_main.post(
            "/api/auth/password",
            json={
                "current_password": DUMMY_VIEWER_PASS,
                "new_password": DUMMY_PASS_ROTATED,
            },
        )
        assert valid_pw_resp.status_code == 200
        assert valid_pw_resp.json() == {"status": "ok"}

        assert co_session_main.get("/api/admin/users").status_code == 200
        assert co_session_parallel.get("/api/admin/users").status_code == 401
        assert co_session_parallel.get("/api/cameras").status_code == 401

        old_login_resp = fresh_login_actor.post(
            "/api/auth/login",
            json={"username": "co-admin", "password": DUMMY_VIEWER_PASS},
        )
        assert old_login_resp.status_code == 401

        new_login_resp = fresh_login_actor.post(
            "/api/auth/login",
            json={"username": "co-admin", "password": DUMMY_PASS_ROTATED},
        )
        assert new_login_resp.status_code == 200

        # 3. Primary admin CAN demote second admin while at least one active admin remains
        demote_resp = primary_admin.patch(
            f"/api/admin/users/{co_admin_id}",
            json={"role": "viewer"},
        )
        assert demote_resp.status_code == 200
        assert demote_resp.json()["role"] == "viewer"
        assert co_session_main.get("/api/admin/users").status_code == 403
        assert co_session_main.get("/api/cameras").status_code == 200

        # Re-promote co-admin to admin, then verify primary admin CAN delete second active admin
        repromote_resp = primary_admin.patch(
            f"/api/admin/users/{co_admin_id}",
            json={"role": "admin"},
        )
        assert repromote_resp.status_code == 200
        assert repromote_resp.json()["role"] == "admin"

        delete_co_resp = primary_admin.delete(f"/api/admin/users/{co_admin_id}")
        assert delete_co_resp.status_code == 204
        assert co_session_main.get("/api/cameras").status_code == 401
        assert fresh_login_actor.get("/api/cameras").status_code == 401

        remaining_users = primary_admin.get("/api/admin/users").json()
        assert [u["id"] for u in remaining_users] == [primary_admin_id]


def test_configurable_storage_paths_live_file_migration_and_playback_continuity(
    migrated_settings: Settings,
    tmp_path: Path,
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
                "name": "Migration Courtyard",
                "slug": "migration-courtyard",
                "source_type": "synthetic",
                "source_url": "synthetic://migration-courtyard",
                "timezone": "America/Chicago",
                "interval_seconds": 600,
                "is_enabled": True,
            },
        )
        assert cam_resp.status_code == 201
        camera_id: int = cam_resp.json()["id"]

        session_factory: sessionmaker[Session] = app.state.session_factory
        t1 = datetime(2026, 6, 1, 15, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 6, 1, 15, 10, 0, tzinfo=UTC)
        with session_factory() as session:
            cam_row = session.get(Camera, camera_id)
            assert cam_row is not None
            f1 = execute_camera_capture(session, migrated_settings, cam_row, captured_at_utc=t1)
            f2 = execute_camera_capture(session, migrated_settings, cam_row, captured_at_utc=t2)
            frame_1_id = f1.id
            frame_1_orig_rel = f1.original_rel_path
            frame_1_prev_rel = f1.preview_rel_path
            frame_2_orig_rel = f2.original_rel_path
            frame_2_prev_rel = f2.preview_rel_path

        export_resp = client.post(
            "/api/exports",
            json={
                "query": {
                    "camera_id": camera_id,
                    "mode": "continuous",
                    "start_timestamp": "2026-06-01T14:55:00Z",
                    "end_timestamp": "2026-06-01T15:20:00Z",
                },
                "fps": 10,
                "resolution_preset": "1080p",
                "watermark_timestamp": False,
            },
        )
        assert export_resp.status_code == 202
        job_id: str = export_resp.json()["id"]
        assert client.get(f"/api/exports/{job_id}").json()["status"] == "completed"

        assert (migrated_settings.photos_dir / frame_1_orig_rel).is_file()
        assert (migrated_settings.previews_dir / frame_1_prev_rel).is_file()
        assert (migrated_settings.renders_dir / f"{job_id}.mp4").is_file()

        custom_root = tmp_path / "custom_storage"
        custom_photos = custom_root / "photos_archive"
        custom_previews = custom_root / "previews_fast"
        custom_renders = custom_root / "renders_ephemeral"

        relocate_resp = client.put(
            "/api/admin/settings",
            json={
                "photos_dir": str(custom_photos),
                "previews_dir": str(custom_previews),
                "renders_dir": str(custom_renders),
            },
        )
        assert relocate_resp.status_code == 200
        relocated_data = relocate_resp.json()
        # 2 originals + 2 previews + 1 rendered MP4 = 5 files migrated
        assert relocated_data["migrated_files_count"] == 5
        assert relocated_data["photos_dir"] == str(custom_photos)
        assert relocated_data["previews_dir"] == str(custom_previews)
        assert relocated_data["renders_dir"] == str(custom_renders)

        # Old directories remain intact as mount roots, with all child files/dirs migrated out
        assert migrated_settings.photos_dir.is_dir()
        assert migrated_settings.previews_dir.is_dir()
        assert migrated_settings.renders_dir.is_dir()
        assert list(migrated_settings.photos_dir.rglob("*")) == []
        assert list(migrated_settings.previews_dir.rglob("*")) == []
        assert list(migrated_settings.renders_dir.rglob("*")) == []

        assert (custom_photos / frame_1_orig_rel).is_file()
        assert (custom_photos / frame_2_orig_rel).is_file()
        assert (custom_previews / frame_1_prev_rel).is_file()
        assert (custom_previews / frame_2_prev_rel).is_file()
        assert (custom_renders / f"{job_id}.mp4").is_file()

        # Playback & export download serve seamlessly from relocated paths
        prev_resp = client.get(f"/api/frames/{frame_1_id}/preview")
        assert prev_resp.status_code == 200
        assert prev_resp.headers["content-type"] == "image/webp"

        orig_resp = client.get(f"/api/frames/{frame_1_id}/original")
        assert orig_resp.status_code == 200
        assert orig_resp.headers["content-type"] == "image/jpeg"

        mp4_resp = client.get(f"/api/exports/{job_id}/download")
        assert mp4_resp.status_code == 200
        assert mp4_resp.headers["content-type"] == "video/mp4"

        # Subsequent capture-now writes into relocated directories (not old default directories)
        new_cap_resp = client.post(f"/api/admin/cameras/{camera_id}/capture-now")
        assert new_cap_resp.status_code == 201
        new_frame_id: int = new_cap_resp.json()["id"]
        with session_factory() as session:
            new_frame_row = session.get(CapturedFrame, new_frame_id)
            assert new_frame_row is not None
            assert (custom_photos / new_frame_row.original_rel_path).is_file()
            assert (custom_previews / new_frame_row.preview_rel_path).is_file()
        assert list(migrated_settings.photos_dir.rglob("*")) == []
        assert list(migrated_settings.previews_dir.rglob("*")) == []

        # Storage stats reflect relocated paths and non-zero footprints
        storage_resp = client.get("/api/admin/storage")
        assert storage_resp.status_code == 200
        storage_data = storage_resp.json()
        assert storage_data["photos_dir"] == str(custom_photos)
        assert storage_data["previews_dir"] == str(custom_previews)
        assert storage_data["renders_dir"] == str(custom_renders)
        assert storage_data["photos_bytes"] > 0
        assert storage_data["previews_bytes"] > 0
        assert storage_data["renders_bytes"] > 0

        # Deleting camera cleans up relocated storage directories and cached renders
        del_resp = client.delete(f"/api/admin/cameras/{camera_id}")
        assert del_resp.status_code == 204
        assert not (custom_photos / "migration-courtyard").exists()
        assert not (custom_previews / "migration-courtyard").exists()
        assert not (custom_renders / f"{job_id}.mp4").exists()


def test_configurable_storage_paths_validation_overlap_running_render_and_rollback(
    migrated_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(migrated_settings, enable_scheduler=False)
    with TestClient(app) as client:
        login_resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": DUMMY_ADMIN_PASS},
        )
        assert login_resp.status_code == 200

        # 1. Relative path -> 422
        rel_resp = client.put("/api/admin/settings", json={"photos_dir": "relative/photos"})
        assert rel_resp.status_code == 422

        # 2. Null byte -> 422
        null_resp = client.put("/api/admin/settings", json={"photos_dir": "/tmp/bad\x00path"})
        assert null_resp.status_code == 422

        # 3. Forbidden system paths -> 422
        for forbidden_path in ("/", "/etc/photos", "/workspace/photos", "/home/appuser/.venv/p"):
            sys_resp = client.put("/api/admin/settings", json={"photos_dir": forbidden_path})
            assert sys_resp.status_code == 422, f"Expected 422 for {forbidden_path}"

        # 4. SQLite database directory or parent data_dir overlap -> 422
        db_parent_resp = client.put(
            "/api/admin/settings",
            json={"photos_dir": str(migrated_settings.db_path.parent)},
        )
        assert db_parent_resp.status_code == 422

        data_root_resp = client.put(
            "/api/admin/settings",
            json={"photos_dir": str(migrated_settings.data_dir)},
        )
        assert data_root_resp.status_code == 422

        # 5. Overlapping or nested storage tiers -> 422
        shared_dir = str(tmp_path / "shared_tier")
        equal_overlap_resp = client.put(
            "/api/admin/settings",
            json={"photos_dir": shared_dir, "previews_dir": shared_dir},
        )
        assert equal_overlap_resp.status_code == 422

        nested_overlap_resp = client.put(
            "/api/admin/settings",
            json={
                "photos_dir": str(tmp_path / "nested_tier"),
                "previews_dir": str(tmp_path / "nested_tier" / "previews"),
            },
        )
        assert nested_overlap_resp.status_code == 422

        # 6. Unwritable / invalid directory -> 400
        file_blocker = tmp_path / "regular_file_blocker"
        file_blocker.write_bytes(b"not-a-directory")
        blocked_resp = client.put(
            "/api/admin/settings",
            json={"photos_dir": str(file_blocker / "photos")},
        )
        assert blocked_resp.status_code == 400

        if os.geteuid() != 0:
            readonly_dir = tmp_path / "readonly_photos"
            readonly_dir.mkdir(parents=True, exist_ok=True)
            readonly_dir.chmod(0o555)
            try:
                ro_resp = client.put(
                    "/api/admin/settings",
                    json={"photos_dir": str(readonly_dir)},
                )
                assert ro_resp.status_code == 400
            finally:
                readonly_dir.chmod(0o755)

        # 7. Relocating renders_dir while a RenderJob has status == 'running' -> 409
        cam_resp = client.post(
            "/api/admin/cameras",
            json={
                "name": "Rollback Gate",
                "slug": "rollback-gate",
                "source_type": "synthetic",
                "source_url": "synthetic://rollback-gate",
                "timezone": "UTC",
                "interval_seconds": 600,
                "is_enabled": True,
            },
        )
        assert cam_resp.status_code == 201
        camera_id: int = cam_resp.json()["id"]
        assert client.post(f"/api/admin/cameras/{camera_id}/capture-now").status_code == 201

        session_factory: sessionmaker[Session] = app.state.session_factory
        now_utc = datetime.now(UTC)
        admin_user_id: int = login_resp.json()["id"]
        with session_factory() as session:
            running_job = RenderJob(
                id="active-running-job",
                camera_id=camera_id,
                created_by_user_id=admin_user_id,
                mode="continuous",
                query_params_json="{}",
                fps=24,
                resolution_preset="1080p",
                watermark_timestamp=False,
                status="running",
                frame_count=2,
                processed_frames=1,
                output_rel_path=None,
                output_size_bytes=None,
                error_message=None,
                created_at=now_utc,
                completed_at=None,
                expires_at=now_utc + timedelta(hours=24),
            )
            session.add(running_job)
            session.commit()

        busy_resp = client.put(
            "/api/admin/settings",
            json={"renders_dir": str(tmp_path / "relocated_renders")},
        )
        assert busy_resp.status_code == 409

        with session_factory() as session:
            job_row = session.get(RenderJob, "active-running-job")
            assert job_row is not None
            session.delete(job_row)
            session.commit()

        # 8. Mid-migration OSError rolls back already-moved files and leaves settings unchanged
        real_move = shutil.move
        move_calls = 0

        def _failing_second_move(src: str, dst: str, *args: Any, **kwargs: Any) -> Any:
            nonlocal move_calls
            move_calls += 1
            if move_calls == 2:
                raise OSError("Simulated cross-device I/O failure")
            return real_move(src, dst, *args, **kwargs)

        monkeypatch.setattr(shutil, "move", _failing_second_move)
        rollback_target = tmp_path / "rollback_target"
        fail_resp = client.put(
            "/api/admin/settings",
            json={
                "photos_dir": str(rollback_target / "photos"),
                "previews_dir": str(rollback_target / "previews"),
            },
        )
        assert fail_resp.status_code == 500
        assert len(list(migrated_settings.photos_dir.rglob("*.jpg"))) == 1
        assert len(list(migrated_settings.previews_dir.rglob("*.webp"))) == 1
        current_prefs = client.get("/api/admin/settings").json()
        assert current_prefs["photos_dir"] == str(migrated_settings.photos_dir)

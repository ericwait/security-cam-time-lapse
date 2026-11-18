"""Guardian Suite #2: Camera secret encryption at rest and error-payload scrubbing."""

import base64
import sqlite3
from contextlib import closing
from io import BytesIO
from pathlib import Path
from typing import Final

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.security import decrypt_secret

DUMMY_ADMIN_PASS: Final[str] = "test-only-dummy-admin-0000"
DUMMY_CAM_SECRET: Final[str] = "cam-dummy-credential-alpha-99"


def _assert_plaintext_absent_from_sqlite_files(db_path: Path, secret: str) -> None:
    """Scan main SQLite DB, WAL, and SHM files on disk for raw or encoded secret bytes."""
    secret_bytes = secret.encode("utf-8")
    candidates = [
        db_path,
        Path(f"{db_path}-wal"),
        Path(f"{db_path}-shm"),
    ]
    assert db_path.is_file()
    for path in candidates:
        if path.is_file():
            raw_bytes = path.read_bytes()
            assert secret_bytes not in raw_bytes, f"Plaintext secret leaked into {path.name}"


def _build_sample_jpeg_bytes() -> bytes:
    """Create a small valid RGB JPEG payload for mock HTTP camera responses."""
    buf = BytesIO()
    Image.new("RGB", (640, 360), color=(42, 84, 140)).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_camera_credentials_encrypted_at_rest_and_never_echoed_in_api_or_disk(
    migrated_settings: Settings,
) -> None:
    app = create_app(migrated_settings, enable_scheduler=False)
    with TestClient(app) as client:
        login_resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": DUMMY_ADMIN_PASS},
        )
        assert login_resp.status_code == 200

        create_resp = client.post(
            "/api/admin/cameras",
            json={
                "name": "South Gate HTTP",
                "source_type": "http",
                "source_url": "http://camera.local/snapshot.jpg",
                "auth_type": "basic",
                "auth_username": "cam-operator",
                "secret": DUMMY_CAM_SECRET,
                "timezone": "America/Chicago",
                "interval_seconds": 900,
                "is_enabled": True,
            },
        )
        assert create_resp.status_code in (200, 201)
        created_json = create_resp.json()
        camera_id: int = created_json["id"]
        assert created_json["has_credentials"] is True
        assert created_json["auth_username"] == "cam-operator"
        assert DUMMY_CAM_SECRET not in create_resp.text
        assert "encrypted_secret" not in create_resp.text
        assert "gAAAAAB" not in create_resp.text

        list_resp = client.get("/api/admin/cameras")
        assert list_resp.status_code == 200
        listed_cams = list_resp.json()
        assert len(listed_cams) == 1
        assert listed_cams[0]["has_credentials"] is True
        assert DUMMY_CAM_SECRET not in list_resp.text
        assert "encrypted_secret" not in list_resp.text
        assert "gAAAAAB" not in list_resp.text

        viewer_cams_resp = client.get("/api/cameras")
        assert viewer_cams_resp.status_code == 200
        assert DUMMY_CAM_SECRET not in viewer_cams_resp.text
        assert "encrypted_secret" not in viewer_cams_resp.text
        assert "gAAAAAB" not in viewer_cams_resp.text

        patch_resp = client.patch(
            f"/api/admin/cameras/{camera_id}",
            json={"interval_seconds": 600},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["has_credentials"] is True
        assert patch_resp.json()["interval_seconds"] == 600
        assert DUMMY_CAM_SECRET not in patch_resp.text
        assert "encrypted_secret" not in patch_resp.text
        assert "gAAAAAB" not in patch_resp.text

    # Inspect raw SQLite storage directly with stdlib sqlite3 + byte scan on disk
    with closing(sqlite3.connect(migrated_settings.db_path)) as conn:
        row = conn.execute(
            "SELECT encrypted_secret FROM cameras WHERE id = ?",
            (camera_id,),
        ).fetchone()
    assert row is not None
    stored_token = row[0]
    assert isinstance(stored_token, str)
    assert stored_token.startswith("gAAAAAB")
    assert DUMMY_CAM_SECRET not in stored_token
    assert decrypt_secret(migrated_settings.secret_key, stored_token) == DUMMY_CAM_SECRET

    _assert_plaintext_absent_from_sqlite_files(migrated_settings.db_path, DUMMY_CAM_SECRET)


def test_source_url_with_embedded_credentials_rejected_with_422(
    migrated_settings: Settings,
) -> None:
    app = create_app(migrated_settings, enable_scheduler=False)
    with TestClient(app) as client:
        login_resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": DUMMY_ADMIN_PASS},
        )
        assert login_resp.status_code == 200

        embedded_http = f"http://user:{DUMMY_CAM_SECRET}@camera.local/snap.jpg"
        resp_http = client.post(
            "/api/admin/cameras",
            json={
                "name": "Embedded Creds Cam",
                "source_type": "http",
                "source_url": embedded_http,
                "auth_type": "none",
                "timezone": "UTC",
            },
        )
        assert resp_http.status_code == 422

        embedded_rtsp = f"rtsp://user:{DUMMY_CAM_SECRET}@camera.local:554/h264Preview_01_main"
        resp_rtsp = client.post(
            "/api/admin/cameras",
            json={
                "name": "Embedded RTSP Cam",
                "source_type": "rtsp",
                "source_url": embedded_rtsp,
                "auth_type": "none",
                "timezone": "UTC",
            },
        )
        assert resp_rtsp.status_code == 422

        probe_resp = client.post(
            "/api/admin/cameras/test-connection",
            json={
                "source_type": "http",
                "source_url": embedded_http,
                "auth_type": "none",
                "timezone": "UTC",
            },
        )
        assert probe_resp.status_code == 422

        assert client.get("/api/admin/cameras").json() == []


def test_http_transport_decrypts_in_memory_and_scrubs_secrets_from_capture_errors(
    migrated_settings: Settings,
) -> None:
    sample_jpeg = _build_sample_jpeg_bytes()
    observed_auth_headers: list[str | None] = []
    transport_mode = "ok"

    def _mock_camera_handler(request: httpx.Request) -> httpx.Response:
        observed_auth_headers.append(request.headers.get("authorization"))
        if transport_mode == "ok":
            return httpx.Response(
                200,
                content=sample_jpeg,
                headers={"content-type": "image/jpeg"},
            )
        if transport_mode == "raise_error":
            raise httpx.ConnectError(
                f"Upstream camera rejected {DUMMY_CAM_SECRET} on "
                f"http://cam-operator:{DUMMY_CAM_SECRET}@camera.local/snap.jpg"
            )
        return httpx.Response(
            500,
            text=f"Upstream auth failure for secret={DUMMY_CAM_SECRET}",
        )

    mock_transport = httpx.MockTransport(_mock_camera_handler)
    app = create_app(
        migrated_settings,
        enable_scheduler=False,
        http_transport=mock_transport,
    )

    with TestClient(app) as client:
        login_resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": DUMMY_ADMIN_PASS},
        )
        assert login_resp.status_code == 200

        cam_resp = client.post(
            "/api/admin/cameras",
            json={
                "name": "Loading Dock HTTP",
                "source_type": "http",
                "source_url": "http://camera.local/snap.jpg",
                "auth_type": "basic",
                "auth_username": "cam-operator",
                "secret": DUMMY_CAM_SECRET,
                "timezone": "America/Chicago",
                "interval_seconds": 900,
                "is_enabled": True,
            },
        )
        assert cam_resp.status_code in (200, 201)
        camera_id: int = cam_resp.json()["id"]

        # 1. Verify in-memory decryption sends exact HTTP Basic Authorization header
        ok_cap = client.post(f"/api/admin/cameras/{camera_id}/capture-now")
        assert ok_cap.status_code in (200, 201)
        expected_b64 = base64.b64encode(f"cam-operator:{DUMMY_CAM_SECRET}".encode()).decode("ascii")
        assert observed_auth_headers == [f"Basic {expected_b64}"]
        assert DUMMY_CAM_SECRET not in ok_cap.text

        # 2. Simulate transport exception echoing the raw secret -> 502 + scrubbed telemetry
        transport_mode = "raise_error"
        test_cap_err = client.post(f"/api/admin/cameras/{camera_id}/test-capture")
        assert test_cap_err.status_code == 502
        assert DUMMY_CAM_SECRET not in test_cap_err.text
        assert expected_b64 not in test_cap_err.text

        cap_now_err = client.post(f"/api/admin/cameras/{camera_id}/capture-now")
        assert cap_now_err.status_code == 502
        assert DUMMY_CAM_SECRET not in cap_now_err.text
        assert expected_b64 not in cap_now_err.text

        cams_after_err = client.get("/api/admin/cameras")
        assert cams_after_err.status_code == 200
        assert DUMMY_CAM_SECRET not in cams_after_err.text
        cam_state = cams_after_err.json()[0]
        assert cam_state["last_capture_status"] == "error"
        assert cam_state["last_capture_error"]
        assert DUMMY_CAM_SECRET not in cam_state["last_capture_error"]

        # 3. Simulate HTTP 500 response body echoing the raw secret -> 502 + scrubbed DB
        transport_mode = "http_500_echo"
        http_500_err = client.post(f"/api/admin/cameras/{camera_id}/capture-now")
        assert http_500_err.status_code == 502
        assert DUMMY_CAM_SECRET not in http_500_err.text

        cams_after_500 = client.get("/api/admin/cameras")
        assert DUMMY_CAM_SECRET not in cams_after_500.text

    _assert_plaintext_absent_from_sqlite_files(migrated_settings.db_path, DUMMY_CAM_SECRET)

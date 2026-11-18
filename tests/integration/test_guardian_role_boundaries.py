"""Guardian Suite #3: Multi-user bootstrap, role boundaries, and session revocation."""

import time
from typing import Any, Final

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.deps import SESSION_COOKIE_NAME
from app.main import create_app

DUMMY_ADMIN_PASS: Final[str] = "test-only-dummy-admin-0000"
DUMMY_VIEWER_PASS: Final[str] = "test-only-dummy-viewer-0000"


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


def _wait_for_export_completion(actor: _ActorClient, job_id: str) -> dict[str, Any]:
    """Poll export job status deterministically until terminal state."""
    deadline = time.monotonic() + 15.0
    last_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        resp = actor.get(f"/api/exports/{job_id}")
        assert resp.status_code == 200
        payload: dict[str, Any] = resp.json()
        last_payload = payload
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Export job {job_id} did not finish in time: {last_payload}")


def test_first_run_bootstrap_onboarding_and_conflict_on_repeat(
    unbootstrapped_migrated_settings: Settings,
) -> None:
    app = create_app(unbootstrapped_migrated_settings, enable_scheduler=False)
    founder_pass = "cam-dummy-founder-pass-01"

    with TestClient(app) as raw_client:
        founder = _ActorClient(raw_client)
        outsider = _ActorClient(raw_client)

        status_before = founder.get("/api/auth/status")
        assert status_before.status_code == 200
        assert status_before.json() == {"needs_bootstrap": True, "user": None}

        boot_resp = founder.post(
            "/api/auth/bootstrap",
            json={"username": "founder-admin", "password": founder_pass},
        )
        assert boot_resp.status_code == 201
        assert founder.session_token is not None
        assert founder_pass not in boot_resp.text
        assert "password_hash" not in boot_resp.text
        boot_user = boot_resp.json()
        assert boot_user["username"] == "founder-admin"
        assert boot_user["role"] == "admin"
        assert boot_user["is_active"] is True

        status_after = founder.get("/api/auth/status")
        assert status_after.status_code == 200
        status_payload = status_after.json()
        assert status_payload["needs_bootstrap"] is False
        assert status_payload["user"]["username"] == "founder-admin"
        assert status_payload["user"]["role"] == "admin"

        repeat_authed = founder.post(
            "/api/auth/bootstrap",
            json={"username": "second-admin", "password": DUMMY_ADMIN_PASS},
        )
        assert repeat_authed.status_code == 409

        repeat_anon = outsider.post(
            "/api/auth/bootstrap",
            json={"username": "intruder-admin", "password": DUMMY_VIEWER_PASS},
        )
        assert repeat_anon.status_code == 409


def test_unauthenticated_client_rejected_across_all_protected_endpoints(
    migrated_settings: Settings,
) -> None:
    app = create_app(migrated_settings, enable_scheduler=False)
    with TestClient(app) as raw_client:
        admin = _ActorClient(raw_client)
        anon = _ActorClient(raw_client)

        login_resp = admin.post(
            "/api/auth/login",
            json={"username": "admin", "password": DUMMY_ADMIN_PASS},
        )
        assert login_resp.status_code == 200

        user_resp = admin.post(
            "/api/admin/users",
            json={
                "username": "target-viewer",
                "password": DUMMY_VIEWER_PASS,
                "role": "viewer",
            },
        )
        assert user_resp.status_code in (200, 201)
        target_user_id: int = user_resp.json()["id"]

        cam_resp = admin.post(
            "/api/admin/cameras",
            json={
                "name": "North Driveway",
                "source_type": "synthetic",
                "source_url": "synthetic://north-driveway",
                "auth_type": "none",
                "timezone": "America/Chicago",
                "interval_seconds": 900,
                "is_enabled": True,
            },
        )
        assert cam_resp.status_code in (200, 201)
        camera_id: int = cam_resp.json()["id"]

        cap_resp = admin.post(f"/api/admin/cameras/{camera_id}/capture-now")
        assert cap_resp.status_code in (200, 201)
        frame_id: int = cap_resp.json()["id"]

        continuous_query = {
            "camera_id": camera_id,
            "mode": "continuous",
            "start_timestamp": "2000-01-01T00:00:00Z",
            "end_timestamp": "2099-12-31T23:59:59Z",
        }
        unauthed_matrix: list[tuple[str, str, dict[str, Any] | None]] = [
            ("GET", "/api/cameras", None),
            ("POST", "/api/timelapse/query", continuous_query),
            ("GET", f"/api/frames/{frame_id}/preview", None),
            ("GET", f"/api/frames/{frame_id}/original", None),
            (
                "POST",
                "/api/exports",
                {
                    "query": continuous_query,
                    "fps": 10,
                    "resolution_preset": "1080p",
                    "watermark_timestamp": False,
                },
            ),
            ("GET", "/api/exports/00000000-0000-0000-0000-000000000000", None),
            ("GET", "/api/exports/00000000-0000-0000-0000-000000000000/download", None),
            (
                "POST",
                "/api/auth/password",
                {
                    "current_password": DUMMY_VIEWER_PASS,
                    "new_password": "cam-dummy-new-pass-09",
                },
            ),
            ("GET", "/api/admin/users", None),
            (
                "POST",
                "/api/admin/users",
                {
                    "username": "rogue-user",
                    "password": DUMMY_VIEWER_PASS,
                    "role": "admin",
                },
            ),
            ("PATCH", f"/api/admin/users/{target_user_id}", {"role": "admin"}),
            ("DELETE", f"/api/admin/users/{target_user_id}", None),
            ("GET", "/api/admin/cameras", None),
            (
                "POST",
                "/api/admin/cameras",
                {
                    "name": "Rogue Cam",
                    "source_type": "synthetic",
                    "source_url": "synthetic://rogue",
                    "timezone": "UTC",
                },
            ),
            ("PATCH", f"/api/admin/cameras/{camera_id}", {"is_enabled": False}),
            ("DELETE", f"/api/admin/cameras/{camera_id}", None),
            (
                "POST",
                "/api/admin/cameras/test-connection",
                {
                    "source_type": "synthetic",
                    "source_url": "synthetic://probe",
                    "timezone": "UTC",
                },
            ),
            ("POST", f"/api/admin/cameras/{camera_id}/test-capture", None),
            ("POST", f"/api/admin/cameras/{camera_id}/capture-now", None),
            ("GET", "/api/admin/settings", None),
            (
                "PUT",
                "/api/admin/settings",
                {
                    "default_timezone": "UTC",
                    "render_cache_ttl_hours": 12,
                    "default_tolerance_minutes": 10,
                },
            ),
            ("GET", "/api/admin/storage", None),
        ]

        for method, path, body in unauthed_matrix:
            resp = (
                anon.request(method, path, json=body)
                if body is not None
                else anon.request(method, path)
            )
            assert resp.status_code == 401, (
                f"Expected 401 on {method} {path}, got {resp.status_code}"
            )


def test_viewer_role_allowed_on_enabled_playback_and_rejected_on_all_admin_routes(
    migrated_settings: Settings,
) -> None:
    app = create_app(migrated_settings, enable_scheduler=False)
    with TestClient(app) as raw_client:
        admin = _ActorClient(raw_client)
        viewer = _ActorClient(raw_client)

        admin_login = admin.post(
            "/api/auth/login",
            json={"username": "admin", "password": DUMMY_ADMIN_PASS},
        )
        assert admin_login.status_code == 200

        viewer_create = admin.post(
            "/api/admin/users",
            json={
                "username": "household-viewer",
                "password": DUMMY_VIEWER_PASS,
                "role": "viewer",
            },
        )
        assert viewer_create.status_code in (200, 201)

        spare_create = admin.post(
            "/api/admin/users",
            json={
                "username": "spare-viewer",
                "password": DUMMY_VIEWER_PASS,
                "role": "viewer",
            },
        )
        assert spare_create.status_code in (200, 201)
        spare_user_id: int = spare_create.json()["id"]

        cam_create = admin.post(
            "/api/admin/cameras",
            json={
                "name": "Garden Terrace",
                "source_type": "synthetic",
                "source_url": "synthetic://garden-terrace",
                "auth_type": "none",
                "timezone": "America/Chicago",
                "interval_seconds": 900,
                "is_enabled": True,
            },
        )
        assert cam_create.status_code in (200, 201)
        camera_id: int = cam_create.json()["id"]

        first_cap = admin.post(f"/api/admin/cameras/{camera_id}/capture-now")
        second_cap = admin.post(f"/api/admin/cameras/{camera_id}/capture-now")
        assert first_cap.status_code in (200, 201)
        assert second_cap.status_code in (200, 201)
        frame_id: int = first_cap.json()["id"]

        viewer_login = viewer.post(
            "/api/auth/login",
            json={"username": "household-viewer", "password": DUMMY_VIEWER_PASS},
        )
        assert viewer_login.status_code == 200
        assert viewer_login.json()["role"] == "viewer"

        # Viewer allowed across enabled playback, still download, export, and own password update
        cams_resp = viewer.get("/api/cameras")
        assert cams_resp.status_code == 200
        assert [c["id"] for c in cams_resp.json()] == [camera_id]

        continuous_query = {
            "camera_id": camera_id,
            "mode": "continuous",
            "start_timestamp": "2000-01-01T00:00:00Z",
            "end_timestamp": "2099-12-31T23:59:59Z",
        }
        query_resp = viewer.post("/api/timelapse/query", json=continuous_query)
        assert query_resp.status_code == 200
        assert query_resp.json()["frame_count"] >= 2

        preview_resp = viewer.get(f"/api/frames/{frame_id}/preview")
        assert preview_resp.status_code == 200
        assert preview_resp.headers["content-type"].startswith("image/webp")
        assert len(preview_resp.content) > 0

        orig_resp = viewer.get(f"/api/frames/{frame_id}/original?download=1")
        assert orig_resp.status_code == 200
        assert orig_resp.headers["content-type"].startswith("image/jpeg")
        assert "attachment" in orig_resp.headers.get("content-disposition", "").lower()

        export_resp = viewer.post(
            "/api/exports",
            json={
                "query": continuous_query,
                "fps": 10,
                "resolution_preset": "1080p",
                "watermark_timestamp": False,
            },
        )
        assert export_resp.status_code == 202
        job_id: str = export_resp.json()["id"]

        completed_job = _wait_for_export_completion(viewer, job_id)
        assert completed_job["status"] == "completed"

        dl_resp = viewer.get(f"/api/exports/{job_id}/download")
        assert dl_resp.status_code == 200
        assert dl_resp.headers["content-type"].startswith("video/mp4")
        assert dl_resp.content[4:8] == b"ftyp"

        rotated_pass = "cam-dummy-viewer-rotated-88"
        pw_resp = viewer.post(
            "/api/auth/password",
            json={
                "current_password": DUMMY_VIEWER_PASS,
                "new_password": rotated_pass,
            },
        )
        assert pw_resp.status_code == 200
        assert pw_resp.json() == {"status": "ok"}

        # Viewer rejected with 403 on EVERY Admin endpoint
        admin_endpoints: list[tuple[str, str, dict[str, Any] | None]] = [
            ("GET", "/api/admin/users", None),
            (
                "POST",
                "/api/admin/users",
                {
                    "username": "escalated-admin",
                    "password": "cam-dummy-escalated-01",
                    "role": "admin",
                },
            ),
            ("PATCH", f"/api/admin/users/{spare_user_id}", {"role": "admin"}),
            ("DELETE", f"/api/admin/users/{spare_user_id}", None),
            ("GET", "/api/admin/cameras", None),
            (
                "POST",
                "/api/admin/cameras",
                {
                    "name": "Unauthorized Cam",
                    "source_type": "synthetic",
                    "source_url": "synthetic://unauth",
                    "timezone": "UTC",
                },
            ),
            ("PATCH", f"/api/admin/cameras/{camera_id}", {"name": "Tampered"}),
            ("DELETE", f"/api/admin/cameras/{camera_id}", None),
            (
                "POST",
                "/api/admin/cameras/test-connection",
                {
                    "source_type": "synthetic",
                    "source_url": "synthetic://probe",
                    "timezone": "UTC",
                },
            ),
            ("POST", f"/api/admin/cameras/{camera_id}/test-capture", None),
            ("POST", f"/api/admin/cameras/{camera_id}/capture-now", None),
            ("GET", "/api/admin/settings", None),
            (
                "PUT",
                "/api/admin/settings",
                {
                    "default_timezone": "UTC",
                    "render_cache_ttl_hours": 1,
                    "default_tolerance_minutes": 1,
                },
            ),
            ("GET", "/api/admin/storage", None),
        ]

        for method, path, body in admin_endpoints:
            resp = (
                viewer.request(method, path, json=body)
                if body is not None
                else viewer.request(method, path)
            )
            assert resp.status_code == 403, (
                f"Expected 403 on {method} {path}, got {resp.status_code}"
            )

        # Confirm zero side-effects occurred from rejected Viewer calls
        users_list = admin.get("/api/admin/users").json()
        spare_after = next(u for u in users_list if u["id"] == spare_user_id)
        assert spare_after["role"] == "viewer"
        assert all(u["username"] != "escalated-admin" for u in users_list)

        admin_cams = admin.get("/api/admin/cameras").json()
        assert len(admin_cams) == 1
        assert admin_cams[0]["name"] == "Garden Terrace"


def test_disabled_camera_hidden_and_forbidden_to_viewer_while_admin_retains_access(
    migrated_settings: Settings,
) -> None:
    app = create_app(migrated_settings, enable_scheduler=False)
    with TestClient(app) as raw_client:
        admin = _ActorClient(raw_client)
        viewer = _ActorClient(raw_client)

        admin_login = admin.post(
            "/api/auth/login",
            json={"username": "admin", "password": DUMMY_ADMIN_PASS},
        )
        assert admin_login.status_code == 200
        create_viewer_resp = admin.post(
            "/api/admin/users",
            json={
                "username": "boundary-viewer",
                "password": DUMMY_VIEWER_PASS,
                "role": "viewer",
            },
        )
        assert create_viewer_resp.status_code in (200, 201)

        viewer_login_resp = viewer.post(
            "/api/auth/login",
            json={"username": "boundary-viewer", "password": DUMMY_VIEWER_PASS},
        )
        assert viewer_login_resp.status_code == 200

        cam_resp = admin.post(
            "/api/admin/cameras",
            json={
                "name": "Back Patio",
                "source_type": "synthetic",
                "source_url": "synthetic://back-patio",
                "timezone": "America/Chicago",
                "interval_seconds": 900,
                "is_enabled": True,
            },
        )
        assert cam_resp.status_code in (200, 201)
        camera_id: int = cam_resp.json()["id"]

        cap_resp = admin.post(f"/api/admin/cameras/{camera_id}/capture-now")
        assert cap_resp.status_code in (200, 201)
        frame_id: int = cap_resp.json()["id"]

        assert [c["id"] for c in viewer.get("/api/cameras").json()] == [camera_id]

        # Admin disables the camera
        disable_resp = admin.patch(
            f"/api/admin/cameras/{camera_id}",
            json={"is_enabled": False},
        )
        assert disable_resp.status_code == 200
        assert disable_resp.json()["is_enabled"] is False

        # Viewer no longer sees disabled camera in /api/cameras and gets 403 on all access
        assert viewer.get("/api/cameras").json() == []

        query_payload = {
            "camera_id": camera_id,
            "mode": "continuous",
            "start_timestamp": "2000-01-01T00:00:00Z",
            "end_timestamp": "2099-12-31T23:59:59Z",
        }
        assert viewer.post("/api/timelapse/query", json=query_payload).status_code == 403
        assert viewer.get(f"/api/frames/{frame_id}/preview").status_code == 403
        assert viewer.get(f"/api/frames/{frame_id}/original").status_code == 403
        forbidden_export = viewer.post(
            "/api/exports",
            json={
                "query": query_payload,
                "fps": 24,
                "resolution_preset": "1080p",
                "watermark_timestamp": False,
            },
        )
        assert forbidden_export.status_code == 403

        # Admin can still inspect historical frames of the disabled camera
        admin_query = admin.post("/api/timelapse/query", json=query_payload)
        assert admin_query.status_code == 200
        assert admin_query.json()["frame_count"] == 1
        assert admin.get(f"/api/frames/{frame_id}/preview").status_code == 200
        assert admin.get(f"/api/frames/{frame_id}/original").status_code == 200


def test_admin_lockout_guardrails_and_deactivation_immediately_revokes_session(
    migrated_settings: Settings,
) -> None:
    app = create_app(migrated_settings, enable_scheduler=False)
    with TestClient(app) as raw_client:
        admin = _ActorClient(raw_client)
        viewer = _ActorClient(raw_client)

        admin_login = admin.post(
            "/api/auth/login",
            json={"username": "admin", "password": DUMMY_ADMIN_PASS},
        )
        assert admin_login.status_code == 200
        admin_id: int = admin_login.json()["id"]

        viewer_create = admin.post(
            "/api/admin/users",
            json={
                "username": "revocable-viewer",
                "password": DUMMY_VIEWER_PASS,
                "role": "viewer",
            },
        )
        assert viewer_create.status_code in (200, 201)
        viewer_id: int = viewer_create.json()["id"]

        viewer_login = viewer.post(
            "/api/auth/login",
            json={"username": "revocable-viewer", "password": DUMMY_VIEWER_PASS},
        )
        assert viewer_login.status_code == 200
        assert viewer.get("/api/cameras").status_code == 200

        # Admin self-lockout guardrails (self-delete, self-demote, self-deactivate -> 400)
        assert admin.delete(f"/api/admin/users/{admin_id}").status_code == 400
        demote_self = admin.patch(
            f"/api/admin/users/{admin_id}",
            json={"role": "viewer"},
        )
        assert demote_self.status_code == 400
        deactivate_self = admin.patch(
            f"/api/admin/users/{admin_id}",
            json={"is_active": False},
        )
        assert deactivate_self.status_code == 400

        # Deactivating Viewer immediately invalidates their active session cookie
        deactivate_resp = admin.patch(
            f"/api/admin/users/{viewer_id}",
            json={"is_active": False},
        )
        assert deactivate_resp.status_code == 200
        assert deactivate_resp.json()["is_active"] is False

        assert viewer.session_token is not None
        assert viewer.get("/api/cameras").status_code == 401
        relogin_resp = viewer.post(
            "/api/auth/login",
            json={"username": "revocable-viewer", "password": DUMMY_VIEWER_PASS},
        )
        assert relogin_resp.status_code == 401

        # Explicit logout revokes server-side session token even if cookie is replayed
        prior_admin_token = admin.session_token
        assert prior_admin_token is not None
        logout_resp = admin.post("/api/auth/logout")
        assert logout_resp.status_code == 200
        assert logout_resp.json() == {"status": "ok"}

        replayed_admin = _ActorClient(raw_client, session_token=prior_admin_token)
        assert replayed_admin.get("/api/admin/users").status_code == 401

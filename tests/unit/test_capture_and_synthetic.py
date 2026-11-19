"""Unit tests for synthetic camera rendering, capture sources, and dual-tier storage."""

import base64
import subprocess
import time
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageStat

from app.capture import (
    CaptureError,
    CaptureSourceConfig,
    build_preview_webp,
    fetch_frame_bytes,
    probe_camera_capture,
    redact_sensitive_text,
    store_dual_tier_capture,
)
from app.synthetic import render_synthetic_frame


def _make_sample_jpeg(width: int = 640, height: int = 360) -> bytes:
    img = Image.new("RGB", (width, height), color=(48, 96, 160))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _sky_mean_luma(jpeg_bytes: bytes) -> float:
    with Image.open(BytesIO(jpeg_bytes)) as img:
        w, h = img.size
        sky_crop = img.crop((0, int(h * 0.08), w, int(h * 0.42))).convert("L")
        stat = ImageStat.Stat(sky_crop)
        return float(stat.mean[0])


def _brightest_sky_centroid_x(jpeg_bytes: bytes) -> float:
    with Image.open(BytesIO(jpeg_bytes)) as img:
        w, h = img.size
        sky = img.crop((0, int(h * 0.06), w, int(h * 0.56))).convert("L")
        sw, _ = sky.size
        raw = sky.tobytes()
        weighted_x = 0.0
        total_w = 0.0
        for idx, val in enumerate(raw):
            if val >= 252:
                weighted_x += float(idx % sw) * float(val)
                total_w += float(val)
        return weighted_x / max(1.0, total_w)


def test_render_synthetic_frame_dimensions_speed_and_day_night_contrast() -> None:
    midnight_utc = datetime(2026, 6, 15, 5, 0, 0, tzinfo=UTC)
    noon_utc = datetime(2026, 6, 15, 18, 0, 0, tzinfo=UTC)

    started = time.perf_counter()
    noon_bytes = render_synthetic_frame(
        camera_name="South Courtyard",
        captured_at_utc=noon_utc,
        camera_timezone="America/Chicago",
    )
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5

    noon_repeat = render_synthetic_frame(
        camera_name="South Courtyard",
        captured_at_utc=noon_utc,
        camera_timezone="America/Chicago",
    )
    assert noon_bytes == noon_repeat

    with Image.open(BytesIO(noon_bytes)) as noon_img:
        assert noon_img.format == "JPEG"
        assert noon_img.size == (2560, 1440)

    midnight_bytes = render_synthetic_frame(
        camera_name="South Courtyard",
        captured_at_utc=midnight_utc,
        camera_timezone="America/Chicago",
    )
    assert _sky_mean_luma(noon_bytes) > _sky_mean_luma(midnight_bytes) + 70.0


def test_render_synthetic_frame_solar_azimuth_stays_anchored_to_standard_time() -> None:
    # Before and after US spring-forward (2026-03-08), 18:00 UTC is 12:00 CST standard solar noon.
    pre_dst_std_noon = render_synthetic_frame(
        camera_name="Courtyard",
        captured_at_utc=datetime(2026, 3, 7, 18, 0, 0, tzinfo=UTC),
        camera_timezone="America/Chicago",
        width=640,
        height=360,
    )
    post_dst_std_noon = render_synthetic_frame(
        camera_name="Courtyard",
        captured_at_utc=datetime(2026, 3, 9, 18, 0, 0, tzinfo=UTC),
        camera_timezone="America/Chicago",
        width=640,
        height=360,
    )
    # Wall-clock 12:00 CDT on 2026-03-09 is 17:00 UTC (11:00 solar time -> shifted east/left).
    post_dst_wall_noon = render_synthetic_frame(
        camera_name="Courtyard",
        captured_at_utc=datetime(2026, 3, 9, 17, 0, 0, tzinfo=UTC),
        camera_timezone="America/Chicago",
        width=640,
        height=360,
    )

    x_pre_std = _brightest_sky_centroid_x(pre_dst_std_noon)
    x_post_std = _brightest_sky_centroid_x(post_dst_std_noon)
    x_post_wall = _brightest_sky_centroid_x(post_dst_wall_noon)

    assert abs(x_pre_std - x_post_std) < 6.0
    assert x_post_std - x_post_wall > 15.0


def test_render_synthetic_frame_validates_inputs() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        render_synthetic_frame(
            camera_name="Gate",
            captured_at_utc=datetime(2026, 1, 1, 12, 0, 0),
            camera_timezone="UTC",
        )

    with pytest.raises(ValueError, match="Unknown IANA timezone"):
        render_synthetic_frame(
            camera_name="Gate",
            captured_at_utc=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
            camera_timezone="Not/A_Zone",
        )


def test_store_dual_tier_capture_preserves_untouched_bytes_and_downscales_preview(
    tmp_path: Path,
) -> None:
    # 2026-03-09 02:30 UTC is 2026-03-08 21:30 CDT in America/Chicago.
    captured_utc = datetime(2026, 3, 9, 2, 30, 15, 123456, tzinfo=UTC)
    original_bytes = render_synthetic_frame(
        camera_name="Driveway",
        captured_at_utc=captured_utc,
        camera_timezone="America/Chicago",
        width=2560,
        height=1440,
    )

    stored = store_dual_tier_capture(
        data_dir=tmp_path,
        camera_slug="driveway-cam",
        captured_at_utc=captured_utc,
        camera_timezone="America/Chicago",
        image_bytes=original_bytes,
    )

    assert stored.local_date == "2026-03-08"
    assert stored.original_rel_path == "driveway-cam/2026/03/08/20260309T023015123456Z.jpg"
    assert stored.preview_rel_path == "driveway-cam/2026/03/08/20260309T023015123456Z.webp"
    assert stored.width == 2560
    assert stored.height == 1440
    assert stored.size_bytes == len(original_bytes)

    saved_original = tmp_path / "photos" / stored.original_rel_path
    saved_preview = tmp_path / "previews" / stored.preview_rel_path
    assert saved_original.read_bytes() == original_bytes
    assert saved_preview.is_file()

    with Image.open(saved_preview) as preview_img:
        assert preview_img.format == "WEBP"
        assert preview_img.size == (1920, 1080)


def test_build_preview_webp_never_upscales_small_frames() -> None:
    small_jpeg = _make_sample_jpeg(640, 360)
    webp_bytes, orig_w, orig_h = build_preview_webp(small_jpeg)
    assert (orig_w, orig_h) == (640, 360)
    with Image.open(BytesIO(webp_bytes)) as preview_img:
        assert preview_img.format == "WEBP"
        assert preview_img.size == (640, 360)


def test_capture_source_repr_and_redaction_helper_hide_secrets() -> None:
    cfg = CaptureSourceConfig(
        camera_name="Front Door",
        camera_slug="front-door",
        source_type="http",
        source_url="https://cam.local/snapshot.jpg",
        auth_type="basic",
        auth_username="admin",
        secret="UltraSecret#42",
    )
    assert "UltraSecret#42" not in repr(cfg)
    scrubbed = redact_sensitive_text(
        "Failed for https://admin:UltraSecret%2342@cam.local/snap with UltraSecret#42",
        "UltraSecret#42",
    )
    assert "UltraSecret" not in scrubbed
    assert "[REDACTED]" in scrubbed


def test_fetch_frame_bytes_http_auth_modes_and_probe() -> None:
    sample_jpeg = _make_sample_jpeg(800, 600)
    seen_auth_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth_headers.append(request.headers.get("Authorization"))
        return httpx.Response(200, content=sample_jpeg, headers={"Content-Type": "image/jpeg"})

    transport = httpx.MockTransport(handler)

    none_cfg = CaptureSourceConfig(
        camera_name="Cam None",
        camera_slug="cam-none",
        source_type="http",
        source_url="http://10.0.0.10/snap.jpg",
        auth_type="none",
    )
    assert fetch_frame_bytes(none_cfg, http_transport=transport) == sample_jpeg

    basic_cfg = CaptureSourceConfig(
        camera_name="Cam Basic",
        camera_slug="cam-basic",
        source_type="http",
        source_url="https://10.0.0.11/snap.jpg",
        auth_type="basic",
        auth_username="operator",
        secret="BasicPass!9",
    )
    assert fetch_frame_bytes(basic_cfg, http_transport=transport) == sample_jpeg

    bearer_cfg = CaptureSourceConfig(
        camera_name="Cam Bearer",
        camera_slug="cam-bearer",
        source_type="http",
        source_url="https://10.0.0.12/snap.jpg",
        auth_type="bearer",
        secret="BearerTokenXYZ",
    )
    preview_webp, w, h = probe_camera_capture(bearer_cfg, http_transport=transport)
    assert (w, h) == (800, 600)
    assert len(preview_webp) > 0

    expected_basic = "Basic " + base64.b64encode(b"operator:BasicPass!9").decode("ascii")
    assert seen_auth_headers == [None, expected_basic, "Bearer BearerTokenXYZ"]


def test_fetch_frame_bytes_http_401_and_500_scrub_secrets_and_url_credentials() -> None:
    secret_val = "CamSecret$987"

    def handler_401(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            text=f"401 Unauthorized for credential {secret_val}",
            request=request,
        )

    cfg_401 = CaptureSourceConfig(
        camera_name="Garage",
        camera_slug="garage",
        source_type="http",
        source_url=f"http://admin:{secret_val}@10.0.0.20/snap.jpg",
        auth_type="basic",
        auth_username="admin",
        secret=secret_val,
    )
    with pytest.raises(CaptureError) as exc_401:
        fetch_frame_bytes(cfg_401, http_transport=httpx.MockTransport(handler_401))

    msg_401 = str(exc_401.value)
    assert secret_val not in msg_401
    assert "[REDACTED]" in msg_401

    def handler_500(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            text=f"Upstream proxy failure with key={secret_val}",
            request=request,
        )

    cfg_500 = CaptureSourceConfig(
        camera_name="Alley",
        camera_slug="alley",
        source_type="http",
        source_url="https://10.0.0.21/snap.jpg",
        auth_type="bearer",
        secret=secret_val,
    )
    with pytest.raises(CaptureError) as exc_500:
        fetch_frame_bytes(cfg_500, http_transport=httpx.MockTransport(handler_500))

    msg_500 = str(exc_500.value)
    assert secret_val not in msg_500
    assert "[REDACTED]" in msg_500


def test_fetch_frame_bytes_rtsp_redacts_stderr_credentials_and_builds_authed_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_cmds: list[list[str]] = []

    def fake_run_failure(
        cmd: list[str],
        *,
        capture_output: bool,
        timeout: float,
        check: bool,
    ) -> subprocess.CompletedProcess[bytes]:
        assert capture_output is True
        assert timeout == 20.0
        assert check is False
        captured_cmds.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout=b"",
            stderr=b"rtsp://admin:SuperSecret123@10.0.0.5/stream: 401 Unauthorized",
        )

    monkeypatch.setattr(subprocess, "run", fake_run_failure)

    rtsp_cfg = CaptureSourceConfig(
        camera_name="North Gate",
        camera_slug="north-gate",
        source_type="rtsp",
        source_url="rtsp://10.0.0.5/stream",
        auth_type="digest",
        auth_username="admin",
        secret="SuperSecret123",
    )

    with pytest.raises(CaptureError) as exc_info:
        fetch_frame_bytes(rtsp_cfg)

    err_text = str(exc_info.value)
    assert "SuperSecret123" not in err_text
    assert "[REDACTED]" in err_text
    assert "rtsp://admin:SuperSecret123@10.0.0.5/stream" in captured_cmds[0]

    sample_jpeg = _make_sample_jpeg(1280, 720)

    def fake_run_success(
        cmd: list[str],
        *,
        capture_output: bool,
        timeout: float,
        check: bool,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=sample_jpeg,
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", fake_run_success)
    assert fetch_frame_bytes(rtsp_cfg) == sample_jpeg


def test_reolink_cgi_login_snap_logout_and_bare_host_autodiscovery() -> None:
    sample_jpeg = _make_sample_jpeg(1920, 1080)
    calls: list[tuple[str, str]] = []
    cam_pass = "ReolinkTestPass!42"

    def reolink_handler(request: httpx.Request) -> httpx.Response:
        path_q = request.url.raw_path.decode("ascii")
        calls.append((request.method, path_q))
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<!doctype html><html><body>Reolink Web</body></html>",
                request=request,
            )
        if "cmd=Login" in path_q and request.method == "POST":
            return httpx.Response(
                200,
                json=[{"cmd": "Login", "code": 0, "value": {"Token": {"name": "tok123"}}}],
                request=request,
            )
        if "cmd=Snap" in path_q and "token=tok123" in path_q:
            return httpx.Response(
                200,
                headers={"content-type": "image/jpeg"},
                content=sample_jpeg,
                request=request,
            )
        if "cmd=Snap" in path_q:
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    '[{"cmd":"Snap","code":1,"error":{"detail":"please login first","rspCode":-6}}]'
                ),
                request=request,
            )
        if "cmd=Logout" in path_q and "token=tok123" in path_q:
            return httpx.Response(
                200,
                json=[{"cmd": "Logout", "code": 0, "value": {"rspCode": 200}}],
                request=request,
            )
        return httpx.Response(404, request=request)

    bare_reolink_cfg = CaptureSourceConfig(
        camera_name="Driveway Reolink",
        camera_slug="driveway-reolink",
        source_type="http",
        source_url="https://192.168.1.2",
        auth_type="digest",
        auth_username="admin",
        secret=cam_pass,
    )
    fetched = fetch_frame_bytes(
        bare_reolink_cfg,
        http_transport=httpx.MockTransport(reolink_handler),
    )
    assert fetched == sample_jpeg
    assert any("cmd=Login" in p for _, p in calls)
    assert any("cmd=Logout" in p for _, p in calls)


def test_rtsp_bare_host_falls_back_to_reolink_preview_01_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample_jpeg = _make_sample_jpeg(1920, 1080)
    seen_urls: list[str] = []

    def fake_run(
        cmd: list[str],
        *,
        capture_output: bool,
        timeout: float,
        check: bool,
    ) -> subprocess.CompletedProcess[bytes]:
        assert capture_output and not check and timeout == 20.0
        url_arg = cmd[cmd.index("-i") + 1]
        seen_urls.append(url_arg)
        if url_arg.endswith("/Preview_01_main"):
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=sample_jpeg, stderr=b""
            )
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout=b"",
            stderr=b"Server returned 404 Not Found",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    bare_rtsp_cfg = CaptureSourceConfig(
        camera_name="Backyard Reolink",
        camera_slug="backyard-reolink",
        source_type="rtsp",
        source_url="rtsp://192.168.1.2",
        auth_type="digest",
        auth_username="admin",
        secret="CamPass99",
    )
    assert fetch_frame_bytes(bare_rtsp_cfg) == sample_jpeg
    assert seen_urls == [
        "rtsp://admin:CamPass99@192.168.1.2",
        "rtsp://admin:CamPass99@192.168.1.2/Preview_01_main",
    ]

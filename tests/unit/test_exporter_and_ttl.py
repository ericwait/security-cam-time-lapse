"""Unit tests for the FFmpeg MP4 timelapse exporter and deterministic TTL cache reaper."""

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest
from PIL import Image

from app.exporter import (
    VALID_FPS,
    VALID_RESOLUTION_PRESETS,
    ExportError,
    ExportFrameInput,
    ExportOptions,
    ResolutionPreset,
    build_ffconcat_manifest,
    build_video_filter,
    find_expired_renders,
    normalize_utc,
    render_timelapse_mp4,
    unlink_render_artifact,
    validate_export_options,
)


@dataclass(frozen=True, slots=True)
class _StubRenderRecord:
    """Immutable test double satisfying the ExpirableRenderRecord protocol."""

    id: str
    expires_at: datetime
    output_rel_path: str | None


def _probe_video_stream(mp4_path: Path) -> tuple[str, str, int, int]:
    """Return (codec_name, pix_fmt, width, height) for stream v:0 via ffprobe."""
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt,width,height",
            "-of",
            "json",
            str(mp4_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    streams = payload["streams"]
    assert isinstance(streams, list) and len(streams) == 1
    stream = streams[0]
    assert isinstance(stream, dict)
    return (
        str(stream["codec_name"]),
        str(stream["pix_fmt"]),
        int(stream["width"]),
        int(stream["height"]),
    )


def test_validate_export_options_accepts_supported_fps_and_presets() -> None:
    for fps in VALID_FPS:
        for preset in VALID_RESOLUTION_PRESETS:
            validate_export_options(
                ExportOptions(
                    fps=fps,
                    resolution_preset=preset,
                    watermark_timestamp=True,
                )
            )


@pytest.mark.parametrize("invalid_fps", [0, -24, 15, 48, 120, True])
def test_validate_export_options_rejects_invalid_fps(invalid_fps: int) -> None:
    with pytest.raises(ValueError, match="Unsupported export fps"):
        validate_export_options(ExportOptions(fps=invalid_fps))


def test_validate_export_options_rejects_invalid_resolution_preset() -> None:
    bad_preset = cast(ResolutionPreset, "720p")
    with pytest.raises(ValueError, match="Unsupported resolution_preset"):
        validate_export_options(ExportOptions(resolution_preset=bad_preset))


def test_build_ffconcat_manifest_escapes_quotes_formats_duration_and_repeats_last_frame() -> None:
    frames = [
        ExportFrameInput(
            image_path=Path("/data/photos/front's_cam/2026/03/08/frame_01.jpg"),
            timestamp_label="2026-03-08 12:00:00 O'Clock CDT",
        ),
        ExportFrameInput(
            image_path=Path("/data/photos/front's_cam/2026/03/08/frame_02.jpg"),
            timestamp_label="2026-03-08 12:15:00 CDT\ninjected",
        ),
    ]

    manifest = build_ffconcat_manifest(frames, fps=24)
    lines = manifest.splitlines()

    assert lines == [
        "ffconcat version 1.0",
        "file '/data/photos/front'\\''s_cam/2026/03/08/frame_01.jpg'",
        "file_packet_meta capture_ts '2026-03-08 12:00:00 O'\\''Clock CDT'",
        "duration 0.041667",
        "file '/data/photos/front'\\''s_cam/2026/03/08/frame_02.jpg'",
        "file_packet_meta capture_ts '2026-03-08 12:15:00 CDT injected'",
        "duration 0.041667",
        "file '/data/photos/front'\\''s_cam/2026/03/08/frame_02.jpg'",
    ]
    assert manifest.endswith("\n")


def test_build_ffconcat_manifest_rejects_empty_frames_and_non_positive_fps() -> None:
    with pytest.raises(ValueError, match="empty frame sequence"):
        build_ffconcat_manifest([], fps=24)

    sample = [ExportFrameInput(image_path=Path("/tmp/a.jpg"), timestamp_label="2026-01-01")]
    with pytest.raises(ValueError, match="positive integer"):
        build_ffconcat_manifest(sample, fps=0)


def test_build_video_filter_presets_and_watermark_options(tmp_path: Path) -> None:
    missing_font = tmp_path / "missing.ttf"
    existing_font = tmp_path / "DejaVuSansMono-Bold.ttf"
    existing_font.write_bytes(b"fake-ttf-placeholder")

    assert (
        build_video_filter(
            ExportOptions(fps=24, resolution_preset="native", watermark_timestamp=False),
            font_path=missing_font,
        )
        == "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    )
    assert build_video_filter(
        ExportOptions(fps=24, resolution_preset="4k", watermark_timestamp=False),
        font_path=missing_font,
    ) == (
        "scale='min(3840,iw)':'min(2160,ih)':force_original_aspect_ratio=decrease,"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    )
    assert build_video_filter(
        ExportOptions(fps=24, resolution_preset="1080p", watermark_timestamp=False),
        font_path=missing_font,
    ) == (
        "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease,"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    )

    vf_without_font = build_video_filter(
        ExportOptions(fps=10, resolution_preset="native", watermark_timestamp=True),
        font_path=missing_font,
    )
    assert vf_without_font == (
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,"
        "drawtext=text='%{metadata\\:capture_ts}':fontcolor=white:"
        "fontsize=max(18\\,h/36):box=1:boxcolor=black@0.65:boxborderw=8:x=24:y=h-th-24"
    )

    vf_with_font = build_video_filter(
        ExportOptions(fps=10, resolution_preset="1080p", watermark_timestamp=True),
        font_path=existing_font,
    )
    assert vf_with_font == (
        "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease,"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,"
        f"drawtext=fontfile={existing_font}:text='%{{metadata\\:capture_ts}}':"
        "fontcolor=white:fontsize=max(18\\,h/36):box=1:boxcolor=black@0.65:"
        "boxborderw=8:x=24:y=h-th-24"
    )


def test_render_timelapse_mp4_executes_ffmpeg_and_encodes_expected_streams(
    tmp_path: Path,
) -> None:
    assert shutil.which("ffmpeg") is not None, "ffmpeg must be installed in test runtime"
    assert shutil.which("ffprobe") is not None, "ffprobe must be installed in test runtime"

    photos_dir = tmp_path / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    palette = [(32, 64, 160), (48, 120, 180), (90, 150, 110), (200, 140, 60)]
    frames: list[ExportFrameInput] = []
    for idx, rgb in enumerate(palette):
        img_path = photos_dir / f"20260308T180{idx}00Z.jpg"
        Image.new("RGB", (2560, 1440), color=rgb).save(img_path, format="JPEG", quality=90)
        frames.append(
            ExportFrameInput(
                image_path=img_path,
                timestamp_label=f"2026-03-08 12:0{idx}:00 CST",
            )
        )

    renders_dir = tmp_path / "cache" / "renders"
    out_1080p = renders_dir / "render_1080p_watermarked.mp4"
    progress_events: list[tuple[int, int]] = []

    result_1080p = render_timelapse_mp4(
        frames,
        output_path=out_1080p,
        options=ExportOptions(fps=10, resolution_preset="1080p", watermark_timestamp=True),
        on_progress=lambda done, total: progress_events.append((done, total)),
    )

    assert result_1080p.output_path == out_1080p
    assert result_1080p.frame_count == 4
    assert result_1080p.size_bytes > 0
    assert out_1080p.is_file()
    assert not out_1080p.with_suffix(".tmp.mp4").exists()

    assert len(progress_events) >= 1
    assert progress_events[-1] == (4, 4)
    assert all(0 <= done <= 4 and total == 4 for done, total in progress_events)
    completed_counts = [done for done, _ in progress_events]
    assert completed_counts == sorted(completed_counts)

    assert _probe_video_stream(out_1080p) == ("h264", "yuv420p", 1920, 1080)

    out_native = renders_dir / "render_native_clean.mp4"
    result_native = render_timelapse_mp4(
        frames,
        output_path=out_native,
        options=ExportOptions(fps=24, resolution_preset="native", watermark_timestamp=False),
    )

    assert result_native.output_path == out_native
    assert result_native.frame_count == 4
    assert result_native.size_bytes > 0
    assert _probe_video_stream(out_native) == ("h264", "yuv420p", 2560, 1440)


def test_render_timelapse_mp4_cleans_up_on_missing_or_corrupt_inputs(tmp_path: Path) -> None:
    assert shutil.which("ffmpeg") is not None

    output_path = tmp_path / "renders" / "failed.mp4"

    with pytest.raises(ExportError, match="empty frame sequence"):
        render_timelapse_mp4(
            [],
            output_path=output_path,
            options=ExportOptions(),
        )

    missing_frame = ExportFrameInput(
        image_path=tmp_path / "does_not_exist.jpg",
        timestamp_label="2026-03-08 12:00:00 CST",
    )
    with pytest.raises(ExportError, match="does not exist"):
        render_timelapse_mp4(
            [missing_frame],
            output_path=output_path,
            options=ExportOptions(),
        )

    corrupt_jpg = tmp_path / "corrupt.jpg"
    corrupt_jpg.write_bytes(b"not-a-valid-jpeg-stream")
    with pytest.raises(ExportError, match="ffmpeg MP4 render failed"):
        render_timelapse_mp4(
            [ExportFrameInput(image_path=corrupt_jpg, timestamp_label="2026-03-08 12:00:00 CST")],
            output_path=output_path,
            options=ExportOptions(),
        )
    assert not output_path.exists()
    assert not output_path.with_suffix(".tmp.mp4").exists()


def test_find_expired_renders_deterministic_boundaries_and_timezone_normalization() -> None:
    frozen_now = datetime(2026, 3, 8, 18, 0, 0, tzinfo=UTC)
    cdt_tz = timezone(timedelta(hours=-5))

    records = [
        _StubRenderRecord(
            id="past_1s",
            expires_at=frozen_now - timedelta(seconds=1),
            output_rel_path="past_1s.mp4",
        ),
        _StubRenderRecord(
            id="exact_boundary",
            expires_at=frozen_now,
            output_rel_path="exact_boundary.mp4",
        ),
        _StubRenderRecord(
            id="future_1s",
            expires_at=frozen_now + timedelta(seconds=1),
            output_rel_path="future_1s.mp4",
        ),
        _StubRenderRecord(
            id="naive_utc_expired",
            expires_at=datetime(2026, 3, 8, 18, 0, 0),
            output_rel_path="naive.mp4",
        ),
        _StubRenderRecord(
            id="offset_aware_retained",
            expires_at=datetime(2026, 3, 8, 13, 0, 1, tzinfo=cdt_tz),
            output_rel_path="offset_future.mp4",
        ),
    ]

    expired_at_boundary = find_expired_renders(records, now_utc=frozen_now)
    assert [record.id for record in expired_at_boundary] == [
        "past_1s",
        "exact_boundary",
        "naive_utc_expired",
    ]

    # One second earlier (frozen_now - 1s), the boundary records are still retained.
    expired_one_sec_before = find_expired_renders(
        records,
        now_utc=frozen_now - timedelta(seconds=1),
    )
    assert [record.id for record in expired_one_sec_before] == ["past_1s"]
    assert normalize_utc(datetime(2026, 3, 8, 13, 0, 0, tzinfo=cdt_tz)) == frozen_now


def test_unlink_render_artifact_deletes_cached_files_and_rejects_traversal(
    tmp_path: Path,
) -> None:
    renders_dir = tmp_path / "cache" / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)

    cached_mp4 = renders_dir / "job_01.mp4"
    cached_mp4.write_bytes(b"mp4-bytes")

    outside_mp4 = tmp_path / "cache" / "outside.mp4"
    outside_mp4.write_bytes(b"sensitive-outside-file")

    nested_dir = renders_dir / "subdir"
    nested_dir.mkdir()

    assert unlink_render_artifact(renders_dir, None) is False
    assert unlink_render_artifact(renders_dir, "   ") is False
    assert unlink_render_artifact(renders_dir, "missing.mp4") is False

    assert unlink_render_artifact(renders_dir, "job_01.mp4") is True
    assert not cached_mp4.exists()
    assert unlink_render_artifact(renders_dir, "job_01.mp4") is False

    with pytest.raises(ValueError, match="escapes renders_dir"):
        unlink_render_artifact(renders_dir, "../outside.mp4")
    assert outside_mp4.is_file()

    with pytest.raises(ValueError, match="escapes renders_dir"):
        unlink_render_artifact(renders_dir, str(outside_mp4))
    assert outside_mp4.is_file()

    with pytest.raises(ValueError, match="escapes renders_dir"):
        unlink_render_artifact(renders_dir, ".")

    with pytest.raises(ValueError, match="directory"):
        unlink_render_artifact(renders_dir, "subdir")

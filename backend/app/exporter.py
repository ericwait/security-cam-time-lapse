"""On-demand FFmpeg MP4 timelapse exporter and ephemeral TTL render cache helpers."""

import os
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal, Protocol

VALID_FPS: Final[tuple[int, ...]] = (10, 24, 30, 60)
ResolutionPreset = Literal["native", "4k", "1080p"]
VALID_RESOLUTION_PRESETS: Final[tuple[ResolutionPreset, ...]] = ("native", "4k", "1080p")
DEFAULT_FONT_PATH: Final[Path] = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf")


class ExportError(RuntimeError):
    """Raised when timelapse MP4 rendering fails or source inputs are missing."""


@dataclass(frozen=True, slots=True)
class ExportFrameInput:
    """Single full-resolution source frame and formatted timestamp label for export."""

    image_path: Path
    timestamp_label: str


@dataclass(frozen=True, slots=True)
class ExportOptions:
    """User-selectable MP4 timelapse rendering parameters."""

    fps: int = 24
    resolution_preset: ResolutionPreset = "native"
    watermark_timestamp: bool = False


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Completed MP4 export artifact metadata."""

    output_path: Path
    size_bytes: int
    frame_count: int


class ExpirableRenderRecord(Protocol):
    """Structural contract for cached render rows eligible for TTL reaping."""

    @property
    def id(self) -> str: ...

    @property
    def expires_at(self) -> datetime: ...

    @property
    def output_rel_path(self) -> str | None: ...


def validate_export_options(options: ExportOptions) -> None:
    """Validate export FPS and resolution preset against supported values."""
    if isinstance(options.fps, bool) or options.fps not in VALID_FPS:
        raise ValueError(f"Unsupported export fps {options.fps!r}; expected one of {VALID_FPS}.")
    if options.resolution_preset not in VALID_RESOLUTION_PRESETS:
        raise ValueError(
            f"Unsupported resolution_preset {options.resolution_preset!r}; "
            f"expected one of {VALID_RESOLUTION_PRESETS}."
        )


def _quote_ffconcat_token(value: str) -> str:
    """Wrap a token in single quotes using FFmpeg concat demuxer escaping rules."""
    sanitized = value.replace("\r", " ").replace("\n", " ")
    escaped = sanitized.replace("'", "'\\''")
    return f"'{escaped}'"


def build_ffconcat_manifest(frames: Sequence[ExportFrameInput], *, fps: int) -> str:
    """Build an FFmpeg ffconcat v1.0 manifest with per-frame packet metadata."""
    if not frames:
        raise ValueError("Cannot build ffconcat manifest from an empty frame sequence.")
    if isinstance(fps, bool) or fps <= 0:
        raise ValueError(f"fps must be a positive integer, got {fps!r}.")

    duration_str = f"{1.0 / fps:.6f}"
    lines: list[str] = ["ffconcat version 1.0"]
    for frame in frames:
        quoted_path = _quote_ffconcat_token(str(frame.image_path))
        quoted_label = _quote_ffconcat_token(frame.timestamp_label)
        lines.append(f"file {quoted_path}")
        lines.append(f"file_packet_meta capture_ts {quoted_label}")
        lines.append(f"duration {duration_str}")

    last_quoted_path = _quote_ffconcat_token(str(frames[-1].image_path))
    lines.append(f"file {last_quoted_path}")
    return "\n".join(lines) + "\n"


def build_video_filter(
    options: ExportOptions,
    *,
    font_path: Path = DEFAULT_FONT_PATH,
) -> str:
    """Construct the FFmpeg -vf filter graph for scaling and optional watermarking."""
    validate_export_options(options)

    if options.resolution_preset == "native":
        scale_filter = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    elif options.resolution_preset == "4k":
        scale_filter = (
            "scale='min(3840,iw)':'min(2160,ih)':force_original_aspect_ratio=decrease,"
            "scale=trunc(iw/2)*2:trunc(ih/2)*2"
        )
    else:
        scale_filter = (
            "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease,"
            "scale=trunc(iw/2)*2:trunc(ih/2)*2"
        )

    if not options.watermark_timestamp:
        return scale_filter

    font_prefix = f"fontfile={font_path}:" if font_path.exists() else ""
    watermark_filter = (
        f"drawtext={font_prefix}"
        "text='%{metadata\\:capture_ts}':"
        "fontcolor=white:"
        "fontsize=max(18\\,h/36):"
        "box=1:boxcolor=black@0.65:boxborderw=8:"
        "x=24:y=h-th-24"
    )
    return f"{scale_filter},{watermark_filter}"


def render_timelapse_mp4(
    frames: Sequence[ExportFrameInput],
    *,
    output_path: Path,
    options: ExportOptions,
    on_progress: Callable[[int, int], None] | None = None,
) -> ExportResult:
    """Render full-resolution source frames into a browser-compatible H.264 MP4."""
    validate_export_options(options)
    if not frames:
        raise ExportError("Cannot render timelapse MP4 from an empty frame sequence.")

    resolved_frames: list[ExportFrameInput] = []
    for frame in frames:
        if not frame.image_path.is_file():
            raise ExportError(f"Source frame does not exist on disk: {frame.image_path}")
        resolved_frames.append(
            ExportFrameInput(
                image_path=frame.image_path.resolve(),
                timestamp_label=frame.timestamp_label,
            )
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_output_path = output_path.with_suffix(".tmp.mp4")
    tmp_output_path.unlink(missing_ok=True)

    manifest_content = build_ffconcat_manifest(resolved_frames, fps=options.fps)
    video_filter = build_video_filter(options)
    total_frames = len(resolved_frames)

    with tempfile.TemporaryDirectory(prefix="timelapse_export_") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        concat_path = tmp_dir / "concat.txt"
        concat_path.write_text(manifest_content, encoding="utf-8")
        stderr_path = tmp_dir / "ffmpeg_stderr.log"

        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-nostdin",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-vf",
            video_filter,
            "-r",
            str(options.fps),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "tv",
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            str(tmp_output_path),
        ]

        try:
            with (
                stderr_path.open("w+", encoding="utf-8", errors="replace") as stderr_file,
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=stderr_file,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                ) as proc,
            ):
                if proc.stdout is not None:
                    for raw_line in proc.stdout:
                        line = raw_line.strip()
                        if on_progress is not None and line.startswith("frame="):
                            raw_count = line.removeprefix("frame=").strip()
                            if raw_count.isdigit():
                                parsed_frame = int(raw_count)
                                on_progress(min(parsed_frame, total_frames), total_frames)
                return_code = proc.wait()
                stderr_file.seek(0)
                stderr_output = stderr_file.read().strip()
        except OSError as exc:
            tmp_output_path.unlink(missing_ok=True)
            raise ExportError(f"Failed to execute ffmpeg: {exc}") from exc

    if return_code != 0:
        tmp_output_path.unlink(missing_ok=True)
        detail = stderr_output or f"exit code {return_code}"
        raise ExportError(f"ffmpeg MP4 render failed ({return_code}): {detail}")

    if not tmp_output_path.is_file():
        raise ExportError("ffmpeg exited with code 0 without producing an MP4 artifact.")

    os.replace(tmp_output_path, output_path)
    if on_progress is not None:
        on_progress(total_frames, total_frames)

    return ExportResult(
        output_path=output_path,
        size_bytes=output_path.stat().st_size,
        frame_count=total_frames,
    )


def normalize_utc(dt: datetime) -> datetime:
    """Normalize naive (assumed UTC) or timezone-aware datetimes to aware UTC."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def find_expired_renders[RecordT: ExpirableRenderRecord](
    records: Iterable[RecordT],
    *,
    now_utc: datetime,
) -> list[RecordT]:
    """Deterministically return records whose expires_at is at or before now_utc."""
    cutoff_utc = normalize_utc(now_utc)
    return [record for record in records if normalize_utc(record.expires_at) <= cutoff_utc]


def unlink_render_artifact(renders_dir: Path, output_rel_path: str | None) -> bool:
    """Safely delete an expired render file inside renders_dir if present."""
    if output_rel_path is None or not output_rel_path.strip():
        return False

    resolved_root = renders_dir.resolve()
    candidate = (resolved_root / output_rel_path).resolve()
    if candidate == resolved_root or not candidate.is_relative_to(resolved_root):
        raise ValueError(f"Render artifact path escapes renders_dir: {output_rel_path!r}")
    if candidate.is_dir():
        raise ValueError(f"Render artifact path points to a directory: {output_rel_path!r}")
    if not candidate.is_file():
        return False

    candidate.unlink(missing_ok=True)
    return True

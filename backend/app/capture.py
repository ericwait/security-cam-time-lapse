"""Pluggable camera frame acquisition, credential redaction, and dual-tier storage."""

import ipaddress
import json
import os
import re
import subprocess
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Final, Literal
from urllib.parse import SplitResult, parse_qs, quote, quote_plus, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from PIL import Image, UnidentifiedImageError

from app.synthetic import render_synthetic_frame

type SourceType = Literal["rtsp", "http", "synthetic"]
type AuthType = Literal["none", "basic", "digest", "bearer"]

HTTP_CAPTURE_TIMEOUT_SECONDS: Final[float] = 15.0
RTSP_CAPTURE_TIMEOUT_SECONDS: Final[float] = 20.0

_URL_USERINFO_RE: Final[re.Pattern[str]] = re.compile(
    r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^\s/?#\"'@]+(?::[^\s/?#\"']*)?)@"
)
_AUTH_HEADER_RE: Final[re.Pattern[str]] = re.compile(r"(?i)\b(Bearer|Basic)\s+([^\s,;\"'\]]+)")
_SAFE_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def redact_sensitive_text(text: str, secret: str | None = None) -> str:
    """Scrub URL userinfo credentials, auth tokens, and raw/encoded secrets from text."""
    redacted = _URL_USERINFO_RE.sub(r"\1[REDACTED]@", text)
    redacted = _AUTH_HEADER_RE.sub(r"\1 [REDACTED]", redacted)

    if secret:
        candidates = {secret, quote(secret, safe=""), quote_plus(secret)}
        for token in sorted(candidates, key=len, reverse=True):
            if token:
                redacted = redacted.replace(token, "[REDACTED]")

    return redacted


class CaptureError(RuntimeError):
    """Raised on capture, transport, or image decode failure with redacted diagnostics."""

    def __init__(self, message: str, *, secret: str | None = None) -> None:
        super().__init__(redact_sensitive_text(message, secret))


@dataclass(frozen=True, slots=True)
class CaptureSourceConfig:
    """Immutable camera capture source and credential parameters."""

    camera_name: str
    camera_slug: str
    source_type: Literal["rtsp", "http", "synthetic"]
    source_url: str
    auth_type: Literal["none", "basic", "digest", "bearer"] = "none"
    auth_username: str | None = None
    secret: str | None = field(default=None, repr=False)
    timezone: str = "UTC"


@dataclass(frozen=True, slots=True)
class StoredCapture:
    """Metadata and relative volume paths for an archived original + preview pair."""

    captured_at_utc: datetime
    camera_timezone: str
    local_date: str
    original_rel_path: str
    preview_rel_path: str
    width: int
    height: int
    size_bytes: int


def _normalize_utc_timestamp(captured_at_utc: datetime | None) -> datetime:
    if captured_at_utc is None:
        return datetime.now(UTC)
    if captured_at_utc.tzinfo is None or captured_at_utc.utcoffset() is None:
        raise CaptureError("captured_at_utc must be a timezone-aware datetime.")
    return captured_at_utc.astimezone(UTC)


def _verify_decodable_image(image_bytes: bytes, *, secret: str | None = None) -> None:
    if not image_bytes:
        raise CaptureError("Captured image payload is empty.", secret=secret)
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            img.load()
            width, height = img.size
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise CaptureError(f"Invalid image payload: {exc}", secret=secret) from exc

    if width <= 0 or height <= 0:
        raise CaptureError("Decoded image has invalid zero dimensions.", secret=secret)


def _is_private_or_local_host(hostname: str | None) -> bool:
    """Return True for RFC1918/loopback/link-local IPs and .local/.lan mDNS camera hosts."""
    if not hostname:
        return False
    lowered = hostname.strip().lower()
    if lowered == "localhost" or lowered.endswith((".local", ".lan", ".home.arpa")):
        return True
    try:
        addr = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return bool(addr.is_private or addr.is_loopback or addr.is_link_local)


def _is_reolink_cgi_url(parsed: SplitResult) -> bool:
    return parsed.path.rstrip("/").lower().endswith("/cgi-bin/api.cgi")


def _extract_reolink_login_token(
    body_text: str,
    *,
    secret: str | None = None,
) -> str:
    try:
        parsed_json: object = json.loads(body_text)
    except ValueError as exc:
        raise CaptureError(
            "Reolink CGI login returned invalid JSON response.",
            secret=secret,
        ) from exc

    if not isinstance(parsed_json, list) or not parsed_json:
        raise CaptureError("Unexpected Reolink CGI login payload.", secret=secret)

    first = parsed_json[0]
    if not isinstance(first, dict):
        raise CaptureError("Unexpected Reolink CGI login item.", secret=secret)

    code = first.get("code")
    if code != 0:
        err_obj = first.get("error")
        detail = "authentication rejected"
        rsp_code: object = "unknown"
        if isinstance(err_obj, dict):
            detail = str(err_obj.get("detail", detail))
            rsp_code = err_obj.get("rspCode", rsp_code)
        raise CaptureError(
            f"Reolink login failed ({detail}, rspCode={rsp_code}). "
            "Verify Camera Username (usually 'admin') and Password.",
            secret=secret,
        )

    val_obj = first.get("value")
    if isinstance(val_obj, dict):
        tok_obj = val_obj.get("Token")
        if isinstance(tok_obj, dict):
            token_name = tok_obj.get("name")
            if isinstance(token_name, str) and token_name:
                return token_name

    raise CaptureError("Reolink CGI login response did not include a session token.", secret=secret)


def _fetch_reolink_cgi_snapshot(
    client: httpx.Client,
    *,
    scheme: str,
    netloc: str,
    channel: str,
    username: str | None,
    secret: str | None,
) -> bytes:
    """Authenticate against Reolink's CGI API, grab a full-res Snap JPEG, and log out."""
    if not username or not secret:
        raise CaptureError(
            "Reolink camera requires credentials: set Authentication to 'digest' or 'basic' "
            "and enter your Reolink username (usually 'admin') and password.",
            secret=secret,
        )

    api_base = f"{scheme}://{netloc}/cgi-bin/api.cgi"
    login_payload = [
        {
            "cmd": "Login",
            "param": {
                "User": {
                    "Version": "0",
                    "userName": username,
                    "password": secret,
                }
            },
        }
    ]
    login_resp = client.post(f"{api_base}?cmd=Login", json=login_payload)
    login_resp.raise_for_status()
    token = _extract_reolink_login_token(login_resp.text, secret=secret)

    try:
        nonce = uuid.uuid4().hex[:10]
        safe_ch = quote(channel, safe="")
        safe_tok = quote(token, safe="")
        snap_resp = client.get(f"{api_base}?cmd=Snap&channel={safe_ch}&rs={nonce}&token={safe_tok}")
        snap_resp.raise_for_status()
        snap_bytes = snap_resp.content
        _verify_decodable_image(snap_bytes, secret=secret)
        return snap_bytes
    finally:
        with suppress(Exception):
            safe_tok = quote(token, safe="")
            client.post(
                f"{api_base}?cmd=Logout&token={safe_tok}",
                json=[{"cmd": "Logout", "param": {}}],
            )


def _probe_is_reolink_cgi(client: httpx.Client, *, scheme: str, netloc: str) -> bool:
    """Check whether a bare camera host exposes Reolink's /cgi-bin/api.cgi Snap endpoint."""
    try:
        probe = client.get(f"{scheme}://{netloc}/cgi-bin/api.cgi?cmd=Snap&channel=0")
    except (httpx.HTTPError, OSError):
        return False
    text_sample = probe.text[:300]
    return '"cmd"' in text_sample and '"Snap"' in text_sample


def _fetch_http_frame(
    source: CaptureSourceConfig,
    *,
    http_transport: httpx.BaseTransport | None = None,
) -> bytes:
    raw_url = source.source_url.strip()
    parsed = urlsplit(raw_url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.netloc:
        raise CaptureError(
            f"HTTP camera source_url must use http:// or https://: {raw_url}",
            secret=source.secret,
        )

    auth: httpx.Auth | None = None
    headers: dict[str, str] = {}
    if source.auth_type == "basic":
        auth = httpx.BasicAuth(source.auth_username or "", source.secret or "")
    elif source.auth_type == "digest":
        auth = httpx.DigestAuth(source.auth_username or "", source.secret or "")
    elif source.auth_type == "bearer":
        if not source.secret:
            raise CaptureError("Bearer authentication requires a configured secret.")
        headers["Authorization"] = f"Bearer {source.secret}"

    verify_tls = (
        True if http_transport is not None else not _is_private_or_local_host(parsed.hostname)
    )
    host_only_netloc = parsed.netloc.rpartition("@")[2] or parsed.netloc

    try:
        with httpx.Client(
            timeout=HTTP_CAPTURE_TIMEOUT_SECONDS,
            follow_redirects=True,
            verify=verify_tls,
            transport=http_transport,
        ) as client:
            if _is_reolink_cgi_url(parsed):
                channel = parse_qs(parsed.query).get("channel", ["0"])[0]
                return _fetch_reolink_cgi_snapshot(
                    client,
                    scheme=scheme,
                    netloc=host_only_netloc,
                    channel=channel,
                    username=source.auth_username,
                    secret=source.secret,
                )

            response = client.get(raw_url, auth=auth, headers=headers)
            response.raise_for_status()
            payload = response.content

            try:
                _verify_decodable_image(payload, secret=source.secret)
                return payload
            except CaptureError:
                if parsed.path in ("", "/") and _probe_is_reolink_cgi(
                    client,
                    scheme=scheme,
                    netloc=host_only_netloc,
                ):
                    return _fetch_reolink_cgi_snapshot(
                        client,
                        scheme=scheme,
                        netloc=host_only_netloc,
                        channel="0",
                        username=source.auth_username,
                        secret=source.secret,
                    )

                content_type = response.headers.get("content-type", "").lower()
                if "text/html" in content_type or payload.lstrip().startswith((b"<!", b"<html")):
                    raise CaptureError(
                        "Camera URL returned an HTML web page instead of a still image. "
                        "Enter the camera's snapshot URL (for example "
                        "/cgi-bin/api.cgi?cmd=Snap&channel=0 or /cgi-bin/snapshot.cgi).",
                        secret=source.secret,
                    ) from None
                raise
    except httpx.HTTPStatusError as exc:
        body_excerpt = exc.response.text.strip()[:300]
        detail = f"{exc} ({body_excerpt})" if body_excerpt else str(exc)
        if exc.response.status_code == 401 and source.auth_type == "none":
            detail += (
                " — Camera requires authentication: select 'digest' or 'basic' "
                "and enter your camera username and password."
            )
        raise CaptureError(detail, secret=source.secret) from exc
    except (httpx.HTTPError, OSError) as exc:
        raise CaptureError(str(exc), secret=source.secret) from exc


def _build_rtsp_url(source: CaptureSourceConfig, *, override_path: str | None = None) -> str:
    raw_url = source.source_url.strip()
    parsed = urlsplit(raw_url)
    if parsed.scheme.lower() not in {"rtsp", "rtsps"} or not parsed.netloc:
        raise CaptureError(
            f"RTSP camera source_url must use rtsp:// or rtsps://: {raw_url}",
            secret=source.secret,
        )

    effective_path = override_path if override_path is not None else parsed.path
    if source.auth_type in ("basic", "digest") and source.auth_username:
        host_port = parsed.netloc.rpartition("@")[2]
        encoded_user = quote(source.auth_username, safe="")
        encoded_secret = quote(source.secret or "", safe="")
        authed_netloc = f"{encoded_user}:{encoded_secret}@{host_port}"
        parts = (parsed.scheme, authed_netloc, effective_path, parsed.query, parsed.fragment)
        return urlunsplit(parts)

    if override_path is not None:
        return urlunsplit(
            (parsed.scheme, parsed.netloc, effective_path, parsed.query, parsed.fragment)
        )
    return raw_url


def _run_rtsp_ffmpeg_once(
    ffmpeg_url: str, *, secret: str | None
) -> subprocess.CompletedProcess[bytes]:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        ffmpeg_url,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            timeout=RTSP_CAPTURE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CaptureError(
            f"RTSP frame capture timed out after {RTSP_CAPTURE_TIMEOUT_SECONDS:.0f}s.",
            secret=secret,
        ) from exc
    except OSError as exc:
        raise CaptureError(
            f"Failed to execute ffmpeg for RTSP capture: {exc}",
            secret=secret,
        ) from exc


def _fetch_rtsp_frame(source: CaptureSourceConfig) -> bytes:
    parsed = urlsplit(source.source_url.strip())
    primary_url = _build_rtsp_url(source)
    completed = _run_rtsp_ffmpeg_once(primary_url, secret=source.secret)

    # If a bare host URL (rtsp://192.168.1.2 or rtsp://192.168.1.2:554/) failed without 401,
    # automatically try Reolink's codec-agnostic main stream path (/Preview_01_main).
    if (completed.returncode != 0 or not completed.stdout) and parsed.path in ("", "/"):
        stderr_preview = completed.stderr.decode("utf-8", errors="replace")
        if "401 Unauthorized" not in stderr_preview:
            fallback_url = _build_rtsp_url(source, override_path="/Preview_01_main")
            fallback_completed = _run_rtsp_ffmpeg_once(fallback_url, secret=source.secret)
            if fallback_completed.returncode == 0 and fallback_completed.stdout:
                _verify_decodable_image(fallback_completed.stdout, secret=source.secret)
                return fallback_completed.stdout

    if completed.returncode != 0 or not completed.stdout:
        stderr_text = completed.stderr.decode("utf-8", errors="replace").strip()
        sanitized_stderr = redact_sensitive_text(stderr_text, source.secret)
        if primary_url != source.source_url:
            sanitized_stderr = sanitized_stderr.replace(primary_url, "[REDACTED]")
        reason = sanitized_stderr or f"ffmpeg exited with code {completed.returncode}"
        if "401 Unauthorized" in stderr_text and (
            source.auth_type == "none" or not source.auth_username
        ):
            reason += (
                " (Camera requires authentication: set Authentication to 'digest' or 'basic' "
                "and enter your camera username and password.)"
            )
        raise CaptureError(f"RTSP frame capture failed: {reason}", secret=source.secret)

    _verify_decodable_image(completed.stdout, secret=source.secret)
    return completed.stdout


def fetch_frame_bytes(
    source: CaptureSourceConfig,
    *,
    captured_at_utc: datetime | None = None,
    http_transport: httpx.BaseTransport | None = None,
) -> bytes:
    """Acquire a single full-resolution still image from synthetic, HTTP(S), or RTSP."""
    effective_utc = _normalize_utc_timestamp(captured_at_utc)

    if source.source_type == "synthetic":
        try:
            return render_synthetic_frame(
                camera_name=source.camera_name,
                captured_at_utc=effective_utc,
                camera_timezone=source.timezone,
            )
        except ValueError as exc:
            raise CaptureError(str(exc), secret=source.secret) from exc

    if source.source_type == "http":
        return _fetch_http_frame(source, http_transport=http_transport)

    if source.source_type == "rtsp":
        return _fetch_rtsp_frame(source)

    raise CaptureError(f"Unsupported capture source_type: {source.source_type!r}")


def build_preview_webp(
    image_bytes: bytes,
    *,
    max_width: int = 1920,
    max_height: int = 1080,
    quality: int = 82,
) -> tuple[bytes, int, int]:
    """Decode full-res bytes, build a non-upscaled WebP preview, and return dimensions."""
    if max_width <= 0 or max_height <= 0:
        raise ValueError("max_width and max_height must be positive integers.")
    if quality < 1 or quality > 100:
        raise ValueError("quality must be between 1 and 100.")
    if not image_bytes:
        raise CaptureError("Cannot build preview from empty image payload.")

    try:
        with Image.open(BytesIO(image_bytes)) as img:
            img.load()
            orig_width, orig_height = img.size
            preview = img.convert("RGB") if img.mode != "RGB" else img.copy()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise CaptureError(f"Cannot decode image for preview generation: {exc}") from exc

    preview.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    output = BytesIO()
    preview.save(output, format="WEBP", quality=quality)
    return (output.getvalue(), orig_width, orig_height)


def _atomic_write_bytes(target_path: Path, payload: bytes) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.parent / f".{target_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        tmp_path.write_bytes(payload)
        os.replace(tmp_path, target_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def store_dual_tier_capture(
    *,
    data_dir: Path,
    camera_slug: str,
    captured_at_utc: datetime,
    camera_timezone: str,
    image_bytes: bytes,
) -> StoredCapture:
    """Persist untouched original bytes alongside a 1080p WebP preview atomically."""
    if not _SAFE_SLUG_RE.match(camera_slug):
        raise ValueError(f"Invalid camera_slug for filesystem path: {camera_slug!r}")
    if captured_at_utc.tzinfo is None or captured_at_utc.utcoffset() is None:
        raise ValueError("captured_at_utc must be a timezone-aware datetime.")
    if not camera_timezone or not camera_timezone.strip():
        raise ValueError("camera_timezone must be a non-empty IANA timezone name.")

    try:
        zone = ZoneInfo(camera_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown IANA timezone: {camera_timezone!r}") from exc

    utc_dt = captured_at_utc.astimezone(UTC)
    local_dt = utc_dt.astimezone(zone)
    local_date = local_dt.strftime("%Y-%m-%d")
    year_str = local_dt.strftime("%Y")
    month_str = local_dt.strftime("%m")
    day_str = local_dt.strftime("%d")
    stem = utc_dt.strftime("%Y%m%dT%H%M%S%fZ")

    original_rel_path = f"{camera_slug}/{year_str}/{month_str}/{day_str}/{stem}.jpg"
    preview_rel_path = f"{camera_slug}/{year_str}/{month_str}/{day_str}/{stem}.webp"

    preview_bytes, orig_width, orig_height = build_preview_webp(image_bytes)

    original_abs_path = data_dir / "photos" / original_rel_path
    preview_abs_path = data_dir / "previews" / preview_rel_path

    _atomic_write_bytes(original_abs_path, image_bytes)
    _atomic_write_bytes(preview_abs_path, preview_bytes)

    return StoredCapture(
        captured_at_utc=utc_dt,
        camera_timezone=camera_timezone,
        local_date=local_date,
        original_rel_path=original_rel_path,
        preview_rel_path=preview_rel_path,
        width=orig_width,
        height=orig_height,
        size_bytes=len(image_bytes),
    )


def probe_camera_capture(
    source: CaptureSourceConfig,
    *,
    http_transport: httpx.BaseTransport | None = None,
) -> tuple[bytes, int, int]:
    """Fetch a live frame and return (preview_webp_bytes, orig_width, orig_height) in memory."""
    frame_bytes = fetch_frame_bytes(source, http_transport=http_transport)
    return build_preview_webp(frame_bytes)

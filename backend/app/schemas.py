"""Pydantic request and response contracts for Auth, Admin, Timelapse, and Export APIs."""

import re
from datetime import date, datetime, time
from typing import Annotated, Final, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from app.capture import AuthType, SourceType
from app.deps import ensure_utc
from app.exporter import ExportOptions, ResolutionPreset, validate_export_options
from app.security import MIN_PASSWORD_LENGTH, validate_iana_timezone
from app.selection import DstMode, normalize_utc

type UserRole = Literal["admin", "viewer"]
type PlaybackMode = Literal["continuous", "daily_slice"]

_SLUG_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9._-]{0,119}$")


def _validate_clean_source_url(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("source_url must not be empty")
    parsed = urlsplit(cleaned)
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise ValueError(
            "Do not embed credentials in source_url; use auth_username and secret fields"
        )
    return cleaned


def _validate_username(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) < 3 or len(cleaned) > 64:
        raise ValueError("Username must be between 3 and 64 characters long")
    return cleaned


def _validate_password_length(value: str) -> str:
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long")
    return value


def _validate_interval_seconds(value: int) -> int:
    if isinstance(value, bool) or value < 10 or value > 86400:
        raise ValueError("interval_seconds must be between 10 and 86400")
    return value


class StatusOkOut(BaseModel):
    """Generic acknowledgement payload."""

    status: Literal["ok"] = "ok"


class UserOut(BaseModel):
    """Public user profile representation (never exposes password hashes)."""

    id: int
    username: str
    role: UserRole
    is_active: bool
    created_at: datetime

    @field_validator("created_at", mode="after")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class AuthStatusOut(BaseModel):
    """Bootstrap and current session status payload."""

    needs_bootstrap: bool
    user: UserOut | None


class BootstrapRequest(BaseModel):
    """First-run initial Admin creation request."""

    username: str
    password: str

    @field_validator("username", mode="after")
    @classmethod
    def _check_username(cls, value: str) -> str:
        return _validate_username(value)

    @field_validator("password", mode="after")
    @classmethod
    def _check_password(cls, value: str) -> str:
        return _validate_password_length(value)


class LoginRequest(BaseModel):
    """Username and password sign-in payload."""

    username: str
    password: str = Field(default="", repr=False)

    @field_validator("username", mode="after")
    @classmethod
    def _strip_username(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Username must not be empty")
        return cleaned

    @field_validator("password", mode="after")
    @classmethod
    def _check_non_empty_password(cls, value: str) -> str:
        if not value:
            raise ValueError("Password must not be empty")
        return value


class PasswordChangeRequest(BaseModel):
    """Authenticated self-service password rotation request."""

    current_password: str = Field(repr=False)
    new_password: str = Field(repr=False)

    @field_validator("current_password", mode="after")
    @classmethod
    def _check_current(cls, value: str) -> str:
        if not value:
            raise ValueError("current_password must not be empty")
        return value

    @field_validator("new_password", mode="after")
    @classmethod
    def _check_new(cls, value: str) -> str:
        return _validate_password_length(value)


class AdminUserCreate(BaseModel):
    """Admin payload to provision a new Admin or Viewer user."""

    username: str
    password: str = Field(repr=False)
    role: UserRole = "viewer"

    @field_validator("username", mode="after")
    @classmethod
    def _check_username(cls, value: str) -> str:
        return _validate_username(value)

    @field_validator("password", mode="after")
    @classmethod
    def _check_password(cls, value: str) -> str:
        return _validate_password_length(value)


class AdminUserUpdate(BaseModel):
    """Admin payload to update user role, active status, or password."""

    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, repr=False)

    @field_validator("password", mode="after")
    @classmethod
    def _check_optional_password(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_password_length(value)


class CameraAdminOut(BaseModel):
    """Admin camera representation with operational telemetry and zero raw secrets."""

    id: int
    name: str
    slug: str
    source_type: SourceType
    source_url: str
    auth_type: AuthType
    auth_username: str | None
    has_credentials: bool
    timezone: str
    interval_seconds: int
    is_enabled: bool
    frame_count: int
    last_capture_at: datetime | None
    last_capture_status: str | None
    last_capture_error: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator("last_capture_at", mode="after")
    @classmethod
    def _normalize_optional_dt(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def _normalize_required_dt(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class CameraCreate(BaseModel):
    """Admin payload to register a camera source and schedule."""

    name: str
    slug: str | None = None
    source_type: SourceType
    source_url: str
    auth_type: AuthType = "none"
    auth_username: str | None = None
    secret: str | None = Field(default=None, repr=False)
    timezone: str = "America/Chicago"
    interval_seconds: int = 900
    is_enabled: bool = True

    @field_validator("name", mode="after")
    @classmethod
    def _check_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or len(cleaned) > 120:
            raise ValueError("Camera name must be between 1 and 120 characters")
        return cleaned

    @field_validator("slug", mode="after")
    @classmethod
    def _check_slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if not cleaned:
            return None
        if _SLUG_PATTERN.match(cleaned) is None:
            raise ValueError(f"Invalid camera slug: {value!r}")
        return cleaned

    @field_validator("source_url", mode="after")
    @classmethod
    def _check_source_url(cls, value: str) -> str:
        return _validate_clean_source_url(value)

    @field_validator("timezone", mode="after")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        return validate_iana_timezone(value)

    @field_validator("interval_seconds", mode="after")
    @classmethod
    def _check_interval(cls, value: int) -> int:
        return _validate_interval_seconds(value)


class CameraUpdate(BaseModel):
    """Admin payload to modify an existing camera's configuration or schedule."""

    name: str | None = None
    source_type: SourceType | None = None
    source_url: str | None = None
    auth_type: AuthType | None = None
    auth_username: str | None = None
    secret: str | None = Field(default=None, repr=False)
    timezone: str | None = None
    interval_seconds: int | None = None
    is_enabled: bool | None = None

    @field_validator("name", mode="after")
    @classmethod
    def _check_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned or len(cleaned) > 120:
            raise ValueError("Camera name must be between 1 and 120 characters")
        return cleaned

    @field_validator("source_url", mode="after")
    @classmethod
    def _check_source_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_clean_source_url(value)

    @field_validator("timezone", mode="after")
    @classmethod
    def _check_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_iana_timezone(value)

    @field_validator("interval_seconds", mode="after")
    @classmethod
    def _check_interval(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _validate_interval_seconds(value)


class CameraConnectionTestRequest(BaseModel):
    """In-memory live probe payload before persisting a camera."""

    name: str = "Connection Test"
    source_type: SourceType
    source_url: str
    auth_type: AuthType = "none"
    auth_username: str | None = None
    secret: str | None = Field(default=None, repr=False)
    timezone: str = "UTC"

    @field_validator("source_url", mode="after")
    @classmethod
    def _check_source_url(cls, value: str) -> str:
        return _validate_clean_source_url(value)

    @field_validator("timezone", mode="after")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        return validate_iana_timezone(value)


class CameraProbeOut(BaseModel):
    """Live camera connection test result with inline WebP preview data URL."""

    ok: bool = True
    width: int
    height: int
    preview_data_url: str


class SystemPreferencesOut(BaseModel):
    """Global operational defaults managed by Admins."""

    default_timezone: str
    render_cache_ttl_hours: int
    default_tolerance_minutes: int


class SystemPreferencesUpdate(BaseModel):
    """Update payload for global system settings."""

    default_timezone: str | None = None
    render_cache_ttl_hours: int | None = None
    default_tolerance_minutes: int | None = None

    @field_validator("default_timezone", mode="after")
    @classmethod
    def _check_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_iana_timezone(value)

    @field_validator("render_cache_ttl_hours", mode="after")
    @classmethod
    def _check_ttl(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or value < 1 or value > 720:
            raise ValueError("render_cache_ttl_hours must be between 1 and 720")
        return value

    @field_validator("default_tolerance_minutes", mode="after")
    @classmethod
    def _check_tolerance(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or value < 0 or value > 720:
            raise ValueError("default_tolerance_minutes must be between 0 and 720")
        return value


class StorageStatsOut(BaseModel):
    """Volume capacity and directory footprint summary for Admin operators."""

    disk_total_bytes: int
    disk_used_bytes: int
    disk_free_bytes: int
    photos_bytes: int
    previews_bytes: int
    renders_bytes: int
    total_frames: int
    active_renders: int


class CameraViewerOut(BaseModel):
    """Enabled camera metadata exposed to Viewers and Admins for playback."""

    id: int
    name: str
    slug: str
    timezone: str
    interval_seconds: int
    frame_count: int
    earliest_capture_at: datetime | None
    latest_capture_at: datetime | None

    @field_validator("earliest_capture_at", "latest_capture_at", mode="after")
    @classmethod
    def _normalize_bounds(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None


class ContinuousTimelapseQuery(BaseModel):
    """Mode 1: Every captured frame within an inclusive timestamp range."""

    mode: Literal["continuous"]
    camera_id: int
    start_timestamp: datetime
    end_timestamp: datetime

    @field_validator("start_timestamp", "end_timestamp", mode="after")
    @classmethod
    def _require_aware_utc(cls, value: datetime) -> datetime:
        return normalize_utc(value)

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        if self.end_timestamp < self.start_timestamp:
            raise ValueError("end_timestamp must be greater than or equal to start_timestamp")
        return self


class DailySliceTimelapseQuery(BaseModel):
    """Mode 2: Closest daily frame at target_time across a calendar date span."""

    mode: Literal["daily_slice"]
    camera_id: int
    start_date: date
    end_date: date
    target_time: time
    tolerance_minutes: int | None = None
    dst_mode: DstMode = "wall_clock"

    @field_validator("tolerance_minutes", mode="after")
    @classmethod
    def _check_tolerance(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or value < 0 or value > 720:
            raise ValueError("tolerance_minutes must be between 0 and 720")
        return value

    @model_validator(mode="after")
    def _check_dates(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be greater than or equal to start_date")
        return self


TimelapseQueryRequest = Annotated[
    ContinuousTimelapseQuery | DailySliceTimelapseQuery,
    Field(discriminator="mode"),
]


class FrameOut(BaseModel):
    """Single selected frame metadata and preview/original/download endpoints."""

    id: int
    camera_id: int
    captured_at_utc: datetime
    local_timestamp: str
    local_date: str
    width: int
    height: int
    size_bytes: int
    preview_url: str
    original_url: str
    download_url: str

    @field_validator("captured_at_utc", mode="after")
    @classmethod
    def _normalize_captured_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class TimelapseQueryResponse(BaseModel):
    """Ordered sequence of matching frames for interactive scrubbing."""

    camera_id: int
    camera_name: str
    camera_timezone: str
    mode: PlaybackMode
    frame_count: int
    frames: list[FrameOut]


class ExportCreateRequest(BaseModel):
    """On-demand MP4 timelapse export creation request."""

    query: TimelapseQueryRequest
    fps: int = 24
    resolution_preset: ResolutionPreset = "native"
    watermark_timestamp: bool = False

    @model_validator(mode="after")
    def _check_export_options(self) -> Self:
        validate_export_options(
            ExportOptions(
                fps=self.fps,
                resolution_preset=self.resolution_preset,
                watermark_timestamp=self.watermark_timestamp,
            )
        )
        return self


class RenderJobOut(BaseModel):
    """Ephemeral MP4 export job progress and download status."""

    id: str
    camera_id: int
    mode: str
    fps: int
    resolution_preset: str
    watermark_timestamp: bool
    status: str
    frame_count: int
    processed_frames: int
    output_size_bytes: int | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None
    expires_at: datetime
    download_url: str | None

    @field_validator("created_at", "expires_at", mode="after")
    @classmethod
    def _normalize_required_job_dt(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("completed_at", mode="after")
    @classmethod
    def _normalize_optional_job_dt(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

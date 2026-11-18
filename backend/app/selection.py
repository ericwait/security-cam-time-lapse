import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import MAXYEAR, MINYEAR, UTC, date, datetime, time, timedelta, timezone
from typing import Final, Literal, Protocol, TypeVar, runtime_checkable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DstMode = Literal["wall_clock", "standard_offset"]

VALID_DST_MODES: Final[frozenset[str]] = frozenset({"wall_clock", "standard_offset"})

_FIXED_OFFSET_ZONE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:[+-]|(?:UTC|GMT|Etc/GMT)[+-]\d)",
    re.IGNORECASE,
)


@runtime_checkable
class SelectableFrame(Protocol):
    """Structural protocol satisfied by ORM Frame models and pure test records."""

    @property
    def id(self) -> int: ...

    @property
    def captured_at_utc(self) -> datetime: ...


FrameT = TypeVar("FrameT", bound=SelectableFrame)


@dataclass(frozen=True, slots=True)
class FrameRecord:
    """Immutable lightweight frame record for unit tests and in-memory selection."""

    id: int
    captured_at_utc: datetime


def normalize_utc(dt: datetime) -> datetime:
    """Validate that ``dt`` is timezone-aware and normalize it to UTC."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware")
    return dt.astimezone(UTC)


def resolve_camera_zone(camera_timezone: str) -> ZoneInfo:
    """Resolve and validate an IANA timezone name, rejecting raw fixed offsets."""
    if (
        not camera_timezone
        or camera_timezone != camera_timezone.strip()
        or camera_timezone.startswith("/")
        or ".." in camera_timezone
        or _FIXED_OFFSET_ZONE_RE.match(camera_timezone) is not None
    ):
        raise ValueError(f"Invalid IANA timezone: {camera_timezone!r}")

    try:
        return ZoneInfo(camera_timezone)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise ValueError(f"Invalid IANA timezone: {camera_timezone!r}") from exc


def compute_standard_offset(tz: ZoneInfo, reference_year: int) -> timedelta:
    """Compute the standard (non-DST) UTC offset of ``tz`` for ``reference_year``."""
    if not (MINYEAR <= reference_year <= MAXYEAR):
        raise ValueError(f"reference_year must be between {MINYEAR} and {MAXYEAR}")

    fallback_offset: timedelta | None = None
    for month in range(1, 13):
        sample = datetime(reference_year, month, 1, 12, 0, tzinfo=tz)
        utc_offset = sample.utcoffset()
        if utc_offset is None:
            continue
        dst_delta = sample.dst()
        if dst_delta == timedelta(0):
            return utc_offset
        if fallback_offset is None:
            fallback_offset = utc_offset - (dst_delta or timedelta(0))

    if fallback_offset is not None:
        return fallback_offset
    return timedelta(0)


def format_frame_local_timestamp(captured_at_utc: datetime, camera_timezone: str) -> str:
    """Format a UTC capture timestamp in the camera's local IANA timezone."""
    utc_dt = normalize_utc(captured_at_utc)
    tz = resolve_camera_zone(camera_timezone)
    local_dt = utc_dt.astimezone(tz)
    tz_label = local_dt.tzname() or camera_timezone
    return f"{local_dt:%Y-%m-%d %H:%M:%S} {tz_label}"


def select_continuous_frames[T: SelectableFrame](
    frames: Iterable[T],
    *,
    start_timestamp: datetime,
    end_timestamp: datetime,
) -> list[T]:
    """Select all frames inclusively within ``[start_timestamp, end_timestamp]``."""
    start_utc = normalize_utc(start_timestamp)
    end_utc = normalize_utc(end_timestamp)
    if end_utc < start_utc:
        raise ValueError("end_timestamp must be greater than or equal to start_timestamp")

    matched: list[tuple[datetime, T]] = []
    for frame in frames:
        captured_utc = normalize_utc(frame.captured_at_utc)
        if start_utc <= captured_utc <= end_utc:
            matched.append((captured_utc, frame))

    matched.sort(key=lambda item: (item[0], item[1].id))
    return [frame for _, frame in matched]


def _pick_closest_by_utc[T: SelectableFrame](
    candidates: Iterable[tuple[datetime, T]],
    *,
    target_utc: datetime,
    tolerance_delta: timedelta,
) -> T | None:
    best_rank: tuple[timedelta, datetime, int] | None = None
    best_frame: T | None = None
    for captured_utc, frame in candidates:
        distance = abs(captured_utc - target_utc)
        if distance <= tolerance_delta:
            rank = (distance, captured_utc, frame.id)
            if best_rank is None or rank < best_rank:
                best_rank = rank
                best_frame = frame
    return best_frame


def _pick_closest_by_wall_clock[T: SelectableFrame](
    candidates: Iterable[tuple[datetime, datetime, T]],
    *,
    target_local_naive: datetime,
    tolerance_delta: timedelta,
) -> T | None:
    best_rank: tuple[timedelta, datetime, int] | None = None
    best_frame: T | None = None
    for captured_utc, captured_local_naive, frame in candidates:
        distance = abs(captured_local_naive - target_local_naive)
        if distance <= tolerance_delta:
            rank = (distance, captured_utc, frame.id)
            if best_rank is None or rank < best_rank:
                best_rank = rank
                best_frame = frame
    return best_frame


def _select_wall_clock_day_frame[T: SelectableFrame](
    day_candidates: list[tuple[datetime, datetime, T]],
    *,
    current_day: date,
    clean_target_time: time,
    tz: ZoneInfo,
    tolerance_delta: timedelta,
) -> T | None:
    if not day_candidates:
        return None

    target_local_naive = datetime.combine(current_day, clean_target_time)
    dt0 = target_local_naive.replace(tzinfo=tz, fold=0)
    dt1 = target_local_naive.replace(tzinfo=tz, fold=1)

    roundtrip_naive = dt0.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
    if roundtrip_naive != target_local_naive:
        return _pick_closest_by_wall_clock(
            day_candidates,
            target_local_naive=target_local_naive,
            tolerance_delta=tolerance_delta,
        )

    utc_candidates = [(captured_utc, frame) for captured_utc, _, frame in day_candidates]
    primary_match = _pick_closest_by_utc(
        utc_candidates,
        target_utc=dt0.astimezone(UTC),
        tolerance_delta=tolerance_delta,
    )
    if primary_match is not None:
        return primary_match

    if dt0.utcoffset() != dt1.utcoffset():
        return _pick_closest_by_utc(
            utc_candidates,
            target_utc=dt1.astimezone(UTC),
            tolerance_delta=tolerance_delta,
        )

    return None


def select_daily_slice_frames[T: SelectableFrame](
    frames: Iterable[T],
    *,
    start_date: date,
    end_date: date,
    target_time: time,
    camera_timezone: str,
    tolerance_minutes: int = 15,
    dst_mode: DstMode = "wall_clock",
) -> list[T]:
    """Select at most one closest frame per calendar day within ``+/-tolerance_minutes``."""
    if end_date < start_date:
        raise ValueError("end_date must be greater than or equal to start_date")
    if tolerance_minutes < 0:
        raise ValueError("tolerance_minutes must be non-negative")
    if dst_mode not in VALID_DST_MODES:
        raise ValueError(f"Unsupported dst_mode: {dst_mode!r}")

    tz = resolve_camera_zone(camera_timezone)
    clean_target_time = target_time.replace(tzinfo=None)
    tolerance_delta = timedelta(minutes=tolerance_minutes)

    normalized_frames = [(normalize_utc(frame.captured_at_utc), frame) for frame in frames]
    if not normalized_frames:
        return []

    total_days = (end_date - start_date).days + 1
    selected: list[T] = []

    if dst_mode == "standard_offset":
        std_tz_by_year = {
            year: timezone(compute_standard_offset(tz, year))
            for year in range(start_date.year, end_date.year + 1)
        }
        buckets_by_tz: dict[timezone, dict[date, list[tuple[datetime, T]]]] = {}
        for std_tz in set(std_tz_by_year.values()):
            day_buckets: dict[date, list[tuple[datetime, T]]] = defaultdict(list)
            for captured_utc, frame in normalized_frames:
                local_day = captured_utc.astimezone(std_tz).date()
                if start_date <= local_day <= end_date:
                    day_buckets[local_day].append((captured_utc, frame))
            buckets_by_tz[std_tz] = day_buckets

        for day_offset in range(total_days):
            current_day = start_date + timedelta(days=day_offset)
            std_tz = std_tz_by_year[current_day.year]
            target_utc = datetime.combine(
                current_day,
                clean_target_time,
                tzinfo=std_tz,
            ).astimezone(UTC)
            chosen = _pick_closest_by_utc(
                buckets_by_tz[std_tz].get(current_day, ()),
                target_utc=target_utc,
                tolerance_delta=tolerance_delta,
            )
            if chosen is not None:
                selected.append(chosen)
        return selected

    wall_buckets: dict[date, list[tuple[datetime, datetime, T]]] = defaultdict(list)
    for captured_utc, frame in normalized_frames:
        local_dt = captured_utc.astimezone(tz)
        local_day = local_dt.date()
        if start_date <= local_day <= end_date:
            wall_buckets[local_day].append((captured_utc, local_dt.replace(tzinfo=None), frame))

    for day_offset in range(total_days):
        current_day = start_date + timedelta(days=day_offset)
        chosen = _select_wall_clock_day_frame(
            wall_buckets.get(current_day, []),
            current_day=current_day,
            clean_target_time=clean_target_time,
            tz=tz,
            tolerance_delta=tolerance_delta,
        )
        if chosen is not None:
            selected.append(chosen)

    return selected

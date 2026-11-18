from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from app.selection import (
    DstMode,
    FrameRecord,
    SelectableFrame,
    compute_standard_offset,
    format_frame_local_timestamp,
    normalize_utc,
    resolve_camera_zone,
    select_continuous_frames,
    select_daily_slice_frames,
)


class _UnorderedOrmLikeFrame:
    """Custom SelectableFrame without __lt__ to mimic SQLAlchemy ORM instances."""

    def __init__(self, frame_id: int, captured_at_utc: datetime) -> None:
        self._id = frame_id
        self._captured_at_utc = captured_at_utc

    @property
    def id(self) -> int:
        return self._id

    @property
    def captured_at_utc(self) -> datetime:
        return self._captured_at_utc


def test_frame_record_conforms_to_protocol_and_is_immutable() -> None:
    record = FrameRecord(id=1, captured_at_utc=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    assert isinstance(record, SelectableFrame)

    with pytest.raises(FrozenInstanceError):
        record.id = 2  # type: ignore[misc]


def test_normalize_utc_converts_offsets_and_rejects_naive_datetimes() -> None:
    chicago_offset = timezone(timedelta(hours=-6))
    aware = datetime(2026, 1, 15, 12, 30, tzinfo=chicago_offset)
    normalized = normalize_utc(aware)

    assert normalized == datetime(2026, 1, 15, 18, 30, tzinfo=UTC)
    assert normalized.tzinfo is UTC

    with pytest.raises(ValueError, match="Timestamp must be timezone-aware"):
        normalize_utc(datetime(2026, 1, 15, 12, 30))


@pytest.mark.parametrize(
    "invalid_zone",
    [
        "",
        "   ",
        " America/Chicago",
        "America/Chicago ",
        "-06:00",
        "+05:00",
        "+00:00",
        "UTC-06:00",
        "UTC+5",
        "GMT+5",
        "Etc/GMT+6",
        "Etc/GMT-10",
        "Not/A_Zone",
        "../UTC",
        "/America/Chicago",
    ],
)
def test_resolve_camera_zone_rejects_fixed_offsets_and_invalid_names(invalid_zone: str) -> None:
    with pytest.raises(ValueError, match="Invalid IANA timezone"):
        resolve_camera_zone(invalid_zone)


def test_resolve_camera_zone_accepts_valid_iana_names() -> None:
    assert resolve_camera_zone("America/Chicago") == ZoneInfo("America/Chicago")
    assert resolve_camera_zone("Australia/Sydney") == ZoneInfo("Australia/Sydney")
    assert resolve_camera_zone("UTC") == ZoneInfo("UTC")


class _YearRoundDstZone(tzinfo):
    """Synthetic timezone reporting year-round +1h DST to exercise fallback math."""

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return timedelta(hours=-4)

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(hours=1)

    def tzname(self, dt: datetime | None) -> str:
        return "EDT"


class _NullOffsetZone(tzinfo):
    """Synthetic timezone reporting None offsets to exercise defensive fallback."""

    def utcoffset(self, dt: datetime | None) -> None:
        return None

    def dst(self, dt: datetime | None) -> None:
        return None

    def tzname(self, dt: datetime | None) -> None:
        return None


def test_compute_standard_offset_handles_northern_southern_and_utc_zones() -> None:
    year_round_zone = cast(ZoneInfo, _YearRoundDstZone())
    null_zone = cast(ZoneInfo, _NullOffsetZone())

    assert compute_standard_offset(ZoneInfo("America/Chicago"), 2026) == timedelta(hours=-6)
    assert compute_standard_offset(ZoneInfo("Australia/Sydney"), 2026) == timedelta(hours=10)
    assert compute_standard_offset(ZoneInfo("UTC"), 2026) == timedelta(0)
    assert compute_standard_offset(year_round_zone, 2026) == timedelta(hours=-5)
    assert compute_standard_offset(null_zone, 2026) == timedelta(0)

    with pytest.raises(ValueError, match="reference_year"):
        compute_standard_offset(ZoneInfo("UTC"), 0)


def test_format_frame_local_timestamp_renders_zone_abbreviation() -> None:
    winter_utc = datetime(2026, 3, 7, 18, 0, 0, tzinfo=UTC)
    summer_utc = datetime(2026, 3, 8, 17, 0, 0, tzinfo=UTC)

    winter_label = format_frame_local_timestamp(winter_utc, "America/Chicago")
    summer_label = format_frame_local_timestamp(summer_utc, "America/Chicago")
    assert winter_label == "2026-03-07 12:00:00 CST"
    assert summer_label == "2026-03-08 12:00:00 CDT"


def test_mode1_continuous_inclusive_bounds_and_microsecond_exclusion() -> None:
    start = datetime(2026, 5, 1, 18, 0, 0, tzinfo=UTC)
    end = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)

    frames = [
        FrameRecord(id=1, captured_at_utc=start - timedelta(microseconds=1)),
        FrameRecord(id=2, captured_at_utc=start),
        FrameRecord(id=3, captured_at_utc=datetime(2026, 5, 2, 9, 15, 0, tzinfo=UTC)),
        FrameRecord(id=4, captured_at_utc=end),
        FrameRecord(id=5, captured_at_utc=end + timedelta(microseconds=1)),
    ]

    selected = select_continuous_frames(frames, start_timestamp=start, end_timestamp=end)
    assert [f.id for f in selected] == [2, 3, 4]


def test_mode1_continuous_sorts_unsorted_inputs_and_handles_cross_timezone_queries() -> None:
    ny_tz = ZoneInfo("America/New_York")
    la_tz = ZoneInfo("America/Los_Angeles")

    # 2026-06-10 08:00 EDT == 12:00 UTC; 2026-06-10 07:00 PDT == 14:00 UTC
    start_ny = datetime(2026, 6, 10, 8, 0, 0, tzinfo=ny_tz)
    end_la = datetime(2026, 6, 10, 7, 0, 0, tzinfo=la_tz)

    same_instant = datetime(2026, 6, 10, 13, 0, 0, tzinfo=UTC)
    frames = [
        _UnorderedOrmLikeFrame(30, datetime(2026, 6, 10, 14, 0, 0, tzinfo=UTC)),
        _UnorderedOrmLikeFrame(22, same_instant),
        _UnorderedOrmLikeFrame(10, datetime(2026, 6, 10, 8, 0, 0, tzinfo=ny_tz)),
        _UnorderedOrmLikeFrame(21, same_instant),
        _UnorderedOrmLikeFrame(99, datetime(2026, 6, 10, 14, 0, 1, tzinfo=UTC)),
    ]

    selected = select_continuous_frames(frames, start_timestamp=start_ny, end_timestamp=end_la)
    assert [f.id for f in selected] == [10, 21, 22, 30]


def test_mode1_continuous_validation_errors() -> None:
    t0 = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    t1 = datetime(2026, 5, 1, 13, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="greater than or equal"):
        select_continuous_frames([], start_timestamp=t1, end_timestamp=t0)

    with pytest.raises(ValueError, match="Timestamp must be timezone-aware"):
        select_continuous_frames(
            [],
            start_timestamp=datetime(2026, 5, 1, 12, 0),
            end_timestamp=t1,
        )

    with pytest.raises(ValueError, match="Timestamp must be timezone-aware"):
        select_continuous_frames(
            [FrameRecord(id=1, captured_at_utc=datetime(2026, 5, 1, 12, 30))],
            start_timestamp=t0,
            end_timestamp=t1,
        )


def test_mode2_daily_slice_selects_one_closest_frame_per_local_day_inclusive() -> None:
    tz = ZoneInfo("America/Chicago")
    # Include late-evening local captures (23:30 CST == next-day 05:30 UTC) to verify
    # day bucketing uses the camera's local IANA date rather than UTC date.
    frames = [
        FrameRecord(
            id=1,
            captured_at_utc=datetime(2026, 1, 9, 23, 30, tzinfo=tz).astimezone(UTC),
        ),
        FrameRecord(
            id=2,
            captured_at_utc=datetime(2026, 1, 10, 23, 22, tzinfo=tz).astimezone(UTC),
        ),
        FrameRecord(
            id=3,
            captured_at_utc=datetime(2026, 1, 10, 23, 33, tzinfo=tz).astimezone(UTC),
        ),
        FrameRecord(
            id=4,
            captured_at_utc=datetime(2026, 1, 11, 23, 29, tzinfo=tz).astimezone(UTC),
        ),
        FrameRecord(
            id=5,
            captured_at_utc=datetime(2026, 1, 12, 23, 31, tzinfo=tz).astimezone(UTC),
        ),
        FrameRecord(
            id=6,
            captured_at_utc=datetime(2026, 1, 13, 23, 30, tzinfo=tz).astimezone(UTC),
        ),
    ]

    selected = select_daily_slice_frames(
        frames,
        start_date=date(2026, 1, 10),
        end_date=date(2026, 1, 12),
        target_time=time(23, 30, tzinfo=UTC),
        camera_timezone="America/Chicago",
        tolerance_minutes=15,
    )
    assert [f.id for f in selected] == [3, 4, 5]


def test_mode2_tolerance_boundary_precision_and_zero_tolerance() -> None:
    just_before_window = datetime(2026, 2, 4, 11, 45, 0, tzinfo=UTC) - timedelta(microseconds=1)
    frames = [
        # Day 1: exact -15m 0s included
        FrameRecord(id=1, captured_at_utc=datetime(2026, 2, 1, 11, 45, 0, tzinfo=UTC)),
        # Day 2: exact +15m 0s included
        FrameRecord(id=2, captured_at_utc=datetime(2026, 2, 2, 12, 15, 0, tzinfo=UTC)),
        # Day 3: +15m 1s excluded
        FrameRecord(id=3, captured_at_utc=datetime(2026, 2, 3, 12, 15, 1, tzinfo=UTC)),
        # Day 4: -15m 0s 1us excluded
        FrameRecord(id=4, captured_at_utc=just_before_window),
        # Day 5: exact 12:00:00 for zero-tolerance check
        FrameRecord(id=5, captured_at_utc=datetime(2026, 2, 5, 12, 0, 0, tzinfo=UTC)),
        FrameRecord(id=6, captured_at_utc=datetime(2026, 2, 5, 12, 0, 1, tzinfo=UTC)),
    ]

    selected_15m = select_daily_slice_frames(
        frames,
        start_date=date(2026, 2, 1),
        end_date=date(2026, 2, 5),
        target_time=time(12, 0, 0),
        camera_timezone="UTC",
        tolerance_minutes=15,
    )
    assert [f.id for f in selected_15m] == [1, 2, 5]

    selected_exact = select_daily_slice_frames(
        frames,
        start_date=date(2026, 2, 1),
        end_date=date(2026, 2, 5),
        target_time=time(12, 0, 0),
        camera_timezone="UTC",
        tolerance_minutes=0,
    )
    assert [f.id for f in selected_exact] == [5]


@pytest.mark.parametrize("dst_mode", ["wall_clock", "standard_offset"])
def test_mode2_equidistant_tie_breaker_always_prefers_earlier_capture(dst_mode: DstMode) -> None:
    earlier = FrameRecord(id=99, captured_at_utc=datetime(2026, 4, 10, 16, 50, 0, tzinfo=UTC))
    later = FrameRecord(id=1, captured_at_utc=datetime(2026, 4, 10, 17, 10, 0, tzinfo=UTC))
    target = time(12, 0) if dst_mode == "wall_clock" else time(11, 0)

    forward_result = select_daily_slice_frames(
        [earlier, later],
        start_date=date(2026, 4, 10),
        end_date=date(2026, 4, 10),
        target_time=target,
        camera_timezone="America/Chicago",
        tolerance_minutes=15,
        dst_mode=dst_mode,
    )
    reverse_result = select_daily_slice_frames(
        [later, earlier],
        start_date=date(2026, 4, 10),
        end_date=date(2026, 4, 10),
        target_time=target,
        camera_timezone="America/Chicago",
        tolerance_minutes=15,
        dst_mode=dst_mode,
    )

    assert [f.id for f in forward_result] == [99]
    assert [f.id for f in reverse_result] == [99]


def test_mode2_missing_days_are_skipped_without_shifting_adjacent_days() -> None:
    frames = [
        FrameRecord(id=10, captured_at_utc=datetime(2026, 2, 10, 12, 3, tzinfo=UTC)),
        # Feb 11 has no captures at all.
        # Feb 12 only has an out-of-tolerance capture (+25m).
        FrameRecord(id=12, captured_at_utc=datetime(2026, 2, 12, 12, 25, tzinfo=UTC)),
        FrameRecord(id=13, captured_at_utc=datetime(2026, 2, 13, 11, 56, tzinfo=UTC)),
    ]

    selected = select_daily_slice_frames(
        frames,
        start_date=date(2026, 2, 10),
        end_date=date(2026, 2, 14),
        target_time=time(12, 0),
        camera_timezone="UTC",
        tolerance_minutes=15,
    )
    assert [f.id for f in selected] == [10, 13]

    empty_frames: list[FrameRecord] = []
    empty_selected = select_daily_slice_frames(
        empty_frames,
        start_date=date(2026, 2, 10),
        end_date=date(2026, 2, 14),
        target_time=time(12, 0),
        camera_timezone="UTC",
    )
    assert empty_selected == []


def test_mode2_dst_spring_forward_jan_to_jun_noon_wall_clock_vs_standard_offset() -> None:
    # America/Chicago springs forward on 2026-03-08 (CST UTC-6 -> CDT UTC-5).
    # Populate every day from 2026-01-01 through 2026-06-01 (152 days) with:
    #   - 17:00 UTC (11:00 CST in winter, 12:00 CDT after spring-forward)
    #   - 18:00 UTC (12:00 CST in winter, 13:00 CDT after spring-forward)
    start_day = date(2026, 1, 1)
    end_day = date(2026, 6, 1)
    total_days = (end_day - start_day).days + 1
    assert total_days == 152

    frames: list[FrameRecord] = []
    next_id = 1
    for offset in range(total_days):
        day = start_day + timedelta(days=offset)
        frames.append(
            FrameRecord(
                id=next_id,
                captured_at_utc=datetime(day.year, day.month, day.day, 17, 0, tzinfo=UTC),
            )
        )
        next_id += 1
        frames.append(
            FrameRecord(
                id=next_id,
                captured_at_utc=datetime(day.year, day.month, day.day, 18, 0, tzinfo=UTC),
            )
        )
        next_id += 1

    wall_selected = select_daily_slice_frames(
        frames,
        start_date=start_day,
        end_date=end_day,
        target_time=time(12, 0),
        camera_timezone="America/Chicago",
        tolerance_minutes=15,
        dst_mode="wall_clock",
    )
    std_selected = select_daily_slice_frames(
        frames,
        start_date=start_day,
        end_date=end_day,
        target_time=time(12, 0),
        camera_timezone="America/Chicago",
        tolerance_minutes=15,
        dst_mode="standard_offset",
    )

    assert len(wall_selected) == 152
    assert len(std_selected) == 152

    for frame in wall_selected:
        frame_day = frame.captured_at_utc.date()
        if frame_day < date(2026, 3, 8):
            assert frame.captured_at_utc.hour == 18
        else:
            assert frame.captured_at_utc.hour == 17

    # In standard_offset mode, every single day across DST stays locked to 18:00 UTC (12:00 CST)
    # so outdoor sun position never jumps by an hour on March 8.
    assert all(frame.captured_at_utc.hour == 18 for frame in std_selected)


def test_mode2_dst_spring_forward_nonexistent_hour_wall_clock_behavior() -> None:
    # On 2026-03-08 in America/Chicago, 02:00..02:59:59 does not exist:
    #   07:55 UTC == 01:55 CST
    #   08:05 UTC == 03:05 CDT
    spring_day = date(2026, 3, 8)
    before_gap = FrameRecord(id=1, captured_at_utc=datetime(2026, 3, 8, 7, 55, tzinfo=UTC))
    after_gap = FrameRecord(id=2, captured_at_utc=datetime(2026, 3, 8, 8, 5, tzinfo=UTC))
    frames = [after_gap, before_gap]

    # 02:30 -> local wall-clock distances are 35m to 01:55 and 35m to 03:05 -> skipped at +/-15m
    skipped = select_daily_slice_frames(
        frames,
        start_date=spring_day,
        end_date=spring_day,
        target_time=time(2, 30),
        camera_timezone="America/Chicago",
        tolerance_minutes=15,
        dst_mode="wall_clock",
    )
    assert skipped == []

    # 02:05 -> 01:55 CST is 10 wall-clock minutes away (<= 15m) -> matched
    matched_before = select_daily_slice_frames(
        frames,
        start_date=spring_day,
        end_date=spring_day,
        target_time=time(2, 5),
        camera_timezone="America/Chicago",
        tolerance_minutes=15,
        dst_mode="wall_clock",
    )
    assert [f.id for f in matched_before] == [1]

    # 02:50 -> 03:05 CDT is 15 wall-clock minutes away (<= 15m) -> matched
    matched_after = select_daily_slice_frames(
        frames,
        start_date=spring_day,
        end_date=spring_day,
        target_time=time(2, 50),
        camera_timezone="America/Chicago",
        tolerance_minutes=15,
        dst_mode="wall_clock",
    )
    assert [f.id for f in matched_after] == [2]

    # At +/-40m around 02:30, both 01:55 and 03:05 are 35m away -> earlier capture wins
    tie_broken = select_daily_slice_frames(
        frames,
        start_date=spring_day,
        end_date=spring_day,
        target_time=time(2, 30),
        camera_timezone="America/Chicago",
        tolerance_minutes=40,
        dst_mode="wall_clock",
    )
    assert [f.id for f in tie_broken] == [1]


def test_mode2_dst_fall_back_repeated_hour_prefers_first_occurrence_with_fallback() -> None:
    # On 2026-11-01 in America/Chicago, 01:30 occurs twice:
    #   fold=0 (first occurrence, CDT UTC-5): 06:30 UTC
    #   fold=1 (second occurrence, CST UTC-6): 07:30 UTC
    fall_day = date(2026, 11, 1)
    first_occurrence = FrameRecord(
        id=10,
        captured_at_utc=datetime(2026, 11, 1, 6, 30, tzinfo=UTC),
    )
    second_occurrence = FrameRecord(
        id=20,
        captured_at_utc=datetime(2026, 11, 1, 7, 30, tzinfo=UTC),
    )

    both_present = select_daily_slice_frames(
        [second_occurrence, first_occurrence],
        start_date=fall_day,
        end_date=fall_day,
        target_time=time(1, 30),
        camera_timezone="America/Chicago",
        tolerance_minutes=15,
        dst_mode="wall_clock",
    )
    assert [f.id for f in both_present] == [10]

    # Even if fold=0 capture is +10m off (06:40 UTC) and fold=1 is exact (07:30 UTC),
    # the first occurrence window is still preferred.
    first_slightly_off = FrameRecord(
        id=11,
        captured_at_utc=datetime(2026, 11, 1, 6, 40, tzinfo=UTC),
    )
    preferred_first = select_daily_slice_frames(
        [second_occurrence, first_slightly_off],
        start_date=fall_day,
        end_date=fall_day,
        target_time=time(1, 30),
        camera_timezone="America/Chicago",
        tolerance_minutes=15,
        dst_mode="wall_clock",
    )
    assert [f.id for f in preferred_first] == [11]

    # When camera only captured during the second occurrence (fold=1), fall back to fold=1
    # rather than dropping the day.
    only_second = select_daily_slice_frames(
        [second_occurrence],
        start_date=fall_day,
        end_date=fall_day,
        target_time=time(1, 30),
        camera_timezone="America/Chicago",
        tolerance_minutes=15,
        dst_mode="wall_clock",
    )
    assert [f.id for f in only_second] == [20]


def test_mode2_southern_hemisphere_sydney_dst_transition() -> None:
    # Australia/Sydney is AEDT (UTC+11) in January and transitions to AEST (UTC+10, standard)
    # on 2026-04-05. Standard offset is +10:00 (winter in June), NOT January's +11:00.
    sydney_tz = ZoneInfo("Australia/Sydney")
    assert compute_standard_offset(sydney_tz, 2026) == timedelta(hours=10)

    # For 12:00 target:
    #   - 2026-04-04 (AEDT +11): wall_clock 12:00 == 01:00 UTC; standard_offset 12:00 == 02:00 UTC
    #   - 2026-04-06 (AEST +10): wall_clock 12:00 == 02:00 UTC; standard_offset 12:00 == 02:00 UTC
    frames = [
        FrameRecord(id=1, captured_at_utc=datetime(2026, 4, 4, 1, 0, tzinfo=UTC)),
        FrameRecord(id=2, captured_at_utc=datetime(2026, 4, 4, 2, 0, tzinfo=UTC)),
        FrameRecord(id=3, captured_at_utc=datetime(2026, 4, 6, 1, 0, tzinfo=UTC)),
        FrameRecord(id=4, captured_at_utc=datetime(2026, 4, 6, 2, 0, tzinfo=UTC)),
    ]

    wall = select_daily_slice_frames(
        frames,
        start_date=date(2026, 4, 4),
        end_date=date(2026, 4, 6),
        target_time=time(12, 0),
        camera_timezone="Australia/Sydney",
        tolerance_minutes=15,
        dst_mode="wall_clock",
    )
    std = select_daily_slice_frames(
        frames,
        start_date=date(2026, 4, 4),
        end_date=date(2026, 4, 6),
        target_time=time(12, 0),
        camera_timezone="Australia/Sydney",
        tolerance_minutes=15,
        dst_mode="standard_offset",
    )

    assert [f.id for f in wall] == [1, 4]
    assert [f.id for f in std] == [2, 4]


def test_mode2_validation_errors() -> None:
    with pytest.raises(ValueError, match="greater than or equal"):
        select_daily_slice_frames(
            [],
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 1),
            target_time=time(12, 0),
            camera_timezone="UTC",
        )

    with pytest.raises(ValueError, match="non-negative"):
        select_daily_slice_frames(
            [],
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 2),
            target_time=time(12, 0),
            camera_timezone="UTC",
            tolerance_minutes=-1,
        )

    with pytest.raises(ValueError, match="Unsupported dst_mode"):
        select_daily_slice_frames(
            [],
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 2),
            target_time=time(12, 0),
            camera_timezone="UTC",
            dst_mode=cast(DstMode, "solar_noon"),
        )

    with pytest.raises(ValueError, match="Invalid IANA timezone"):
        select_daily_slice_frames(
            [],
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 2),
            target_time=time(12, 0),
            camera_timezone="-06:00",
        )

    with pytest.raises(ValueError, match="Timestamp must be timezone-aware"):
        select_daily_slice_frames(
            [FrameRecord(id=1, captured_at_utc=datetime(2026, 3, 1, 12, 0))],
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 1),
            target_time=time(12, 0),
            camera_timezone="UTC",
        )

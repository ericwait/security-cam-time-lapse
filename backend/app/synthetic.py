"""Deterministic full-resolution synthetic security-camera frame generator."""

import math
from contextlib import suppress
from datetime import UTC, datetime, timedelta, timezone
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageDraw, ImageFont

DEJAVU_MONO_BOLD_PATH: Final[Path] = Path(
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
)
OBSERVER_LATITUDE_DEG: Final[float] = 40.0

type RGBColor = tuple[int, int, int]
type SkyPalette = tuple[RGBColor, RGBColor, RGBColor]

NIGHT_PALETTE: Final[SkyPalette] = ((7, 11, 26), (14, 22, 48), (24, 36, 68))
TWILIGHT_PALETTE: Final[SkyPalette] = ((24, 38, 86), (92, 66, 118), (232, 124, 72))
GOLDEN_PALETTE: Final[SkyPalette] = ((36, 86, 170), (108, 162, 218), (246, 190, 114))
MIDDAY_PALETTE: Final[SkyPalette] = ((26, 104, 202), (86, 164, 236), (186, 222, 250))


def _clamp_byte(value: float) -> int:
    return max(0, min(255, round(value)))


def _lerp(a: float, b: float, t: float) -> float:
    clamped = max(0.0, min(1.0, t))
    return a + (b - a) * clamped


def _lerp_color(c1: RGBColor, c2: RGBColor, t: float) -> RGBColor:
    clamped = max(0.0, min(1.0, t))
    return (
        _clamp_byte(c1[0] + (c2[0] - c1[0]) * clamped),
        _clamp_byte(c1[1] + (c2[1] - c1[1]) * clamped),
        _clamp_byte(c1[2] + (c2[2] - c1[2]) * clamped),
    )


def _lerp_palette(p1: SkyPalette, p2: SkyPalette, t: float) -> SkyPalette:
    return (
        _lerp_color(p1[0], p2[0], t),
        _lerp_color(p1[1], p2[1], t),
        _lerp_color(p1[2], p2[2], t),
    )


@lru_cache(maxsize=8)
def _load_hud_font(pixel_size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load DejaVuSansMono-Bold when present on disk, falling back cleanly."""
    if DEJAVU_MONO_BOLD_PATH.is_file():
        with suppress(OSError):
            return ImageFont.truetype(str(DEJAVU_MONO_BOLD_PATH), size=pixel_size)
    try:
        return ImageFont.load_default(size=pixel_size)
    except TypeError:
        return ImageFont.load_default()


def _resolve_standard_offset(zone: ZoneInfo, year: int, reference_local: datetime) -> timedelta:
    """Resolve a timezone's standard-time UTC offset (dst() == 0) for solar geometry."""
    for probe_month in (1, 7, 4, 10):
        probe_local = datetime(year, probe_month, 15, 12, 0, tzinfo=UTC).astimezone(zone)
        dst_delta = probe_local.dst() or timedelta(0)
        utc_offset = probe_local.utcoffset()
        if dst_delta == timedelta(0) and utc_offset is not None:
            return utc_offset

    fallback_offset = reference_local.utcoffset() or timedelta(0)
    fallback_dst = reference_local.dst() or timedelta(0)
    return fallback_offset - fallback_dst


def _compute_sky_palette(sun_alt_deg: float) -> SkyPalette:
    """Interpolate zenith, mid-sky, and horizon colors from solar altitude."""
    if sun_alt_deg <= -12.0:
        return NIGHT_PALETTE
    if sun_alt_deg <= -2.0:
        return _lerp_palette(NIGHT_PALETTE, TWILIGHT_PALETTE, (sun_alt_deg + 12.0) / 10.0)
    if sun_alt_deg <= 8.0:
        return _lerp_palette(TWILIGHT_PALETTE, GOLDEN_PALETTE, (sun_alt_deg + 2.0) / 10.0)
    if sun_alt_deg <= 22.0:
        return _lerp_palette(GOLDEN_PALETTE, MIDDAY_PALETTE, (sun_alt_deg - 8.0) / 14.0)

    summer_boost = min(1.0, (sun_alt_deg - 22.0) / 52.0)
    bright_zenith = _lerp_color(MIDDAY_PALETTE[0], (42, 128, 224), summer_boost)
    bright_mid = _lerp_color(MIDDAY_PALETTE[1], (112, 184, 246), summer_boost)
    bright_horizon = _lerp_color(MIDDAY_PALETTE[2], (212, 236, 255), summer_boost)
    return (bright_zenith, bright_mid, bright_horizon)


def _render_sky_backdrop(
    width: int,
    height: int,
    horizon_y: int,
    palette: SkyPalette,
) -> Image.Image:
    """Render a smooth 2D sky backdrop by stretching a 1px vertical gradient strip in C."""
    zenith, mid_sky, horizon = palette
    below_horizon = _lerp_color(horizon, (18, 24, 34), 0.65)
    strip_bytes = bytearray(height * 3)
    mid_y = max(1, int(horizon_y * 0.52))
    denom_top = max(1, mid_y)
    denom_low = max(1, horizon_y - mid_y)

    for y in range(height):
        if y <= mid_y:
            color = _lerp_color(zenith, mid_sky, (y / denom_top) ** 0.9)
        elif y <= horizon_y:
            color = _lerp_color(mid_sky, horizon, ((y - mid_y) / denom_low) ** 0.85)
        else:
            color = below_horizon
        idx = y * 3
        strip_bytes[idx] = color[0]
        strip_bytes[idx + 1] = color[1]
        strip_bytes[idx + 2] = color[2]

    strip_img = Image.frombytes("RGB", (1, height), bytes(strip_bytes))
    return strip_img.resize((width, height), Image.Resampling.BILINEAR)


def _draw_celestial_and_clouds(
    draw: ImageDraw.ImageDraw,
    *,
    width: int,
    height: int,
    horizon_y: int,
    solar_hour: float,
    day_of_year: int,
    sun_alt_deg: float,
    palette: SkyPalette,
) -> None:
    """Draw stars, moon or sun along a seasonal arc, and deterministic drifting clouds."""
    scale = width / 2560.0

    # Stars fade in smoothly during nautical/civil twilight.
    if sun_alt_deg < -1.5:
        star_visibility = min(1.0, (-1.5 - sun_alt_deg) / 10.5)
        drift = (solar_hour / 24.0) + (day_of_year / 365.25)
        for idx in range(56):
            rx = ((idx * 0.61803398875) + drift * 0.18) % 1.0
            ry = (((idx * 0.38196601125) + 0.07 * (idx % 5)) % 0.88) * 0.92
            sx = int(rx * width)
            sy = int((0.06 + ry * 0.82) * horizon_y)
            twinkle = 0.65 + 0.35 * math.sin(idx * 2.1 + solar_hour * 1.4)
            star_color = _lerp_color(palette[0], (240, 246, 255), star_visibility * twinkle)
            radius = max(1, round((1.2 + (idx % 3) * 0.7) * scale))
            draw.ellipse((sx - radius, sy - radius, sx + radius, sy + radius), fill=star_color)

    # Moon rises across the nocturnal arc when the sun is below the horizon.
    if sun_alt_deg < 0.0:
        lunar_hour = (solar_hour + 12.0) % 24.0
        lunar_arc = math.cos(math.radians((lunar_hour - 12.0) * 15.0))
        if lunar_arc > -0.1:
            moon_x = int((0.5 + (lunar_hour - 12.0) / 16.0) * width)
            moon_alt_norm = max(0.0, lunar_arc)
            moon_y = horizon_y - int((0.10 + 0.58 * moon_alt_norm) * horizon_y)
            moon_r = max(10, round(26 * scale))
            glow_r = round(moon_r * 1.8)
            moon_vis = min(1.0, -sun_alt_deg / 8.0)
            glow_col = _lerp_color(palette[1], (150, 175, 215), 0.35 * moon_vis)
            disc_col = _lerp_color(palette[1], (232, 238, 248), 0.92 * moon_vis)
            draw.ellipse(
                (moon_x - glow_r, moon_y - glow_r, moon_x + glow_r, moon_y + glow_r),
                fill=glow_col,
            )
            draw.ellipse(
                (moon_x - moon_r, moon_y - moon_r, moon_x + moon_r, moon_y + moon_r),
                fill=disc_col,
            )

    # Sun follows standard-time horizontal azimuth and seasonal solar altitude.
    if sun_alt_deg > -3.0:
        sun_x = int((0.5 + (solar_hour - 12.0) / 16.0) * width)
        sun_y = horizon_y - int((sun_alt_deg / 82.0) * (height * 0.49))
        sun_r = max(14, round(36 * scale))
        warmth = max(0.0, min(1.0, (18.0 - sun_alt_deg) / 20.0))
        core_color = _lerp_color((255, 250, 228), (255, 186, 98), warmth)
        halo_outer = _lerp_color(palette[1], (255, 214, 148), 0.38)
        halo_inner = _lerp_color(palette[1], (255, 234, 184), 0.68)
        outer_r = round(sun_r * 2.9)
        inner_r = round(sun_r * 1.75)
        draw.ellipse(
            (sun_x - outer_r, sun_y - outer_r, sun_x + outer_r, sun_y + outer_r),
            fill=halo_outer,
        )
        draw.ellipse(
            (sun_x - inner_r, sun_y - inner_r, sun_x + inner_r, sun_y + inner_r),
            fill=halo_inner,
        )
        draw.ellipse(
            (sun_x - sun_r, sun_y - sun_r, sun_x + sun_r, sun_y + sun_r),
            fill=core_color,
        )

    # Deterministic drifting clouds parameterized by day-of-year and solar hour.
    daylight = max(0.0, min(1.0, (sun_alt_deg + 8.0) / 26.0))
    twilight_tint = max(0.0, 1.0 - abs(sun_alt_deg - 1.5) / 9.0)
    cloud_day = _lerp_color((36, 48, 76), (242, 246, 252), daylight)
    cloud_fill = _lerp_color(cloud_day, (244, 164, 118), 0.55 * twilight_tint)
    cloud_shadow = _lerp_color(cloud_fill, palette[1], 0.35)

    for c_idx in range(4):
        cx_norm = ((c_idx * 0.27) + day_of_year * 0.071 + solar_hour * 0.035) % 1.16 - 0.08
        cy_norm = 0.22 + (c_idx % 3) * 0.14 + 0.03 * math.sin(day_of_year * 0.2 + c_idx)
        cx = int(cx_norm * width)
        cy = int(cy_norm * horizon_y)
        cw = round((150 + 38 * (c_idx % 3)) * scale)
        ch = round((28 + 8 * (c_idx % 2)) * scale)
        draw.ellipse((cx - cw, cy - ch // 2, cx + cw, cy + ch), fill=cloud_shadow)
        draw.ellipse(
            (cx - int(cw * 0.78), cy - ch, cx + int(cw * 0.72), cy + int(ch * 0.65)),
            fill=cloud_fill,
        )
        draw.ellipse(
            (cx - int(cw * 0.35), cy - int(ch * 1.25), cx + int(cw * 0.45), cy + int(ch * 0.5)),
            fill=cloud_fill,
        )


def _seasonal_factors(day_of_year: int) -> tuple[float, float, float]:
    """Return (snow_cover, spring_bud, lush_green) weights in [0, 1] for day_of_year."""
    if day_of_year <= 50:
        snow = 1.0
    elif day_of_year <= 82:
        snow = max(0.0, 1.0 - (day_of_year - 50) / 32.0)
    elif day_of_year >= 340:
        snow = min(1.0, (day_of_year - 340) / 25.0)
    else:
        snow = 0.0

    bud = max(0.0, 1.0 - abs(day_of_year - 92.0) / 34.0)

    if day_of_year < 70 or day_of_year > 320:
        lush = 0.0
    elif day_of_year <= 135:
        lush = (day_of_year - 70) / 65.0
    elif day_of_year <= 245:
        lush = 1.0
    else:
        lush = max(0.0, 1.0 - (day_of_year - 245) / 75.0)

    return (snow, bud, lush)


def _draw_courtyard_and_horizon(
    draw: ImageDraw.ImageDraw,
    *,
    width: int,
    height: int,
    horizon_y: int,
    day_of_year: int,
    sun_alt_deg: float,
) -> None:
    """Draw mountain ridgeline, seasonal courtyard grounds/trees, and stone perspective grid."""
    scale = width / 2560.0
    ambient = max(0.14, min(1.0, (sun_alt_deg + 10.0) / 42.0))
    snow, bud, lush = _seasonal_factors(day_of_year)

    # Distant mountain ridgeline.
    ridge_base = _lerp_color((58, 76, 108), (198, 212, 230), 0.45 * snow)
    ridge_lit = _lerp_color((16, 22, 36), ridge_base, ambient)
    ridge_points: list[tuple[int, int]] = [(0, horizon_y)]
    segments = 16
    for i in range(segments + 1):
        px = int((i / segments) * width)
        wave = (
            0.055 * math.sin(i * 0.75 + 0.4)
            + 0.028 * math.cos(i * 1.6 - 0.8)
            + 0.018 * math.sin(i * 2.7)
        )
        py = horizon_y - int((0.065 + max(-0.04, wave)) * height)
        ridge_points.append((px, py))
    ridge_points.append((width, horizon_y))
    draw.polygon(ridge_points, fill=ridge_lit)

    # Mid-ground seasonal hills.
    hill_summer = _lerp_color((64, 92, 62), (48, 128, 64), lush)
    hill_season = _lerp_color(hill_summer, (214, 224, 236), 0.75 * snow)
    hill_lit = _lerp_color((14, 20, 28), hill_season, ambient)
    foothill_points: list[tuple[int, int]] = [(0, horizon_y + 4)]
    for i in range(segments + 1):
        px = int((i / segments) * width)
        py = horizon_y - int((0.024 + 0.018 * math.cos(i * 0.9 + 1.2)) * height)
        foothill_points.append((px, py))
    foothill_points.append((width, horizon_y + 4))
    draw.polygon(foothill_points, fill=hill_lit)

    # Courtyard flanking lawn / snow beds below horizon.
    ground_green = _lerp_color((88, 96, 74), (54, 138, 66), max(lush, 0.45 * bud))
    ground_season = _lerp_color(ground_green, (226, 234, 244), 0.85 * snow)
    ground_lit = _lerp_color((15, 22, 26), ground_season, ambient)
    draw.rectangle((0, horizon_y, width, height), fill=ground_lit)

    # Central paved stone courtyard trapezoid with perspective grid.
    plaza_top_half = int(width * 0.14)
    plaza_bot_half = int(width * 0.46)
    center_x = width // 2
    stone_base = _lerp_color((142, 146, 154), (176, 184, 196), 0.25 * snow)
    stone_lit = _lerp_color((28, 32, 42), stone_base, ambient)
    curb_lit = _lerp_color((20, 24, 32), (112, 118, 128), ambient)

    plaza_poly = [
        (center_x - plaza_top_half, horizon_y),
        (center_x + plaza_top_half, horizon_y),
        (center_x + plaza_bot_half, height),
        (center_x - plaza_bot_half, height),
    ]
    draw.polygon(plaza_poly, fill=stone_lit)

    joint_color = _lerp_color((18, 22, 30), (116, 122, 132), ambient)
    joint_w = max(1, round(2 * scale))
    for lane in range(-4, 5):
        frac = lane / 4.0
        top_x = center_x + int(plaza_top_half * frac)
        bot_x = center_x + int(plaza_bot_half * frac)
        draw.line(((top_x, horizon_y), (bot_x, height)), fill=joint_color, width=joint_w)

    ground_span = height - horizon_y
    for row in range(1, 10):
        depth = (row / 10.0) ** 1.65
        ry = horizon_y + int(ground_span * depth)
        half_w = int(_lerp(plaza_top_half, plaza_bot_half, depth))
        draw.line(
            ((center_x - half_w, ry), (center_x + half_w, ry)),
            fill=joint_color,
            width=joint_w,
        )

    # Low architectural courtyard wall along the horizon edge.
    wall_h = max(6, round(18 * scale))
    wall_x1 = center_x - plaza_top_half - int(width * 0.12)
    wall_x2 = center_x + plaza_top_half + int(width * 0.12)
    draw.rectangle((wall_x1, horizon_y - wall_h, wall_x2, horizon_y + 2), fill=curb_lit)

    # Warm courtyard bollard lights glow softly at night/twilight.
    if sun_alt_deg < 4.0:
        lamp_strength = min(1.0, (4.0 - sun_alt_deg) / 12.0)
        pool_col = _lerp_color(stone_lit, (238, 192, 118), 0.42 * lamp_strength)
        bulb_col = _lerp_color(stone_lit, (255, 232, 176), 0.92 * lamp_strength)
        for side in (-1, 1):
            for depth_frac in (0.28, 0.62):
                ly = horizon_y + int(ground_span * depth_frac)
                lx = center_x + side * int(_lerp(plaza_top_half, plaza_bot_half, depth_frac))
                prx = round(42 * scale * (0.6 + depth_frac))
                pry = round(16 * scale * (0.6 + depth_frac))
                draw.ellipse((lx - prx, ly - pry, lx + prx, ly + pry), fill=pool_col)
                post_h = round(34 * scale * (0.65 + depth_frac))
                post_w = max(2, round(5 * scale))
                draw.rectangle((lx - post_w, ly - post_h, lx + post_w, ly), fill=curb_lit)
                bulb_r = max(3, round(7 * scale))
                draw.ellipse(
                    (lx - bulb_r, ly - post_h - bulb_r, lx + bulb_r, ly - post_h + bulb_r),
                    fill=bulb_col,
                )

    # Seasonal courtyard trees flanking left and right lawns.
    trunk_col = _lerp_color((22, 18, 16), (86, 62, 46), ambient)
    canopy_bud = (146, 198, 98)
    canopy_lush = (42, 134, 58)
    canopy_autumn = (188, 118, 52)
    autumn_weight = max(0.0, 1.0 - abs(day_of_year - 280.0) / 36.0)
    foliage_base = _lerp_color(canopy_bud, canopy_lush, min(1.0, lush * 1.2))
    foliage_base = _lerp_color(foliage_base, canopy_autumn, autumn_weight)
    foliage_lit = _lerp_color((16, 28, 22), foliage_base, ambient)
    snow_cap_lit = _lerp_color((42, 52, 68), (238, 244, 252), ambient)

    tree_specs = (
        (0.12, 0.38, 1.15),
        (0.24, 0.18, 0.85),
        (0.76, 0.18, 0.85),
        (0.88, 0.38, 1.15),
    )
    for x_frac, depth_frac, size_mult in tree_specs:
        tx = int(width * x_frac)
        base_y = horizon_y + int(ground_span * depth_frac)
        trunk_h = round(140 * scale * size_mult)
        trunk_w = max(3, round(10 * scale * size_mult))
        crown_y = base_y - trunk_h
        draw.rectangle((tx - trunk_w, crown_y, tx + trunk_w, base_y), fill=trunk_col)

        branch_len = round(54 * scale * size_mult)
        branch_w = max(2, round(4 * scale * size_mult))
        branch_origin = (tx, crown_y + round(18 * scale))
        for b_dx, b_dy in ((-1.0, -0.75), (1.0, -0.75), (-0.65, -1.15), (0.65, -1.15)):
            end_x = tx + round(branch_len * b_dx)
            end_y = crown_y + round(branch_len * b_dy * 0.6)
            draw.line((branch_origin, (end_x, end_y)), fill=trunk_col, width=branch_w)
            if snow > 0.25:
                sr = max(3, round(12 * scale * size_mult * snow))
                draw.ellipse((end_x - sr, end_y - sr, end_x + sr, end_y + sr), fill=snow_cap_lit)

        canopy_density = max(0.35 * bud, lush, 0.55 * autumn_weight)
        if canopy_density > 0.08:
            cr = round((34 + 46 * canopy_density) * scale * size_mult)
            offsets = ((0, -int(cr * 0.45)), (-int(cr * 0.62), 0), (int(cr * 0.62), 0))
            for ox, oy in offsets:
                draw.ellipse(
                    (tx + ox - cr, crown_y + oy - cr, tx + ox + cr, crown_y + oy + cr),
                    fill=foliage_lit,
                )


def _draw_hud_overlay(
    draw: ImageDraw.ImageDraw,
    *,
    width: int,
    height: int,
    camera_name: str,
    local_dt: datetime,
    utc_dt: datetime,
    day_of_year: int,
    sun_alt_deg: float,
) -> None:
    """Render top and bottom high-contrast security-camera HUD bars and season gauge."""
    bar_h = max(28, round(height * 0.042))
    font_px = max(13, round(bar_h * 0.54))
    font = _load_hud_font(font_px)
    pad_x = max(12, round(width * 0.015))
    pad_y = max(4, (bar_h - font_px) // 2 - 1)

    hud_bg: RGBColor = (12, 16, 24)
    hud_border: RGBColor = (46, 62, 84)
    text_primary: RGBColor = (242, 247, 252)
    text_muted: RGBColor = (176, 194, 214)

    # Top bar: camera label (left) and local + UTC timestamps (right).
    draw.rectangle((0, 0, width, bar_h), fill=hud_bg)
    draw.line(((0, bar_h), (width, bar_h)), fill=hud_border, width=1)

    left_label = f"CAM: {camera_name}"
    tz_label = local_dt.tzname() or "LOCAL"
    right_label = (
        f"{local_dt.strftime('%Y-%m-%d %H:%M:%S')} {tz_label}  UTC {utc_dt.strftime('%H:%M:%S')}Z"
    )
    draw.text((pad_x, pad_y), left_label, fill=text_primary, font=font)
    right_bbox = draw.textbbox((0, 0), right_label, font=font)
    right_w = right_bbox[2] - right_bbox[0]
    right_x = max(pad_x, width - pad_x - right_w)
    draw.text((right_x, pad_y), right_label, fill=text_primary, font=font)

    # Bottom bar: solar elevation telemetry + seasonal progress bar.
    bot_top = height - bar_h
    draw.rectangle((0, bot_top, width, height), fill=hud_bg)
    draw.line(((0, bot_top), (width, bot_top)), fill=hud_border, width=1)

    telemetry = f"DAY {day_of_year:03d}/365  SOLAR {sun_alt_deg:+05.1f}\u00b0"
    draw.text((pad_x, bot_top + pad_y), telemetry, fill=text_muted, font=font)

    gauge_w = max(80, round(width * 0.16))
    gauge_h = max(8, round(bar_h * 0.28))
    gauge_x1 = width - pad_x - gauge_w
    gauge_y1 = bot_top + (bar_h - gauge_h) // 2
    gauge_x2 = width - pad_x
    gauge_y2 = gauge_y1 + gauge_h
    draw.rectangle((gauge_x1, gauge_y1, gauge_x2, gauge_y2), outline=hud_border, fill=(24, 32, 46))
    progress_frac = max(0.0, min(1.0, day_of_year / 365.0))
    fill_w = max(2, round((gauge_w - 2) * progress_frac))
    draw.rectangle(
        (gauge_x1 + 1, gauge_y1 + 1, gauge_x1 + 1 + fill_w, gauge_y2 - 1),
        fill=(84, 188, 126),
    )


def render_synthetic_frame(
    *,
    camera_name: str,
    captured_at_utc: datetime,
    camera_timezone: str,
    width: int = 2560,
    height: int = 1440,
    jpeg_quality: int = 88,
) -> bytes:
    """Render a deterministic full-resolution JPEG synthetic camera frame."""
    if captured_at_utc.tzinfo is None or captured_at_utc.utcoffset() is None:
        raise ValueError("captured_at_utc must be a timezone-aware datetime.")
    if not camera_timezone or not camera_timezone.strip():
        raise ValueError("camera_timezone must be a non-empty IANA timezone name.")
    try:
        zone = ZoneInfo(camera_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown IANA timezone: {camera_timezone!r}") from exc

    if width < 64 or height < 64:
        raise ValueError("Synthetic frame dimensions must be at least 64x64.")
    if jpeg_quality < 1 or jpeg_quality > 100:
        raise ValueError("jpeg_quality must be between 1 and 100.")

    utc_dt = captured_at_utc.astimezone(UTC)
    local_dt = utc_dt.astimezone(zone)
    std_offset = _resolve_standard_offset(zone, utc_dt.year, local_dt)
    solar_dt = utc_dt.astimezone(timezone(std_offset))

    day_of_year = solar_dt.timetuple().tm_yday
    solar_hour = (
        solar_dt.hour
        + solar_dt.minute / 60.0
        + solar_dt.second / 3600.0
        + solar_dt.microsecond / 3_600_000_000.0
    )

    declination_deg = -23.44 * math.cos(2.0 * math.pi * (day_of_year + 10) / 365.25)
    declination_rad = math.radians(declination_deg)
    lat_rad = math.radians(OBSERVER_LATITUDE_DEG)
    hour_angle_rad = math.radians((solar_hour - 12.0) * 15.0)
    sin_alt = math.sin(lat_rad) * math.sin(declination_rad) + math.cos(lat_rad) * math.cos(
        declination_rad
    ) * math.cos(hour_angle_rad)
    sun_alt_deg = math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))

    horizon_y = int(height * 0.62)
    palette = _compute_sky_palette(sun_alt_deg)
    image = _render_sky_backdrop(width, height, horizon_y, palette)
    draw = ImageDraw.Draw(image)

    _draw_celestial_and_clouds(
        draw,
        width=width,
        height=height,
        horizon_y=horizon_y,
        solar_hour=solar_hour,
        day_of_year=day_of_year,
        sun_alt_deg=sun_alt_deg,
        palette=palette,
    )
    _draw_courtyard_and_horizon(
        draw,
        width=width,
        height=height,
        horizon_y=horizon_y,
        day_of_year=day_of_year,
        sun_alt_deg=sun_alt_deg,
    )
    _draw_hud_overlay(
        draw,
        width=width,
        height=height,
        camera_name=camera_name,
        local_dt=local_dt,
        utc_dt=utc_dt,
        day_of_year=day_of_year,
        sun_alt_deg=sun_alt_deg,
    )

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=jpeg_quality)
    return buffer.getvalue()

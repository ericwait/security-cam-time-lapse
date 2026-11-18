import type { CameraViewer, DstReferenceMode, TimelapseMode } from "./types";

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes) || bytes <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const decimals = unitIndex === 0 ? 0 : value >= 100 ? 0 : 1;
  return `${value.toFixed(decimals)} ${units[unitIndex]}`;
}

export function formatTimestamp(isoString: string | null | undefined): string {
  if (!isoString) {
    return "Never";
  }
  const parsed = new Date(isoString);
  if (Number.isNaN(parsed.getTime())) {
    return isoString;
  }
  return parsed.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export function formatDateInTimeZone(isoString: string, timeZone: string): string {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) {
    return "2026-01-01";
  }
  try {
    const formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
    const parts = formatter.formatToParts(date);
    const year = parts.find((p) => p.type === "year")?.value ?? String(date.getUTCFullYear());
    const month = parts.find((p) => p.type === "month")?.value ?? pad2(date.getUTCMonth() + 1);
    const day = parts.find((p) => p.type === "day")?.value ?? pad2(date.getUTCDate());
    return `${year}-${month}-${day}`;
  } catch {
    return `${date.getUTCFullYear()}-${pad2(date.getUTCMonth() + 1)}-${pad2(date.getUTCDate())}`;
  }
}

export function formatForDateTimeLocal(date: Date): string {
  const year = date.getFullYear();
  const month = pad2(date.getMonth() + 1);
  const day = pad2(date.getDate());
  const hours = pad2(date.getHours());
  const minutes = pad2(date.getMinutes());
  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

function floorToMinute(date: Date): Date {
  const copy = new Date(date.getTime());
  copy.setSeconds(0, 0);
  return copy;
}

function ceilToMinute(date: Date): Date {
  const copy = new Date(date.getTime());
  if (copy.getSeconds() > 0 || copy.getMilliseconds() > 0) {
    copy.setMinutes(copy.getMinutes() + 1, 0, 0);
  }
  return copy;
}

export function normalizeTargetTime(raw: string): string {
  const trimmed = raw.trim();
  if (/^\d{2}:\d{2}$/.test(trimmed)) {
    return `${trimmed}:00`;
  }
  if (/^\d{2}:\d{2}:\d{2}$/.test(trimmed)) {
    return trimmed;
  }
  return "12:00:00";
}

export interface InitialQueryDraft {
  mode: TimelapseMode;
  startDate: string;
  endDate: string;
  targetTime: string;
  toleranceMinutes: number;
  dstMode: DstReferenceMode;
  startDateTimeLocal: string;
  endDateTimeLocal: string;
}

export function deriveInitialQueryDraft(camera: CameraViewer): InitialQueryDraft {
  if (camera.earliest_capture_at && camera.latest_capture_at) {
    const earliest = new Date(camera.earliest_capture_at);
    const latest = new Date(camera.latest_capture_at);
    if (!Number.isNaN(earliest.getTime()) && !Number.isNaN(latest.getTime())) {
      const startDate = formatDateInTimeZone(camera.earliest_capture_at, camera.timezone);
      const endDate = formatDateInTimeZone(camera.latest_capture_at, camera.timezone);
      const spansMultipleDays = startDate !== endDate;

      const startFloor = floorToMinute(earliest);
      let endCeil = ceilToMinute(latest);
      if (endCeil.getTime() <= startFloor.getTime()) {
        endCeil = new Date(startFloor.getTime() + 60_000);
      }

      return {
        mode: spansMultipleDays ? "daily_slice" : "continuous",
        startDate,
        endDate,
        targetTime: "12:00",
        toleranceMinutes: 15,
        dstMode: "wall_clock",
        startDateTimeLocal: formatForDateTimeLocal(startFloor),
        endDateTimeLocal: formatForDateTimeLocal(endCeil),
      };
    }
  }

  const now = new Date();
  const weekAgo = new Date(now.getTime() - 7 * 86_400_000);
  const dayAgo = new Date(now.getTime() - 86_400_000);
  return {
    mode: "daily_slice",
    startDate: formatDateInTimeZone(weekAgo.toISOString(), camera.timezone),
    endDate: formatDateInTimeZone(now.toISOString(), camera.timezone),
    targetTime: "12:00",
    toleranceMinutes: 15,
    dstMode: "wall_clock",
    startDateTimeLocal: formatForDateTimeLocal(floorToMinute(dayAgo)),
    endDateTimeLocal: formatForDateTimeLocal(ceilToMinute(now)),
  };
}

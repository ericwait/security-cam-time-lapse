export interface HealthPayload {
  status: string;
  version: string;
  database: string;
  journal_mode?: string;
}

export type UserRole = "admin" | "viewer";

export interface User {
  id: number;
  username: string;
  role: UserRole;
  is_active: boolean;
  created_at: string;
}

export interface AuthStatusPayload {
  needs_bootstrap: boolean;
  user: User | null;
}

export interface CameraViewer {
  id: number;
  name: string;
  slug: string;
  timezone: string;
  interval_seconds: number;
  frame_count: number;
  earliest_capture_at: string | null;
  latest_capture_at: string | null;
}

export type TimelapseMode = "continuous" | "daily_slice";
export type DstReferenceMode = "wall_clock" | "standard_offset";

export interface ContinuousQueryPayload {
  camera_id: number;
  mode: "continuous";
  start_timestamp: string;
  end_timestamp: string;
}

export interface DailySliceQueryPayload {
  camera_id: number;
  mode: "daily_slice";
  start_date: string;
  end_date: string;
  target_time: string;
  tolerance_minutes: number;
  dst_mode: DstReferenceMode;
}

export type TimelapseQueryPayload = ContinuousQueryPayload | DailySliceQueryPayload;

export interface FrameItem {
  id: number;
  camera_id: number;
  captured_at_utc: string;
  local_timestamp: string;
  local_date: string;
  width: number;
  height: number;
  size_bytes: number;
  preview_url: string;
  original_url: string;
  download_url: string;
}

export interface TimelapseQueryResponse {
  camera_id: number;
  camera_name: string;
  camera_timezone: string;
  mode: TimelapseMode;
  frame_count: number;
  frames: FrameItem[];
}

export type ExportFps = 10 | 24 | 30 | 60;
export type ResolutionPreset = "native" | "4k" | "1080p";
export type RenderJobStatus = "queued" | "running" | "completed" | "failed" | "expired";

export interface CreateExportPayload {
  query: TimelapseQueryPayload;
  fps: ExportFps;
  resolution_preset: ResolutionPreset;
  watermark_timestamp: boolean;
}

export interface RenderJob {
  id: string;
  camera_id: number;
  mode: TimelapseMode;
  fps: ExportFps;
  resolution_preset: ResolutionPreset;
  watermark_timestamp: boolean;
  status: RenderJobStatus;
  frame_count: number;
  processed_frames: number;
  output_size_bytes: number | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  expires_at: string | null;
  download_url: string | null;
}

export type CameraSourceType = "rtsp" | "http" | "synthetic";
export type CameraAuthType = "none" | "basic" | "digest" | "bearer";

export interface CameraAdmin {
  id: number;
  name: string;
  slug: string;
  source_type: CameraSourceType;
  source_url: string;
  auth_type: CameraAuthType;
  auth_username: string | null;
  has_credentials: boolean;
  timezone: string;
  interval_seconds: number;
  is_enabled: boolean;
  frame_count: number;
  last_capture_at: string | null;
  last_capture_status: string | null;
  last_capture_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface CameraUpsertPayload {
  name: string;
  slug?: string | null;
  source_type: CameraSourceType;
  source_url: string;
  auth_type: CameraAuthType;
  auth_username?: string | null;
  secret?: string | null;
  timezone: string;
  interval_seconds: number;
  is_enabled: boolean;
}

export type CameraPatchPayload = Partial<CameraUpsertPayload>;

export interface CameraTestConnectionPayload {
  camera_id?: number | null;
  name?: string;
  source_type: CameraSourceType;
  source_url: string;
  auth_type: CameraAuthType;
  auth_username?: string | null;
  secret?: string | null;
  timezone?: string;
}

export interface CameraTestCaptureResult {
  ok: boolean;
  width: number;
  height: number;
  preview_data_url: string;
}

export interface CreateUserPayload {
  username: string;
  password: string;
  role: UserRole;
}

export interface UpdateUserPayload {
  role?: UserRole;
  is_active?: boolean;
  password?: string;
}

export interface SystemSettings {
  default_timezone: string;
  render_cache_ttl_hours: number;
  default_tolerance_minutes: number;
  photos_dir: string;
  previews_dir: string;
  renders_dir: string;
  migrated_files_count?: number;
}

export interface UpdateSystemSettingsPayload {
  default_timezone?: string;
  render_cache_ttl_hours?: number;
  default_tolerance_minutes?: number;
  photos_dir?: string;
  previews_dir?: string;
  renders_dir?: string;
}

export interface StorageStats {
  disk_total_bytes: number;
  disk_used_bytes: number;
  disk_free_bytes: number;
  photos_bytes: number;
  previews_bytes: number;
  renders_bytes: number;
  total_frames: number;
  active_renders: number;
  photos_dir: string;
  previews_dir: string;
  renders_dir: string;
}

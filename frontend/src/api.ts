import type {
  AuthStatusPayload,
  CameraAdmin,
  CameraPatchPayload,
  CameraTestCaptureResult,
  CameraTestConnectionPayload,
  CameraUpsertPayload,
  CameraViewer,
  CreateExportPayload,
  CreateUserPayload,
  FrameItem,
  HealthPayload,
  RenderJob,
  StorageStats,
  SystemSettings,
  TimelapseQueryPayload,
  TimelapseQueryResponse,
  UpdateUserPayload,
  User,
} from "./types";

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

interface FastApiValidationItem {
  msg?: string;
}

interface FastApiErrorBody {
  detail?: string | FastApiValidationItem[];
}

async function extractErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const raw = await response.text();
    if (!raw) {
      return fallback;
    }
    const parsed = JSON.parse(raw) as FastApiErrorBody;
    if (typeof parsed.detail === "string" && parsed.detail.trim().length > 0) {
      return parsed.detail;
    }
    if (Array.isArray(parsed.detail) && parsed.detail.length > 0) {
      const messages = parsed.detail
        .map((item) => item.msg)
        .filter((msg): msg is string => typeof msg === "string" && msg.length > 0);
      if (messages.length > 0) {
        return messages.join("; ");
      }
    }
  } catch {
    // Ignore parse errors and return fallback status message.
  }
  return fallback;
}

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (init?.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: {
      ...headers,
      ...(init?.headers as Record<string, string> | undefined),
    },
  });

  if (!response.ok) {
    const fallback = `Request to ${path} failed with HTTP ${response.status}`;
    const message = await extractErrorMessage(response, fallback);
    throw new ApiError(message, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  if (!text) {
    return undefined as T;
  }
  return JSON.parse(text) as T;
}

export async function fetchHealth(): Promise<HealthPayload> {
  const response = await fetch("/api/health", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new ApiError(`Health check returned HTTP ${response.status}`, response.status);
  }
  return (await response.json()) as HealthPayload;
}

export async function fetchAuthStatus(): Promise<AuthStatusPayload> {
  return apiRequest<AuthStatusPayload>("/api/auth/status");
}

export async function bootstrapAdmin(username: string, password: string): Promise<User> {
  return apiRequest<User>("/api/auth/bootstrap", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export async function login(username: string, password: string): Promise<User> {
  return apiRequest<User>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export async function logout(): Promise<{ status: string }> {
  return apiRequest<{ status: string }>("/api/auth/logout", {
    method: "POST",
  });
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<{ status: string }> {
  return apiRequest<{ status: string }>("/api/auth/password", {
    method: "POST",
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
}

export async function fetchViewerCameras(): Promise<CameraViewer[]> {
  return apiRequest<CameraViewer[]>("/api/cameras");
}

export async function queryTimelapse(
  payload: TimelapseQueryPayload,
): Promise<TimelapseQueryResponse> {
  return apiRequest<TimelapseQueryResponse>("/api/timelapse/query", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function createExport(payload: CreateExportPayload): Promise<RenderJob> {
  return apiRequest<RenderJob>("/api/exports", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function fetchExportJob(jobId: string): Promise<RenderJob> {
  return apiRequest<RenderJob>(`/api/exports/${encodeURIComponent(jobId)}`);
}

export async function fetchAdminCameras(): Promise<CameraAdmin[]> {
  return apiRequest<CameraAdmin[]>("/api/admin/cameras");
}

export async function createAdminCamera(payload: CameraUpsertPayload): Promise<CameraAdmin> {
  return apiRequest<CameraAdmin>("/api/admin/cameras", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateAdminCamera(
  id: number,
  payload: CameraPatchPayload,
): Promise<CameraAdmin> {
  return apiRequest<CameraAdmin>(`/api/admin/cameras/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteAdminCamera(id: number): Promise<void> {
  return apiRequest<void>(`/api/admin/cameras/${id}`, {
    method: "DELETE",
  });
}

export async function testCameraConnection(
  payload: CameraTestConnectionPayload,
): Promise<CameraTestCaptureResult> {
  return apiRequest<CameraTestCaptureResult>("/api/admin/cameras/test-connection", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function testExistingCameraCapture(id: number): Promise<CameraTestCaptureResult> {
  return apiRequest<CameraTestCaptureResult>(`/api/admin/cameras/${id}/test-capture`, {
    method: "POST",
  });
}

export async function captureCameraNow(id: number): Promise<FrameItem> {
  return apiRequest<FrameItem>(`/api/admin/cameras/${id}/capture-now`, {
    method: "POST",
  });
}

export async function fetchAdminUsers(): Promise<User[]> {
  return apiRequest<User[]>("/api/admin/users");
}

export async function createAdminUser(payload: CreateUserPayload): Promise<User> {
  return apiRequest<User>("/api/admin/users", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateAdminUser(id: number, payload: UpdateUserPayload): Promise<User> {
  return apiRequest<User>(`/api/admin/users/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteAdminUser(id: number): Promise<void> {
  return apiRequest<void>(`/api/admin/users/${id}`, {
    method: "DELETE",
  });
}

export async function fetchAdminSettings(): Promise<SystemSettings> {
  return apiRequest<SystemSettings>("/api/admin/settings");
}

export async function updateAdminSettings(payload: SystemSettings): Promise<SystemSettings> {
  return apiRequest<SystemSettings>("/api/admin/settings", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function fetchAdminStorage(): Promise<StorageStats> {
  return apiRequest<StorageStats>("/api/admin/storage");
}

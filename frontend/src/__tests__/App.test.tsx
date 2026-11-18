import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type {
  CameraAdmin,
  CameraViewer,
  HealthPayload,
  RenderJob,
  TimelapseQueryResponse,
  User,
} from "../types";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const healthyPayload: HealthPayload = {
  status: "ok",
  version: "0.1.0-alpha.4",
  database: "ok",
  journal_mode: "wal",
};

const sampleViewerCameras: CameraViewer[] = [
  {
    id: 1,
    name: "Garden South",
    slug: "garden-south",
    timezone: "America/Chicago",
    interval_seconds: 900,
    frame_count: 120,
    earliest_capture_at: "2026-02-15T18:00:00Z",
    latest_capture_at: "2026-04-15T17:00:00Z",
  },
];

const sampleQueryResponse: TimelapseQueryResponse = {
  camera_id: 1,
  camera_name: "Garden South",
  camera_timezone: "America/Chicago",
  mode: "daily_slice",
  frame_count: 2,
  frames: [
    {
      id: 101,
      camera_id: 1,
      captured_at_utc: "2026-03-07T18:00:00Z",
      local_timestamp: "2026-03-07 12:00:00 CST",
      local_date: "2026-03-07",
      width: 2560,
      height: 1440,
      size_bytes: 1_240_000,
      preview_url: "/api/frames/101/preview",
      original_url: "/api/frames/101/original",
      download_url: "/api/frames/101/download",
    },
    {
      id: 102,
      camera_id: 1,
      captured_at_utc: "2026-03-08T17:00:00Z",
      local_timestamp: "2026-03-08 12:00:00 CDT",
      local_date: "2026-03-08",
      width: 2560,
      height: 1440,
      size_bytes: 1_310_000,
      preview_url: "/api/frames/102/preview",
      original_url: "/api/frames/102/original",
      download_url: "/api/frames/102/download",
    },
  ],
};

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === "string") {
    return input;
  }
  if (input instanceof URL) {
    return input.pathname;
  }
  return input.url;
}

describe("Security Cam Time-Lapse Web UI", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("completes the first-run bootstrap admin setup flow when needs_bootstrap is true", async () => {
    const adminUser: User = {
      id: 1,
      username: "admin",
      role: "admin",
      is_active: true,
      created_at: "2026-11-18T12:00:00Z",
    };

    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = requestPath(input);
        const method = init?.method ?? "GET";

        if (path === "/api/health") {
          return jsonResponse(healthyPayload);
        }
        if (path === "/api/auth/status") {
          return jsonResponse({ needs_bootstrap: true, user: null });
        }
        if (path === "/api/auth/bootstrap" && method === "POST") {
          const parsed = JSON.parse(String(init?.body)) as { username: string; password: string };
          expect(parsed.username).toBe("admin");
          expect(parsed.password).toBe("CorrectHorseBattery9!");
          return jsonResponse(adminUser, 201);
        }
        if (path === "/api/cameras") {
          return jsonResponse(sampleViewerCameras);
        }
        if (path === "/api/timelapse/query" && method === "POST") {
          return jsonResponse(sampleQueryResponse);
        }
        throw new Error(`Unhandled request: ${method} ${path}`);
      });

    render(<App />);

    expect(
      screen.getByRole("heading", { level: 1, name: "Security Cam Time-Lapse" }),
    ).toBeInTheDocument();

    const healthStrip = screen.getByTestId("system-health");
    await waitFor(() => {
      expect(within(healthStrip).getByText("Healthy")).toBeInTheDocument();
    });
    expect(within(healthStrip).getByText("0.1.0-alpha.4")).toBeInTheDocument();
    expect(within(healthStrip).getByText("SQLite WAL")).toBeInTheDocument();

    const bootstrapForm = await screen.findByTestId("bootstrap-form");
    const usernameInput = within(bootstrapForm).getByLabelText("Username") as HTMLInputElement;
    expect(usernameInput.value).toBe("admin");

    fireEvent.change(within(bootstrapForm).getByLabelText("Password"), {
      target: { value: "CorrectHorseBattery9!" },
    });
    fireEvent.change(within(bootstrapForm).getByLabelText("Confirm password"), {
      target: { value: "CorrectHorseBattery9!" },
    });
    fireEvent.submit(bootstrapForm);

    await waitFor(() => {
      expect(screen.getByTestId("nav-player")).toBeInTheDocument();
    });
    expect(screen.getByTestId("nav-admin")).toBeInTheDocument();
    expect(await screen.findByTestId("sequence-player")).toBeInTheDocument();
    expect(fetchSpy).toHaveBeenCalled();
  });

  it("signs in as Viewer, auto-loads playable frames, and hides Admin Console navigation", async () => {
    const viewerUser: User = {
      id: 2,
      username: "alex",
      role: "viewer",
      is_active: true,
      created_at: "2026-11-18T12:05:00Z",
    };

    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = requestPath(input);
        const method = init?.method ?? "GET";

        if (path === "/api/health") {
          return jsonResponse(healthyPayload);
        }
        if (path === "/api/auth/status") {
          return jsonResponse({ needs_bootstrap: false, user: null });
        }
        if (path === "/api/auth/login" && method === "POST") {
          return jsonResponse(viewerUser);
        }
        if (path === "/api/cameras") {
          return jsonResponse(sampleViewerCameras);
        }
        if (path === "/api/timelapse/query" && method === "POST") {
          const payload = JSON.parse(String(init?.body)) as {
            mode: string;
            target_time: string;
            dst_mode: string;
          };
          expect(payload.mode).toBe("daily_slice");
          expect(payload.target_time).toBe("12:00:00");
          expect(payload.dst_mode).toBe("wall_clock");
          return jsonResponse(sampleQueryResponse);
        }
        throw new Error(`Unhandled request: ${method} ${path}`);
      },
    );

    render(<App />);

    const loginForm = await screen.findByTestId("login-form");
    fireEvent.change(within(loginForm).getByLabelText("Username"), {
      target: { value: "alex" },
    });
    fireEvent.change(within(loginForm).getByLabelText("Password"), {
      target: { value: "ViewerSecretPass123!" },
    });
    fireEvent.submit(loginForm);

    await waitFor(() => {
      expect(screen.getByTestId("nav-player")).toBeInTheDocument();
    });

    // Role boundary check: Viewers must never see Admin Console navigation.
    expect(screen.queryByTestId("nav-admin")).not.toBeInTheDocument();

    // Instant sequence player loads frames automatically without extra clicks.
    const readout = await screen.findByTestId("frame-readout");
    await waitFor(() => {
      expect(readout).toHaveTextContent("Frame 1 of 2 — 2026-03-07 12:00:00 CST (2560×1440)");
    });

    const playerSection = screen.getByTestId("sequence-player");
    const displayedImg = within(playerSection).getByRole("img") as HTMLImageElement;
    expect(displayedImg.getAttribute("src")).toBe("/api/frames/101/preview");

    // Toggling Inspect Full-Res Original switches to the untouched original frame URL.
    fireEvent.click(screen.getByTestId("toggle-fullres"));
    expect(displayedImg.getAttribute("src")).toBe("/api/frames/101/original");

    const stillDownload = screen.getByTestId("download-still-link");
    expect(stillDownload).toHaveAttribute("href", "/api/frames/101/download");
  });

  it("allows Admin to switch Mode 2 DST mode to standard_offset, export MP4, and open Admin Console", async () => {
    const adminUser: User = {
      id: 1,
      username: "ops-admin",
      role: "admin",
      is_active: true,
      created_at: "2026-11-18T12:00:00Z",
    };

    const adminCameras: CameraAdmin[] = [
      {
        id: 1,
        name: "Garden South",
        slug: "garden-south",
        source_type: "synthetic",
        source_url: "synthetic://garden-south",
        auth_type: "none",
        auth_username: null,
        has_credentials: false,
        timezone: "America/Chicago",
        interval_seconds: 900,
        is_enabled: true,
        frame_count: 120,
        last_capture_at: "2026-04-15T17:00:00Z",
        last_capture_status: "ok",
        last_capture_error: null,
        created_at: "2026-02-15T18:00:00Z",
        updated_at: "2026-04-15T17:00:00Z",
      },
    ];

    let exportPollCount = 0;
    let capturedExportBody: Record<string, unknown> | null = null;

    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = requestPath(input);
        const method = init?.method ?? "GET";

        if (path === "/api/health") {
          return jsonResponse(healthyPayload);
        }
        if (path === "/api/auth/status") {
          return jsonResponse({ needs_bootstrap: false, user: adminUser });
        }
        if (path === "/api/cameras") {
          return jsonResponse(sampleViewerCameras);
        }
        if (path === "/api/timelapse/query" && method === "POST") {
          return jsonResponse(sampleQueryResponse);
        }
        if (path === "/api/exports" && method === "POST") {
          capturedExportBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
          const queuedJob: RenderJob = {
            id: "job-uuid-1234",
            camera_id: 1,
            mode: "daily_slice",
            fps: 24,
            resolution_preset: "native",
            watermark_timestamp: true,
            status: "running",
            frame_count: 2,
            processed_frames: 1,
            output_size_bytes: null,
            error_message: null,
            created_at: "2026-11-18T12:10:00Z",
            completed_at: null,
            expires_at: "2026-11-19T12:10:00Z",
            download_url: null,
          };
          return jsonResponse(queuedJob, 202);
        }
        if (path === "/api/exports/job-uuid-1234") {
          exportPollCount += 1;
          const completedJob: RenderJob = {
            id: "job-uuid-1234",
            camera_id: 1,
            mode: "daily_slice",
            fps: 24,
            resolution_preset: "native",
            watermark_timestamp: true,
            status: "completed",
            frame_count: 2,
            processed_frames: 2,
            output_size_bytes: 4_194_304,
            error_message: null,
            created_at: "2026-11-18T12:10:00Z",
            completed_at: "2026-11-18T12:10:02Z",
            expires_at: "2026-11-19T12:10:00Z",
            download_url: "/api/exports/job-uuid-1234/download",
          };
          return jsonResponse(completedJob);
        }
        if (path === "/api/admin/cameras" && method === "GET") {
          return jsonResponse(adminCameras);
        }
        throw new Error(`Unhandled request: ${method} ${path}`);
      },
    );

    render(<App />);

    // Both primary navigation tabs render for Admin.
    expect(await screen.findByTestId("nav-player")).toBeInTheDocument();
    expect(screen.getByTestId("nav-admin")).toBeInTheDocument();

    // Wait for initial query frames to load.
    await waitFor(() => {
      expect(screen.getByTestId("frame-readout")).toHaveTextContent("Frame 1 of 2");
    });

    // Switch Mode 2 DST selector to fixed standard-time offset and enable watermark.
    const dstSelect = screen.getByTestId("dst-mode-select") as HTMLSelectElement;
    fireEvent.change(dstSelect, { target: { value: "standard_offset" } });
    expect(dstSelect.value).toBe("standard_offset");

    fireEvent.click(screen.getByTestId("export-watermark-checkbox"));
    fireEvent.click(screen.getByTestId("start-export-button"));

    const downloadMp4Link = await screen.findByTestId("download-mp4-link", undefined, {
      timeout: 4000,
    });
    expect(downloadMp4Link).toHaveAttribute("href", "/api/exports/job-uuid-1234/download");
    expect(exportPollCount).toBeGreaterThanOrEqual(1);
    expect(capturedExportBody).toMatchObject({
      fps: 24,
      resolution_preset: "native",
      watermark_timestamp: true,
      query: {
        camera_id: 1,
        mode: "daily_slice",
        dst_mode: "standard_offset",
      },
    });

    // Switch into Admin Console and verify admin camera management renders.
    fireEvent.click(screen.getByTestId("nav-admin"));
    expect(await screen.findByTestId("camera-form")).toBeInTheDocument();
    expect(screen.getByTestId("admin-tab-users")).toBeInTheDocument();
    expect(screen.getByTestId("admin-tab-storage")).toBeInTheDocument();
  });

  it("renders graceful error states when /api/health or /api/auth/login fails", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = requestPath(input);
        const method = init?.method ?? "GET";

        if (path === "/api/health") {
          return jsonResponse({ detail: "Database unavailable" }, 503);
        }
        if (path === "/api/auth/status") {
          return jsonResponse({ needs_bootstrap: false, user: null });
        }
        if (path === "/api/auth/login" && method === "POST") {
          return jsonResponse({ detail: "Invalid username or password" }, 401);
        }
        throw new Error(`Unhandled request: ${method} ${path}`);
      },
    );

    render(<App />);

    const healthStrip = screen.getByTestId("system-health");
    await waitFor(() => {
      expect(within(healthStrip).getByText("Unavailable")).toBeInTheDocument();
    });
    expect(within(healthStrip).getByText("Health check returned HTTP 503")).toBeInTheDocument();

    const loginForm = await screen.findByTestId("login-form");
    fireEvent.change(within(loginForm).getByLabelText("Username"), {
      target: { value: "wrong-user" },
    });
    fireEvent.change(within(loginForm).getByLabelText("Password"), {
      target: { value: "wrong-password" },
    });
    fireEvent.submit(loginForm);

    expect(await within(loginForm).findByRole("alert")).toHaveTextContent(
      "Invalid username or password",
    );
  });

  it("supports Mode 1 continuous queries, timeline scrubbing, and Admin camera credential creation", async () => {
    const adminUser: User = {
      id: 1,
      username: "admin",
      role: "admin",
      is_active: true,
      created_at: "2026-11-18T12:00:00Z",
    };

    let savedCameraPayload: Record<string, unknown> | null = null;
    let mode1QueryCount = 0;

    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = requestPath(input);
        const method = init?.method ?? "GET";

        if (path === "/api/health") {
          return jsonResponse(healthyPayload);
        }
        if (path === "/api/auth/status") {
          return jsonResponse({ needs_bootstrap: false, user: adminUser });
        }
        if (path === "/api/cameras") {
          return jsonResponse(sampleViewerCameras);
        }
        if (path === "/api/timelapse/query" && method === "POST") {
          const parsed = JSON.parse(String(init?.body)) as { mode: string };
          if (parsed.mode === "continuous") {
            mode1QueryCount += 1;
          }
          return jsonResponse(sampleQueryResponse);
        }
        if (path === "/api/admin/cameras" && method === "GET") {
          return jsonResponse([]);
        }
        if (path === "/api/admin/cameras" && method === "POST") {
          savedCameraPayload = JSON.parse(String(init?.body)) as Record<string, unknown>;
          return jsonResponse(
            {
              id: 5,
              name: "Driveway North",
              slug: "driveway-north",
              source_type: "http",
              source_url: "http://192.0.2.25/snapshot.jpg",
              auth_type: "basic",
              auth_username: "camuser",
              has_credentials: true,
              timezone: "America/Chicago",
              interval_seconds: 600,
              is_enabled: true,
              frame_count: 0,
              last_capture_at: null,
              last_capture_status: null,
              last_capture_error: null,
              created_at: "2026-11-18T12:20:00Z",
              updated_at: "2026-11-18T12:20:00Z",
            },
            201,
          );
        }
        throw new Error(`Unhandled request: ${method} ${path}`);
      },
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId("frame-readout")).toHaveTextContent("Frame 1 of 2");
    });

    // Scrub directly to frame index 1 via timeline scrubber.
    fireEvent.change(screen.getByTestId("timeline-scrubber"), { target: { value: "1" } });
    expect(screen.getByTestId("frame-readout")).toHaveTextContent(
      "Frame 2 of 2 — 2026-03-08 12:00:00 CDT (2560×1440)",
    );

    // Switch to Mode 1 continuous query and submit.
    fireEvent.click(screen.getByTestId("mode-continuous"));
    fireEvent.click(screen.getByTestId("run-query-button"));
    await waitFor(() => {
      expect(mode1QueryCount).toBe(1);
    });

    // Navigate to Admin Console and register a camera with Basic credentials.
    fireEvent.click(screen.getByTestId("nav-admin"));
    const camForm = await screen.findByTestId("camera-form");

    fireEvent.change(within(camForm).getByLabelText("Camera Name"), {
      target: { value: "Driveway North" },
    });
    fireEvent.change(within(camForm).getByLabelText("Source Type"), {
      target: { value: "http" },
    });
    fireEvent.change(within(camForm).getByLabelText("Stream / Snapshot URL"), {
      target: { value: "http://192.0.2.25/snapshot.jpg" },
    });
    fireEvent.change(within(camForm).getByLabelText("Authentication"), {
      target: { value: "basic" },
    });
    fireEvent.change(within(camForm).getByLabelText("Camera Username"), {
      target: { value: "camuser" },
    });
    fireEvent.change(within(camForm).getByLabelText("Camera Password / Token"), {
      target: { value: "CamSecretToken42!" },
    });
    fireEvent.submit(camForm);

    await waitFor(() => {
      expect(savedCameraPayload).toMatchObject({
        name: "Driveway North",
        source_type: "http",
        source_url: "http://192.0.2.25/snapshot.jpg",
        auth_type: "basic",
        auth_username: "camuser",
        secret: "CamSecretToken42!",
      });
    });
  });
});

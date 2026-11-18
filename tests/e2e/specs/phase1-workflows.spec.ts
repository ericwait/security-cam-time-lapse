import AxeBuilder from "@axe-core/playwright";
import { type APIRequestContext, expect, type Page, test } from "@playwright/test";

const apiBaseUrl = (process.env.E2E_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const VIEWER_TEMP_CRED = "ViewerPass-TestOnly!";

function requireBootstrapAdminCredential(): string {
  const value = process.env.BOOTSTRAP_ADMIN_PASSWORD;
  if (!value || value.trim().length === 0) {
    throw new Error(
      "BOOTSTRAP_ADMIN_PASSWORD must be provided in the environment to run Phase 1 E2E tests.",
    );
  }
  return value;
}

async function waitForStackReady(request: APIRequestContext): Promise<void> {
  await expect
    .poll(
      async () => {
        try {
          const response = await request.get(`${apiBaseUrl}/api/health`, { timeout: 2_000 });
          return response.status();
        } catch {
          return 0;
        }
      },
      {
        message: `Expected ${apiBaseUrl}/api/health to respond with HTTP 200`,
        timeout: 15_000,
        intervals: [250, 500, 1_000],
      },
    )
    .toBe(200);

  await expect
    .poll(
      async () => {
        try {
          const response = await request.get("/", { timeout: 2_000 });
          return response.status();
        } catch {
          return 0;
        }
      },
      {
        message: "Expected web UI server on / to respond with HTTP 200",
        timeout: 15_000,
        intervals: [250, 500, 1_000],
      },
    )
    .toBe(200);
}

async function assertZeroAxeViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
}

test.describe.serial("Phase 1 Admin setup, Viewer playback, DST modes, and MP4 export", () => {
  let createdViewerUsername = "";
  let createdCameraName = "";

  test("Admin Console workflow and axe accessibility across Player, Cameras, Users, Storage, and Password modal", async ({
    page,
    request,
  }, testInfo) => {
    const adminCred = requireBootstrapAdminCredential();
    const runId = `${Date.now()}-${testInfo.retry}`;
    const cameraName = `East Garden E2E ${runId}`;
    const viewerUsername = `e2e-viewer-${runId}`;

    await waitForStackReady(request);
    await page.goto("/");

    const healthCard = page.getByTestId("system-health");
    await expect(healthCard.getByRole("status")).toContainText("Healthy");

    const loginForm = page.getByTestId("login-form");
    await expect(loginForm).toBeVisible();
    await loginForm.getByLabel("Username").fill("admin");
    await loginForm.getByLabel("Password").fill(adminCred);
    await loginForm.getByRole("button", { name: "Sign In" }).click();

    // Authenticated Timelapse Player view auto-loads seeded synthetic camera frames
    const sequencePlayer = page.getByTestId("sequence-player");
    await expect(sequencePlayer).toBeVisible();
    await expect(page.getByLabel("Camera")).toContainText("Front Courtyard (Synthetic)");
    await expect(page.getByTestId("frame-readout")).toContainText(/^Frame 1 of \d+ — /);
    await expect(sequencePlayer.locator("img.player-image")).toBeVisible();
    await assertZeroAxeViolations(page);

    // Admin Console -> Cameras tab
    await page.getByTestId("nav-admin").click();
    await expect(page.getByRole("heading", { level: 2, name: "Admin Console" })).toBeVisible();
    const cameraForm = page.getByTestId("camera-form");
    await expect(cameraForm).toBeVisible();
    await expect(
      page.locator(".camera-row").filter({ hasText: "Front Courtyard (Synthetic)" }),
    ).toBeVisible();
    await assertZeroAxeViolations(page);

    // Create a new synthetic camera, verify connection probe, save, and trigger Capture Now
    await cameraForm.getByLabel("Camera Name").fill(cameraName);
    await cameraForm.getByLabel("Source Type").selectOption("synthetic");
    await cameraForm.getByLabel("Stream / Snapshot URL").fill(`synthetic://east-garden-${runId}`);
    await cameraForm.getByLabel("IANA Timezone").fill("America/Chicago");
    await cameraForm.getByLabel("Capture Interval (seconds)").fill("900");

    await cameraForm.getByRole("button", { name: "Test Connection" }).click();
    await expect(cameraForm.getByAltText("Live camera connection test preview")).toBeVisible();

    await page.getByTestId("save-camera-button").click();

    const newCameraRow = page.locator(".camera-row").filter({ hasText: cameraName });
    await expect(newCameraRow).toBeVisible();
    await newCameraRow.getByRole("button", { name: "Capture Now" }).click();
    await expect(newCameraRow.locator(".status-badge--healthy")).toHaveText("ok");
    await expect(page.locator(".success-banner")).toContainText(cameraName);

    // Admin Console -> Users tab
    await page.getByTestId("admin-tab-users").click();
    const createUserForm = page.getByTestId("create-user-form");
    await expect(createUserForm).toBeVisible();
    await expect(page.getByRole("table")).toContainText("admin");
    await assertZeroAxeViolations(page);

    await createUserForm.getByLabel("Username").fill(viewerUsername);
    await createUserForm.getByLabel("Temporary Password").fill(VIEWER_TEMP_CRED);
    await createUserForm.getByLabel("Role").selectOption("viewer");
    await page.getByTestId("create-user-button").click();

    const viewerRow = page.locator("tbody tr").filter({ hasText: viewerUsername });
    await expect(viewerRow).toBeVisible();
    await expect(viewerRow).toContainText("viewer");
    await expect(viewerRow).toContainText("Active");

    createdViewerUsername = viewerUsername;
    createdCameraName = cameraName;

    // Admin Console -> Storage & Settings tab
    await page.getByTestId("admin-tab-storage").click();
    await expect(
      page.getByRole("progressbar", { name: "Host volume capacity usage" }),
    ).toBeVisible();
    const metricsGrid = page.locator(".metrics-grid");
    await expect(metricsGrid).toContainText("Disk Free / Total");
    await expect(metricsGrid).toContainText("Untouched Originals");
    await expect(metricsGrid).toContainText("WebP Previews");
    await expect(metricsGrid).toContainText("Total Frames");
    const totalFramesBox = page.locator(".metric-box").filter({ hasText: "Total Frames" });
    const rawFramesText = (await totalFramesBox.locator(".metric-value").innerText()).replace(
      /,/g,
      "",
    );
    expect(Number(rawFramesText)).toBeGreaterThanOrEqual(71);
    await assertZeroAxeViolations(page);

    // Change Password modal -> axe scan -> close -> Sign Out
    await page.getByTestId("nav-password").click();
    const passwordDialog = page.getByRole("dialog", { name: "Change Password" });
    await expect(passwordDialog).toBeVisible();
    await expect(page.getByTestId("password-form")).toBeVisible();
    await assertZeroAxeViolations(page);

    await passwordDialog.getByRole("button", { name: "Close" }).click();
    await expect(passwordDialog).toBeHidden();

    await page.getByTestId("logout-button").click();
    await expect(page.getByTestId("login-form")).toBeVisible();
  });

  test("Viewer role boundary, Mode 2 DST scrubbing, Full-Res inspection, and on-demand FFmpeg MP4 export", async ({
    page,
  }) => {
    expect(createdViewerUsername).toBeTruthy();

    await page.goto("/");
    const healthCard = page.getByTestId("system-health");
    await expect(healthCard.getByRole("status")).toContainText("Healthy");

    const loginForm = page.getByTestId("login-form");
    await expect(loginForm).toBeVisible();
    await loginForm.getByLabel("Username").fill(createdViewerUsername);
    await loginForm.getByLabel("Password").fill(VIEWER_TEMP_CRED);
    await loginForm.getByRole("button", { name: "Sign In" }).click();

    await expect(page.getByTestId("nav-player")).toBeVisible();
    await expect(page.getByTestId("sequence-player")).toBeVisible();

    // Viewer role boundary: zero Admin Console nav in UI and HTTP 403 on Admin API
    await expect(page.getByTestId("nav-admin")).toHaveCount(0);
    const forbiddenAdminRes = await page.request.get("/api/admin/users");
    expect(forbiddenAdminRes.status()).toBe(403);

    const camerasApiRes = await page.request.get("/api/cameras");
    expect(camerasApiRes.status()).toBe(200);
    const camerasList = (await camerasApiRes.json()) as Array<{ name: string }>;
    const cameraNames = camerasList.map((cam) => cam.name);
    expect(cameraNames).toContain("Front Courtyard (Synthetic)");
    expect(cameraNames).toContain(createdCameraName);

    // Mode 2 (daily_slice) across the March 2026 America/Chicago DST transition
    const frameReadout = page.getByTestId("frame-readout");
    await expect(page.getByTestId("mode-daily-slice")).toHaveAttribute("aria-pressed", "true");
    await expect(frameReadout).toContainText(/^Frame 1 of \d+ — 2026-02-25 12:00:00 CST/);

    const queryControls = page.getByTestId("query-controls");
    await queryControls.getByLabel("Start date").fill("2026-02-25");
    await queryControls.getByLabel("End date").fill("2026-03-26");
    await page.getByTestId("dst-mode-select").selectOption("standard_offset");
    await page.getByTestId("run-query-button").click();

    await expect(frameReadout).toContainText("Frame 1 of 30 — 2026-02-25 12:00:00 CST");

    // Instant client-side scrubbing across the March 8 DST spring-forward boundary (index 19 = March 16)
    const scrubber = page.getByTestId("timeline-scrubber");
    await scrubber.fill("19");
    await expect(frameReadout).toContainText("Frame 20 of 30 — 2026-03-16 13:00:00 CDT");

    // Toggle Full-Res Original inspection and verify untouched original + download link
    const playerStageImg = page.getByTestId("sequence-player").locator("img.player-image");
    await expect(playerStageImg).toHaveAttribute("src", /^\/api\/frames\/\d+\/preview$/);

    const toggleFullRes = page.getByTestId("toggle-fullres");
    await toggleFullRes.click();
    await expect(toggleFullRes).toHaveAttribute("aria-pressed", "true");
    await expect(playerStageImg).toHaveAttribute("src", /^\/api\/frames\/\d+\/original$/);

    const downloadStillLink = page.getByTestId("download-still-link");
    await expect(downloadStillLink).toBeVisible();
    const stillHref = await downloadStillLink.getAttribute("href");
    expect(stillHref).toMatch(/^\/api\/frames\/\d+\/original\?download=1$/);
    const stillResponse = await page.request.get(stillHref!);
    expect(stillResponse.status()).toBe(200);
    expect(stillResponse.headers()["content-type"]).toContain("image/jpeg");

    // Switch to Mode 1 (continuous) with a compact single-day frame window and export MP4
    await page.getByTestId("mode-continuous").click();
    await expect(page.getByTestId("mode-continuous")).toHaveAttribute("aria-pressed", "true");
    await queryControls.getByLabel("Start date & time").fill("2026-03-26T06:00");
    await queryControls.getByLabel("End date & time").fill("2026-03-26T23:59");
    await page.getByTestId("run-query-button").click();

    await expect(frameReadout).toContainText(/^Frame 1 of \d+ — 2026-03-26 /);

    const exportPanel = page.getByTestId("export-panel");
    await expect(exportPanel).toBeVisible();
    await page.getByTestId("export-fps-select").selectOption("10");
    await page.getByTestId("export-resolution-select").selectOption("1080p");
    await page.getByTestId("export-watermark-checkbox").check();
    await page.getByTestId("start-export-button").click();

    const downloadMp4Link = page.getByTestId("download-mp4-link");
    await expect(downloadMp4Link).toBeVisible({ timeout: 25_000 });

    const mp4Href = await downloadMp4Link.getAttribute("href");
    expect(mp4Href).toMatch(/^\/api\/exports\/[^/]+\/download$/);

    const mp4Response = await page.request.get(mp4Href!);
    expect(mp4Response.status()).toBe(200);
    expect(mp4Response.headers()["content-type"]).toContain("video/mp4");
    const mp4Body = await mp4Response.body();
    expect(mp4Body.byteLength).toBeGreaterThan(1_000);
    expect(mp4Body.subarray(4, 8).toString("ascii")).toBe("ftyp");

    await assertZeroAxeViolations(page);
  });
});

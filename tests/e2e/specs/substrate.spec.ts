import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

interface HealthResponse {
  status: string;
  version: string;
  database: string;
  journal_mode: string;
}

const apiBaseUrl = (process.env.E2E_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

test.describe("Unauthenticated sign-in shell & health readiness", () => {
  test("reports healthy SQLite WAL status, renders Sign-In form, and passes axe accessibility checks", async ({
    page,
    request,
  }) => {
    let healthBody: HealthResponse | undefined;

    await expect
      .poll(
        async () => {
          try {
            const response = await request.get(`${apiBaseUrl}/api/health`, { timeout: 2_000 });
            if (!response.ok()) {
              return response.status();
            }
            healthBody = (await response.json()) as HealthResponse;
            return response.status();
          } catch {
            return 0;
          }
        },
        {
          message: `Expected ${apiBaseUrl}/api/health to become ready with HTTP 200`,
          timeout: 15_000,
          intervals: [250, 500, 1_000],
        },
      )
      .toBe(200);

    expect(healthBody).toMatchObject({
      status: "ok",
      database: "ok",
      journal_mode: "wal",
    });
    expect(healthBody?.version).toBeTruthy();

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
          message: "Expected web UI server to respond on / with HTTP 200",
          timeout: 15_000,
          intervals: [250, 500, 1_000],
        },
      )
      .toBe(200);

    await page.goto("/");

    await expect(
      page.getByRole("heading", { level: 1, name: "Security Cam Time-Lapse" }),
    ).toBeVisible();

    const healthCard = page.getByTestId("system-health");
    await expect(healthCard).toBeVisible();
    await expect(healthCard.getByRole("status")).toContainText("Healthy");
    await expect(healthCard).toContainText("Online");
    await expect(healthCard).toContainText("SQLite WAL");
    await expect(healthCard.getByRole("alert")).toHaveCount(0);

    const loginForm = page.getByTestId("login-form");
    await expect(loginForm).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "Sign In" })).toBeVisible();
    await expect(loginForm.getByLabel("Username")).toBeVisible();
    await expect(loginForm.getByLabel("Password")).toBeVisible();
    await expect(loginForm.getByRole("button", { name: "Sign In" })).toBeVisible();

    const accessibilityScanResults = await new AxeBuilder({ page }).analyze();
    expect(accessibilityScanResults.violations).toEqual([]);
  });
});

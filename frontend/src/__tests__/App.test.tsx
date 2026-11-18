import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";

describe("App shell", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the heading and healthy API status from /api/health", async () => {
    const payload = {
      status: "ok",
      version: "0.1.0-alpha.4",
      database: "ok",
      journal_mode: "wal",
    };
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    render(<App />);

    const heading = screen.getByRole("heading", {
      level: 1,
      name: "Security Cam Time-Lapse",
    });
    expect(heading).toBeInTheDocument();

    const healthCard = screen.getByTestId("system-health");
    await waitFor(() => {
      expect(within(healthCard).getByText("Healthy")).toBeInTheDocument();
    });

    expect(within(healthCard).getByText("0.1.0-alpha.4")).toBeInTheDocument();
    expect(within(healthCard).getByText("SQLite WAL")).toBeInTheDocument();
    expect(fetchSpy).toHaveBeenCalledWith("/api/health", {
      headers: { Accept: "application/json" },
    });
  });

  it("renders a graceful error state when /api/health returns HTTP 503", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Database unavailable" }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      }),
    );

    render(<App />);

    const healthCard = screen.getByTestId("system-health");
    await waitFor(() => {
      expect(within(healthCard).getByText("Unavailable")).toBeInTheDocument();
    });

    expect(within(healthCard).getByText("Health check returned HTTP 503")).toBeInTheDocument();
  });
});

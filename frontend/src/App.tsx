import { useEffect, useState } from "react";

export interface HealthPayload {
  status: string;
  version: string;
  database: string;
  journal_mode?: string;
}

type HealthState =
  { kind: "loading" } | { kind: "ready"; data: HealthPayload } | { kind: "error"; message: string };

function formatDatabaseLabel(payload: HealthPayload): string {
  const mode = (payload.journal_mode ?? "wal").toUpperCase();
  if (payload.database === "ok") {
    return `SQLite ${mode}`;
  }
  return `${payload.database} (${mode})`;
}

export function App() {
  const [health, setHealth] = useState<HealthState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;

    async function fetchHealth(): Promise<void> {
      try {
        const response = await fetch("/api/health", {
          headers: { Accept: "application/json" },
        });
        if (!response.ok) {
          throw new Error(`Health check returned HTTP ${response.status}`);
        }
        const data = (await response.json()) as HealthPayload;
        if (!cancelled) {
          setHealth({ kind: "ready", data });
        }
      } catch (err) {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : "Unable to reach /api/health";
          setHealth({ kind: "error", message });
        }
      }
    }

    void fetchHealth();

    return () => {
      cancelled = true;
    };
  }, []);

  const isHealthy = health.kind === "ready" && health.data.status === "ok";
  const badgeVariant =
    health.kind === "loading"
      ? "status-badge status-badge--loading"
      : isHealthy
        ? "status-badge status-badge--healthy"
        : "status-badge status-badge--error";
  const badgeText =
    health.kind === "loading" ? "Checking API" : isHealthy ? "Healthy" : "Unavailable";
  const apiDetail = health.kind === "loading" ? "Connecting..." : isHealthy ? "Online" : "Degraded";
  const versionLabel = health.kind === "ready" ? health.data.version : "Pending";
  const dbLabel = health.kind === "ready" ? formatDatabaseLabel(health.data) : "Unverified";
  const errorMessage = health.kind === "error" ? health.message : null;

  return (
    <div className="app-shell">
      <header className="site-header">
        <div>
          <p className="eyebrow">Self-Hosted Visual History</p>
          <h1 className="site-title">Security Cam Time-Lapse</h1>
          <p className="site-subtitle">
            Full-resolution still archival and instant browser sequence playback.
          </p>
        </div>
        <span className="version-pill">Build: {versionLabel}</span>
      </header>

      <main className="site-main">
        <section className="card" data-testid="system-health" aria-labelledby="health-heading">
          <div className="card-header">
            <h2 id="health-heading" className="section-title">
              System Health
            </h2>
            <div role="status" aria-live="polite" className={badgeVariant}>
              <span className="status-dot" aria-hidden="true" />
              <span>{badgeText}</span>
            </div>
          </div>

          <dl className="metrics-grid">
            <div className="metric-box">
              <dt className="metric-label">API Service</dt>
              <dd className="metric-value">{apiDetail}</dd>
            </div>
            <div className="metric-box">
              <dt className="metric-label">Version</dt>
              <dd className="metric-value">{versionLabel}</dd>
            </div>
            <div className="metric-box">
              <dt className="metric-label">Database</dt>
              <dd className="metric-value">{dbLabel}</dd>
            </div>
          </dl>

          {errorMessage && (
            <p role="alert" className="error-banner">
              {errorMessage}
            </p>
          )}
        </section>

        <section className="card" aria-labelledby="readiness-heading">
          <div className="card-header">
            <h2 id="readiness-heading" className="section-title">
              Phase 0 Substrate Readiness
            </h2>
          </div>

          <div className="capabilities-grid">
            <article className="capability-tile">
              <h3 className="tile-title">Capture Archive</h3>
              <p className="tile-copy">
                Untouched full-resolution camera frames stored in YYYY/MM/DD folders.
              </p>
            </article>
            <article className="capability-tile">
              <h3 className="tile-title">Instant Sequence Scrubbing</h3>
              <p className="tile-copy">
                Lightweight WebP previews enable immediate browser scrubbing at any FPS.
              </p>
            </article>
            <article className="capability-tile">
              <h3 className="tile-title">Ephemeral MP4 Export</h3>
              <p className="tile-copy">
                On-demand FFmpeg H.264 rendering from native stills with TTL cleanup.
              </p>
            </article>
          </div>
        </section>
      </main>

      <footer className="site-footer">
        <p className="footer-copy">
          Phase 0 engineering substrate — FastAPI, React, TypeScript, and SQLite WAL.
        </p>
      </footer>
    </div>
  );
}

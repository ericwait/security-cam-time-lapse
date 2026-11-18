import { useCallback, useEffect, useState } from "react";
import { fetchAuthStatus, fetchHealth, logout } from "./api";
import { AccountModal } from "./components/AccountModal";
import { AdminConsole } from "./components/AdminConsole";
import { BootstrapView, LoginView } from "./components/AuthViews";
import { TimelapseStudio } from "./components/TimelapseStudio";
import type { HealthPayload, User } from "./types";

export type { HealthPayload };

type HealthState =
  { kind: "loading" } | { kind: "ready"; data: HealthPayload } | { kind: "error"; message: string };

type AuthState =
  | { kind: "loading" }
  | { kind: "ready"; needsBootstrap: boolean; user: User | null }
  | { kind: "error"; message: string };

type ActiveTab = "player" | "admin";

function formatDatabaseLabel(payload: HealthPayload): string {
  const mode = (payload.journal_mode ?? "wal").toUpperCase();
  if (payload.database === "ok") {
    return `SQLite ${mode}`;
  }
  return `${payload.database} (${mode})`;
}

export function App() {
  const [health, setHealth] = useState<HealthState>({ kind: "loading" });
  const [auth, setAuth] = useState<AuthState>({ kind: "loading" });
  const [activeTab, setActiveTab] = useState<ActiveTab>("player");
  const [isPasswordModalOpen, setIsPasswordModalOpen] = useState(false);

  const loadHealth = useCallback(async (isCancelled: () => boolean): Promise<void> => {
    try {
      const data = await fetchHealth();
      if (!isCancelled()) {
        setHealth({ kind: "ready", data });
      }
    } catch (err) {
      if (!isCancelled()) {
        const message = err instanceof Error ? err.message : "Unable to reach /api/health";
        setHealth({ kind: "error", message });
      }
    }
  }, []);

  const loadAuth = useCallback(async (isCancelled?: () => boolean): Promise<void> => {
    try {
      const status = await fetchAuthStatus();
      if (!isCancelled?.()) {
        setAuth({
          kind: "ready",
          needsBootstrap: status.needs_bootstrap,
          user: status.user,
        });
      }
    } catch (err) {
      if (!isCancelled?.()) {
        const message =
          err instanceof Error ? err.message : "Unable to verify authentication session.";
        setAuth({ kind: "error", message });
      }
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const checkCancelled = () => cancelled;

    void loadHealth(checkCancelled);
    void loadAuth(checkCancelled);

    return () => {
      cancelled = true;
    };
  }, [loadHealth, loadAuth]);

  function handleAuthenticated(user: User): void {
    setActiveTab("player");
    setAuth({ kind: "ready", needsBootstrap: false, user });
  }

  async function handleLogout(): Promise<void> {
    try {
      await logout();
    } catch {
      // Clear local session state even if network logout fails.
    }
    setIsPasswordModalOpen(false);
    setActiveTab("player");
    setAuth({ kind: "ready", needsBootstrap: false, user: null });
  }

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
  const healthErrorMessage = health.kind === "error" ? health.message : null;

  const currentUser = auth.kind === "ready" ? auth.user : null;

  return (
    <div className="app-shell">
      <header className="site-header">
        <div className="header-top-row">
          <div>
            <p className="eyebrow">Self-Hosted Visual History</p>
            <h1 className="site-title">Security Cam Time-Lapse</h1>
            <p className="site-subtitle">
              Full-resolution still archival, instant browser sequence playback, and ephemeral MP4
              exports.
            </p>
          </div>

          <div className="health-strip" data-testid="system-health">
            <div className="health-strip-row">
              <div role="status" aria-live="polite" className={badgeVariant}>
                <span className="status-dot" aria-hidden="true" />
                <span>{badgeText}</span>
              </div>
              <span className="health-chip">API: {apiDetail}</span>
              <span className="health-chip">{dbLabel}</span>
              <span className="version-pill">{versionLabel}</span>
            </div>
            {healthErrorMessage && (
              <p role="alert" className="error-banner error-banner--compact">
                {healthErrorMessage}
              </p>
            )}
          </div>
        </div>

        {currentUser && (
          <nav aria-label="Primary" className="primary-nav">
            <div className="nav-links">
              <button
                type="button"
                data-testid="nav-player"
                aria-current={activeTab === "player" ? "page" : undefined}
                className={
                  activeTab === "player" ? "btn btn--toggle btn--toggle-active" : "btn btn--toggle"
                }
                onClick={() => setActiveTab("player")}
              >
                Timelapse Player
              </button>

              {currentUser.role === "admin" && (
                <button
                  type="button"
                  data-testid="nav-admin"
                  aria-current={activeTab === "admin" ? "page" : undefined}
                  className={
                    activeTab === "admin" ? "btn btn--toggle btn--toggle-active" : "btn btn--toggle"
                  }
                  onClick={() => setActiveTab("admin")}
                >
                  Admin Console
                </button>
              )}
            </div>

            <div className="nav-account">
              <span className="user-badge">
                <span>{currentUser.username}</span>
                <span className="role-pill">{currentUser.role}</span>
              </span>

              <button
                type="button"
                data-testid="nav-password"
                className="btn btn--secondary"
                onClick={() => setIsPasswordModalOpen(true)}
              >
                Change Password
              </button>

              <button
                type="button"
                data-testid="logout-button"
                className="btn btn--secondary"
                onClick={() => {
                  void handleLogout();
                }}
              >
                Sign Out
              </button>
            </div>
          </nav>
        )}
      </header>

      <main className="site-main">
        {auth.kind === "loading" && (
          <section className="card" aria-labelledby="workspace-loading-heading">
            <h2 id="workspace-loading-heading" className="section-title">
              Loading Workspace
            </h2>
            <p className="section-copy">Verifying authentication and system state…</p>
          </section>
        )}

        {auth.kind === "error" && (
          <section className="card" aria-labelledby="workspace-error-heading">
            <div className="card-header">
              <h2 id="workspace-error-heading" className="section-title">
                Service Unavailable
              </h2>
            </div>
            <p role="alert" className="error-banner">
              {auth.message}
            </p>
            <div className="form-actions">
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => {
                  setAuth({ kind: "loading" });
                  void loadAuth();
                }}
              >
                Retry Connection
              </button>
            </div>
          </section>
        )}

        {auth.kind === "ready" && auth.needsBootstrap && (
          <BootstrapView onSuccess={handleAuthenticated} />
        )}

        {auth.kind === "ready" && !auth.needsBootstrap && auth.user === null && (
          <LoginView onSuccess={handleAuthenticated} />
        )}

        {auth.kind === "ready" &&
          !auth.needsBootstrap &&
          auth.user !== null &&
          (activeTab === "admin" && auth.user.role === "admin" ? (
            <AdminConsole currentUser={auth.user} />
          ) : (
            <TimelapseStudio />
          ))}
      </main>

      {isPasswordModalOpen && currentUser && (
        <AccountModal onClose={() => setIsPasswordModalOpen(false)} />
      )}

      <footer className="site-footer">
        <p className="footer-copy">
          Security Cam Time-Lapse — Untouched full-resolution archives, WebP sequence playback, and
          SQLite WAL.
        </p>
      </footer>
    </div>
  );
}

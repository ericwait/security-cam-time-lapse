import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  captureCameraNow,
  createAdminCamera,
  createAdminUser,
  deleteAdminCamera,
  deleteAdminUser,
  fetchAdminCameras,
  fetchAdminSettings,
  fetchAdminStorage,
  fetchAdminUsers,
  testCameraConnection,
  testExistingCameraCapture,
  updateAdminCamera,
  updateAdminSettings,
  updateAdminUser,
} from "../api";
import { formatBytes, formatTimestamp } from "../format";
import type {
  CameraAdmin,
  CameraAuthType,
  CameraPatchPayload,
  CameraSourceType,
  CameraTestCaptureResult,
  StorageStats,
  SystemSettings,
  User,
  UserRole,
} from "../types";

type AdminSubTab = "cameras" | "users" | "storage";

interface AdminConsoleProps {
  currentUser: User;
}

export function AdminConsole({ currentUser }: AdminConsoleProps) {
  const [subTab, setSubTab] = useState<AdminSubTab>("cameras");

  // Cameras state
  const [cameras, setCameras] = useState<CameraAdmin[]>([]);
  const [camerasLoading, setCamerasLoading] = useState(false);
  const [cameraBannerError, setCameraBannerError] = useState<string | null>(null);
  const [cameraBannerSuccess, setCameraBannerSuccess] = useState<string | null>(null);
  const [cameraProbes, setCameraProbes] = useState<Record<number, CameraTestCaptureResult>>({});

  // Camera form state
  const [editingCameraId, setEditingCameraId] = useState<number | null>(null);
  const [camName, setCamName] = useState("");
  const [camSourceType, setCamSourceType] = useState<CameraSourceType>("synthetic");
  const [camSourceUrl, setCamSourceUrl] = useState("synthetic://garden");
  const [camAuthType, setCamAuthType] = useState<CameraAuthType>("none");
  const [camAuthUsername, setCamAuthUsername] = useState("");
  const [camSecret, setCamSecret] = useState("");
  const [camTimezone, setCamTimezone] = useState("America/Chicago");
  const [camInterval, setCamInterval] = useState(900);
  const [camEnabled, setCamEnabled] = useState(true);
  const [camSaving, setCamSaving] = useState(false);
  const [camTestingForm, setCamTestingForm] = useState(false);
  const [formProbe, setFormProbe] = useState<CameraTestCaptureResult | null>(null);

  // Users state
  const [users, setUsers] = useState<User[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [usersSuccess, setUsersSuccess] = useState<string | null>(null);
  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState<UserRole>("viewer");
  const [creatingUser, setCreatingUser] = useState(false);

  // Storage & settings state
  const [storage, setStorage] = useState<StorageStats | null>(null);
  const [settings, setSettings] = useState<SystemSettings>({
    default_timezone: "America/Chicago",
    render_cache_ttl_hours: 24,
    default_tolerance_minutes: 15,
  });
  const [storageLoading, setStorageLoading] = useState(false);
  const [storageError, setStorageError] = useState<string | null>(null);
  const [settingsSuccess, setSettingsSuccess] = useState<string | null>(null);
  const [savingSettings, setSavingSettings] = useState(false);

  const loadCameras = useCallback(async (): Promise<void> => {
    setCamerasLoading(true);
    setCameraBannerError(null);
    try {
      const list = await fetchAdminCameras();
      setCameras(list);
    } catch (err) {
      setCameraBannerError(err instanceof Error ? err.message : "Failed to load cameras.");
    } finally {
      setCamerasLoading(false);
    }
  }, []);

  const loadUsers = useCallback(async (): Promise<void> => {
    setUsersLoading(true);
    setUsersError(null);
    try {
      const list = await fetchAdminUsers();
      setUsers(list);
    } catch (err) {
      setUsersError(err instanceof Error ? err.message : "Failed to load users.");
    } finally {
      setUsersLoading(false);
    }
  }, []);

  const loadStorageAndSettings = useCallback(async (): Promise<void> => {
    setStorageLoading(true);
    setStorageError(null);
    try {
      const [storageData, settingsData] = await Promise.all([
        fetchAdminStorage(),
        fetchAdminSettings(),
      ]);
      setStorage(storageData);
      setSettings(settingsData);
    } catch (err) {
      setStorageError(err instanceof Error ? err.message : "Failed to load storage metrics.");
    } finally {
      setStorageLoading(false);
    }
  }, []);

  useEffect(() => {
    if (subTab === "cameras") {
      void loadCameras();
    } else if (subTab === "users") {
      void loadUsers();
    } else {
      void loadStorageAndSettings();
    }
  }, [subTab, loadCameras, loadUsers, loadStorageAndSettings]);

  function resetCameraForm(): void {
    setEditingCameraId(null);
    setCamName("");
    setCamSourceType("synthetic");
    setCamSourceUrl("synthetic://garden");
    setCamAuthType("none");
    setCamAuthUsername("");
    setCamSecret("");
    setCamTimezone("America/Chicago");
    setCamInterval(900);
    setCamEnabled(true);
    setFormProbe(null);
  }

  function startEditCamera(camera: CameraAdmin): void {
    setEditingCameraId(camera.id);
    setCamName(camera.name);
    setCamSourceType(camera.source_type);
    setCamSourceUrl(camera.source_url);
    setCamAuthType(camera.auth_type);
    setCamAuthUsername(camera.auth_username ?? "");
    setCamSecret("");
    setCamTimezone(camera.timezone);
    setCamInterval(camera.interval_seconds);
    setCamEnabled(camera.is_enabled);
    setFormProbe(null);
    setCameraBannerError(null);
    setCameraBannerSuccess(null);
  }

  async function handleTestFormConnection(): Promise<void> {
    setCameraBannerError(null);
    setFormProbe(null);
    setCamTestingForm(true);
    try {
      const probe = await testCameraConnection({
        camera_id: editingCameraId,
        name: camName.trim() || "Connection Test",
        source_type: camSourceType,
        source_url: camSourceUrl.trim(),
        auth_type: camAuthType,
        auth_username: camAuthType === "none" ? null : camAuthUsername.trim() || null,
        secret: camAuthType === "none" ? null : camSecret || null,
        timezone: camTimezone.trim() || "America/Chicago",
      });
      setFormProbe(probe);
    } catch (err) {
      setCameraBannerError(err instanceof Error ? err.message : "Camera connection test failed.");
    } finally {
      setCamTestingForm(false);
    }
  }

  async function handleSaveCamera(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setCameraBannerError(null);
    setCameraBannerSuccess(null);
    setCamSaving(true);

    try {
      if (editingCameraId !== null) {
        const patch: CameraPatchPayload = {
          name: camName.trim(),
          source_type: camSourceType,
          source_url: camSourceUrl.trim(),
          auth_type: camAuthType,
          auth_username: camAuthType === "none" ? null : camAuthUsername.trim() || null,
          timezone: camTimezone.trim(),
          interval_seconds: Number(camInterval),
          is_enabled: camEnabled,
        };
        if (camSecret.length > 0) {
          patch.secret = camSecret;
        }
        await updateAdminCamera(editingCameraId, patch);
        setCameraBannerSuccess(`Updated camera "${camName.trim()}".`);
      } else {
        await createAdminCamera({
          name: camName.trim(),
          source_type: camSourceType,
          source_url: camSourceUrl.trim(),
          auth_type: camAuthType,
          auth_username: camAuthType === "none" ? null : camAuthUsername.trim() || null,
          secret: camAuthType === "none" ? null : camSecret || null,
          timezone: camTimezone.trim(),
          interval_seconds: Number(camInterval),
          is_enabled: camEnabled,
        });
        setCameraBannerSuccess(`Added camera "${camName.trim()}".`);
      }
      resetCameraForm();
      await loadCameras();
    } catch (err) {
      setCameraBannerError(err instanceof Error ? err.message : "Unable to save camera.");
    } finally {
      setCamSaving(false);
    }
  }

  async function handleRowTestCapture(camera: CameraAdmin): Promise<void> {
    setCameraBannerError(null);
    try {
      const probe = await testExistingCameraCapture(camera.id);
      setCameraProbes((prev) => ({ ...prev, [camera.id]: probe }));
    } catch (err) {
      setCameraBannerError(
        err instanceof Error ? err.message : `Test capture failed for ${camera.name}.`,
      );
    }
  }

  async function handleCaptureNow(camera: CameraAdmin): Promise<void> {
    setCameraBannerError(null);
    setCameraBannerSuccess(null);
    try {
      const frame = await captureCameraNow(camera.id);
      setCameraBannerSuccess(
        `Captured full-res frame (${frame.width}×${frame.height}) for "${camera.name}".`,
      );
      await loadCameras();
    } catch (err) {
      setCameraBannerError(
        err instanceof Error ? err.message : `Capture failed for ${camera.name}.`,
      );
    }
  }

  async function handleToggleCameraEnabled(camera: CameraAdmin): Promise<void> {
    setCameraBannerError(null);
    setCameraBannerSuccess(null);
    try {
      await updateAdminCamera(camera.id, { is_enabled: !camera.is_enabled });
      await loadCameras();
    } catch (err) {
      setCameraBannerError(err instanceof Error ? err.message : "Failed to update camera status.");
    }
  }

  async function handleDeleteCamera(camera: CameraAdmin): Promise<void> {
    setCameraBannerError(null);
    setCameraBannerSuccess(null);
    try {
      await deleteAdminCamera(camera.id);
      if (editingCameraId === camera.id) {
        resetCameraForm();
      }
      setCameraBannerSuccess(`Deleted camera "${camera.name}".`);
      await loadCameras();
    } catch (err) {
      setCameraBannerError(err instanceof Error ? err.message : "Failed to delete camera.");
    }
  }

  async function handleCreateUser(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setUsersError(null);
    setUsersSuccess(null);
    setCreatingUser(true);
    try {
      const created = await createAdminUser({
        username: newUsername.trim(),
        password: newPassword,
        role: newRole,
      });
      setNewUsername("");
      setNewPassword("");
      setNewRole("viewer");
      setUsersSuccess(`Created ${created.role} account "${created.username}".`);
      await loadUsers();
    } catch (err) {
      setUsersError(err instanceof Error ? err.message : "Failed to create user.");
    } finally {
      setCreatingUser(false);
    }
  }

  async function handleToggleUserRole(target: User): Promise<void> {
    setUsersError(null);
    setUsersSuccess(null);
    const nextRole: UserRole = target.role === "admin" ? "viewer" : "admin";
    try {
      await updateAdminUser(target.id, { role: nextRole });
      await loadUsers();
    } catch (err) {
      setUsersError(err instanceof Error ? err.message : "Failed to update user role.");
    }
  }

  async function handleToggleUserActive(target: User): Promise<void> {
    setUsersError(null);
    setUsersSuccess(null);
    try {
      await updateAdminUser(target.id, { is_active: !target.is_active });
      await loadUsers();
    } catch (err) {
      setUsersError(err instanceof Error ? err.message : "Failed to update user status.");
    }
  }

  async function handleDeleteUser(target: User): Promise<void> {
    setUsersError(null);
    setUsersSuccess(null);
    try {
      await deleteAdminUser(target.id);
      setUsersSuccess(`Removed user "${target.username}".`);
      await loadUsers();
    } catch (err) {
      setUsersError(err instanceof Error ? err.message : "Failed to delete user.");
    }
  }

  async function handleSaveSettings(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setStorageError(null);
    setSettingsSuccess(null);
    setSavingSettings(true);
    try {
      const updated = await updateAdminSettings(settings);
      setSettings(updated);
      setSettingsSuccess("System defaults saved.");
    } catch (err) {
      setStorageError(err instanceof Error ? err.message : "Failed to save system settings.");
    } finally {
      setSavingSettings(false);
    }
  }

  const diskUsedPercent =
    storage && storage.disk_total_bytes > 0
      ? Math.min(100, Math.round((storage.disk_used_bytes / storage.disk_total_bytes) * 100))
      : 0;

  return (
    <div className="studio-stack">
      <div className="view-heading-bar">
        <div>
          <h2 className="view-title">Admin Console</h2>
          <p className="section-copy">
            Configure camera endpoints, encrypted credentials, capture schedules, user roles, and
            volume settings directly from the browser.
          </p>
        </div>

        <nav aria-label="Admin sections" className="sub-nav">
          <button
            type="button"
            data-testid="admin-tab-cameras"
            aria-pressed={subTab === "cameras"}
            className={
              subTab === "cameras" ? "btn btn--toggle btn--toggle-active" : "btn btn--toggle"
            }
            onClick={() => setSubTab("cameras")}
          >
            Cameras
          </button>
          <button
            type="button"
            data-testid="admin-tab-users"
            aria-pressed={subTab === "users"}
            className={
              subTab === "users" ? "btn btn--toggle btn--toggle-active" : "btn btn--toggle"
            }
            onClick={() => setSubTab("users")}
          >
            Users
          </button>
          <button
            type="button"
            data-testid="admin-tab-storage"
            aria-pressed={subTab === "storage"}
            className={
              subTab === "storage" ? "btn btn--toggle btn--toggle-active" : "btn btn--toggle"
            }
            onClick={() => setSubTab("storage")}
          >
            Storage &amp; Settings
          </button>
        </nav>
      </div>

      {subTab === "cameras" && (
        <>
          {cameraBannerError && (
            <p role="alert" className="error-banner">
              {cameraBannerError}
            </p>
          )}
          {cameraBannerSuccess && (
            <p role="status" className="success-banner">
              {cameraBannerSuccess}
            </p>
          )}

          <section className="card" aria-labelledby="configured-cameras-heading">
            <div className="card-header">
              <h3 id="configured-cameras-heading" className="subsection-title">
                Configured Cameras
              </h3>
              <span className="meta-pill">{cameras.length} configured</span>
            </div>

            {camerasLoading ? (
              <p className="section-copy">Loading camera configurations…</p>
            ) : cameras.length === 0 ? (
              <p className="section-copy">
                No cameras registered yet. Use the form below to connect an RTSP, HTTP snapshot, or
                synthetic camera.
              </p>
            ) : (
              <div className="camera-list">
                {cameras.map((cam) => {
                  const statusLabel = !cam.is_enabled
                    ? "Disabled"
                    : cam.last_capture_status === "error"
                      ? "error"
                      : cam.last_capture_status === "ok"
                        ? "ok"
                        : "Idle";
                  const statusClass = !cam.is_enabled
                    ? "status-badge status-badge--neutral"
                    : cam.last_capture_status === "error"
                      ? "status-badge status-badge--error"
                      : cam.last_capture_status === "ok"
                        ? "status-badge status-badge--healthy"
                        : "status-badge status-badge--loading";
                  const rowProbe = cameraProbes[cam.id];

                  return (
                    <article key={cam.id} className="camera-row">
                      <div className="camera-row-header">
                        <div>
                          <p className="camera-row-title">{cam.name}</p>
                          <p className="field-hint">
                            <code>{cam.slug}</code> · {cam.source_type.toUpperCase()} ·{" "}
                            {cam.source_url}
                          </p>
                        </div>
                        <span className={statusClass}>{statusLabel}</span>
                      </div>

                      <dl className="camera-meta-row">
                        <div>
                          <dt>Frames</dt>
                          <dd>{cam.frame_count.toLocaleString()}</dd>
                        </div>
                        <div>
                          <dt>Interval</dt>
                          <dd>{cam.interval_seconds}s</dd>
                        </div>
                        <div>
                          <dt>Timezone</dt>
                          <dd>{cam.timezone}</dd>
                        </div>
                        <div>
                          <dt>Auth</dt>
                          <dd>
                            {cam.auth_type}
                            {cam.has_credentials ? " (encrypted secret stored)" : ""}
                          </dd>
                        </div>
                        <div>
                          <dt>Last Capture</dt>
                          <dd>{formatTimestamp(cam.last_capture_at)}</dd>
                        </div>
                      </dl>

                      {cam.last_capture_error && (
                        <p className="error-banner">{cam.last_capture_error}</p>
                      )}

                      <div className="button-group">
                        <button
                          type="button"
                          className="btn btn--secondary"
                          onClick={() => {
                            void handleRowTestCapture(cam);
                          }}
                        >
                          Test Capture
                        </button>
                        <button
                          type="button"
                          className="btn btn--primary"
                          onClick={() => {
                            void handleCaptureNow(cam);
                          }}
                        >
                          Capture Now
                        </button>
                        <button
                          type="button"
                          className="btn btn--secondary"
                          onClick={() => {
                            void handleToggleCameraEnabled(cam);
                          }}
                        >
                          {cam.is_enabled ? "Disable" : "Enable"}
                        </button>
                        <button
                          type="button"
                          className="btn btn--secondary"
                          onClick={() => startEditCamera(cam)}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="btn btn--danger"
                          onClick={() => {
                            void handleDeleteCamera(cam);
                          }}
                        >
                          Delete
                        </button>
                      </div>

                      {rowProbe && (
                        <div className="probe-preview-box">
                          <p className="field-hint">
                            Live test capture succeeded ({rowProbe.width}×{rowProbe.height}):
                          </p>
                          <img
                            src={rowProbe.preview_data_url}
                            alt={`Live test capture for ${cam.name}`}
                            className="probe-preview-image"
                          />
                        </div>
                      )}
                    </article>
                  );
                })}
              </div>
            )}
          </section>

          <section className="card" aria-labelledby="camera-form-heading">
            <div className="card-header">
              <h3 id="camera-form-heading" className="subsection-title">
                {editingCameraId !== null ? "Edit Camera" : "Add Camera"}
              </h3>
              {editingCameraId !== null && (
                <button type="button" className="btn btn--secondary" onClick={resetCameraForm}>
                  Cancel Edit
                </button>
              )}
            </div>

            <form data-testid="camera-form" className="form-stack" onSubmit={handleSaveCamera}>
              <div className="form-grid">
                <div className="form-field">
                  <label htmlFor="camera-name-input" className="form-label">
                    Camera Name
                  </label>
                  <input
                    id="camera-name-input"
                    type="text"
                    className="form-input"
                    value={camName}
                    onChange={(event) => setCamName(event.target.value)}
                    required
                  />
                </div>

                <div className="form-field">
                  <label htmlFor="camera-source-type-select" className="form-label">
                    Source Type
                  </label>
                  <select
                    id="camera-source-type-select"
                    className="form-select"
                    value={camSourceType}
                    onChange={(event) => {
                      const nextType = event.target.value as CameraSourceType;
                      setCamSourceType(nextType);
                      if (nextType === "synthetic" && !camSourceUrl.startsWith("synthetic://")) {
                        setCamSourceUrl("synthetic://garden");
                      } else if (
                        nextType !== "synthetic" &&
                        camSourceUrl.startsWith("synthetic://")
                      ) {
                        setCamSourceUrl("");
                      }
                      if (
                        editingCameraId === null &&
                        nextType !== "synthetic" &&
                        camAuthType === "none"
                      ) {
                        setCamAuthType("digest");
                        if (!camAuthUsername) {
                          setCamAuthUsername("admin");
                        }
                      }
                    }}
                  >
                    <option value="synthetic">synthetic</option>
                    <option value="http">http</option>
                    <option value="rtsp">rtsp</option>
                  </select>
                </div>

                <div className="form-field">
                  <label htmlFor="camera-source-url-input" className="form-label">
                    Stream / Snapshot URL
                  </label>
                  <input
                    id="camera-source-url-input"
                    type="text"
                    className="form-input"
                    placeholder={
                      camSourceType === "rtsp"
                        ? "rtsp://192.168.1.2:554/Preview_01_main"
                        : camSourceType === "http"
                          ? "https://192.168.1.2/cgi-bin/api.cgi?cmd=Snap&channel=0"
                          : "synthetic://garden"
                    }
                    value={camSourceUrl}
                    onChange={(event) => setCamSourceUrl(event.target.value)}
                    required
                  />
                  {camSourceType === "http" && (
                    <span className="field-hint">
                      Reolink HTTP/HTTPS (e.g. https://192.168.1.2) and standard snapshot URLs
                      supported
                    </span>
                  )}
                  {camSourceType === "rtsp" && (
                    <span className="field-hint">
                      Reolink full-res stream: rtsp://192.168.1.2:554/Preview_01_main
                    </span>
                  )}
                </div>

                <div className="form-field">
                  <label htmlFor="camera-auth-type-select" className="form-label">
                    Authentication
                  </label>
                  <select
                    id="camera-auth-type-select"
                    className="form-select"
                    value={camAuthType}
                    onChange={(event) => setCamAuthType(event.target.value as CameraAuthType)}
                  >
                    <option value="none">none</option>
                    <option value="basic">basic</option>
                    <option value="digest">digest</option>
                    <option value="bearer">bearer</option>
                  </select>
                </div>

                {camAuthType !== "none" && (
                  <>
                    <div className="form-field">
                      <label htmlFor="camera-auth-username-input" className="form-label">
                        Camera Username
                      </label>
                      <input
                        id="camera-auth-username-input"
                        type="text"
                        className="form-input"
                        autoComplete="off"
                        value={camAuthUsername}
                        onChange={(event) => setCamAuthUsername(event.target.value)}
                      />
                    </div>

                    <div className="form-field">
                      <label htmlFor="camera-auth-secret-input" className="form-label">
                        Camera Password / Token
                      </label>
                      <input
                        id="camera-auth-secret-input"
                        type="password"
                        className="form-input"
                        autoComplete="new-password"
                        value={camSecret}
                        onChange={(event) => setCamSecret(event.target.value)}
                      />
                      <span className="field-hint">
                        Encrypted at rest; leave blank when editing to keep existing secret
                      </span>
                    </div>
                  </>
                )}

                <div className="form-field">
                  <label htmlFor="camera-timezone-input" className="form-label">
                    IANA Timezone
                  </label>
                  <input
                    id="camera-timezone-input"
                    type="text"
                    className="form-input"
                    value={camTimezone}
                    onChange={(event) => setCamTimezone(event.target.value)}
                    required
                  />
                </div>

                <div className="form-field">
                  <label htmlFor="camera-interval-input" className="form-label">
                    Capture Interval (seconds)
                  </label>
                  <input
                    id="camera-interval-input"
                    type="number"
                    min={10}
                    max={86400}
                    className="form-input"
                    value={camInterval}
                    onChange={(event) => setCamInterval(Math.max(10, Number(event.target.value)))}
                    required
                  />
                </div>

                <div className="form-field form-field--checkbox">
                  <label htmlFor="camera-enabled-checkbox" className="checkbox-label">
                    <input
                      id="camera-enabled-checkbox"
                      type="checkbox"
                      checked={camEnabled}
                      onChange={(event) => setCamEnabled(event.target.checked)}
                    />
                    <span>Enabled</span>
                  </label>
                </div>
              </div>

              {cameraBannerError && (
                <p role="alert" className="error-banner">
                  {cameraBannerError}
                </p>
              )}

              {formProbe && (
                <div className="probe-preview-box">
                  <p className="field-hint">
                    Connection verified ({formProbe.width}×{formProbe.height}):
                  </p>
                  <img
                    src={formProbe.preview_data_url}
                    alt="Live camera connection test preview"
                    className="probe-preview-image"
                  />
                </div>
              )}

              <div className="form-actions">
                <button
                  type="button"
                  className="btn btn--secondary"
                  disabled={camTestingForm || !camSourceUrl.trim()}
                  onClick={() => {
                    void handleTestFormConnection();
                  }}
                >
                  {camTestingForm ? "Testing Connection..." : "Test Connection"}
                </button>

                <button
                  type="submit"
                  data-testid="save-camera-button"
                  className="btn btn--primary"
                  disabled={camSaving}
                >
                  {camSaving ? "Saving..." : "Save Camera"}
                </button>
              </div>
            </form>
          </section>
        </>
      )}

      {subTab === "users" && (
        <>
          {usersError && (
            <p role="alert" className="error-banner">
              {usersError}
            </p>
          )}
          {usersSuccess && (
            <p role="status" className="success-banner">
              {usersSuccess}
            </p>
          )}

          <section className="card" aria-labelledby="users-table-heading">
            <div className="card-header">
              <h3 id="users-table-heading" className="subsection-title">
                User Accounts
              </h3>
              <span className="meta-pill">{users.length} accounts</span>
            </div>

            {usersLoading ? (
              <p className="section-copy">Loading accounts…</p>
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th scope="col">Username</th>
                      <th scope="col">Role</th>
                      <th scope="col">Status</th>
                      <th scope="col">Created</th>
                      <th scope="col">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((account) => {
                      const isSelf = account.id === currentUser.id;
                      return (
                        <tr key={account.id}>
                          <td>
                            <strong>{account.username}</strong>
                            {isSelf ? " (you)" : ""}
                          </td>
                          <td>
                            <span className="role-pill">{account.role}</span>
                          </td>
                          <td>{account.is_active ? "Active" : "Disabled"}</td>
                          <td>{formatTimestamp(account.created_at)}</td>
                          <td>
                            <div className="button-group">
                              <button
                                type="button"
                                className="btn btn--secondary btn--small"
                                disabled={isSelf}
                                onClick={() => {
                                  void handleToggleUserRole(account);
                                }}
                              >
                                {account.role === "admin" ? "Set Viewer" : "Set Admin"}
                              </button>
                              <button
                                type="button"
                                className="btn btn--secondary btn--small"
                                disabled={isSelf}
                                onClick={() => {
                                  void handleToggleUserActive(account);
                                }}
                              >
                                {account.is_active ? "Disable" : "Enable"}
                              </button>
                              <button
                                type="button"
                                className="btn btn--danger btn--small"
                                disabled={isSelf}
                                onClick={() => {
                                  void handleDeleteUser(account);
                                }}
                              >
                                Delete
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="card" aria-labelledby="create-user-heading">
            <div className="card-header">
              <h3 id="create-user-heading" className="subsection-title">
                Create User
              </h3>
            </div>

            <form data-testid="create-user-form" className="form-stack" onSubmit={handleCreateUser}>
              <div className="form-grid">
                <div className="form-field">
                  <label htmlFor="create-user-username" className="form-label">
                    Username
                  </label>
                  <input
                    id="create-user-username"
                    type="text"
                    className="form-input"
                    autoComplete="off"
                    value={newUsername}
                    onChange={(event) => setNewUsername(event.target.value)}
                    required
                  />
                </div>

                <div className="form-field">
                  <label htmlFor="create-user-password" className="form-label">
                    Temporary Password
                  </label>
                  <input
                    id="create-user-password"
                    type="password"
                    className="form-input"
                    autoComplete="new-password"
                    value={newPassword}
                    onChange={(event) => setNewPassword(event.target.value)}
                    required
                  />
                </div>

                <div className="form-field">
                  <label htmlFor="create-user-role" className="form-label">
                    Role
                  </label>
                  <select
                    id="create-user-role"
                    className="form-select"
                    value={newRole}
                    onChange={(event) => setNewRole(event.target.value as UserRole)}
                  >
                    <option value="viewer">viewer</option>
                    <option value="admin">admin</option>
                  </select>
                </div>
              </div>

              <div className="form-actions">
                <button
                  type="submit"
                  data-testid="create-user-button"
                  className="btn btn--primary"
                  disabled={creatingUser}
                >
                  {creatingUser ? "Creating..." : "Create User"}
                </button>
              </div>
            </form>
          </section>
        </>
      )}

      {subTab === "storage" && (
        <>
          {storageError && (
            <p role="alert" className="error-banner">
              {storageError}
            </p>
          )}
          {settingsSuccess && (
            <p role="status" className="success-banner">
              {settingsSuccess}
            </p>
          )}

          <section className="card" aria-labelledby="storage-capacity-heading">
            <div className="card-header">
              <h3 id="storage-capacity-heading" className="subsection-title">
                Volume Storage Capacity
              </h3>
              {storage && (
                <span className="meta-pill">
                  {diskUsedPercent}% used ({formatBytes(storage.disk_free_bytes)} free)
                </span>
              )}
            </div>

            {storageLoading || !storage ? (
              <p className="section-copy">Loading disk metrics…</p>
            ) : (
              <div className="storage-stack">
                <progress
                  className="progress-bar"
                  value={storage.disk_used_bytes}
                  max={Math.max(1, storage.disk_total_bytes)}
                  aria-label="Host volume capacity usage"
                />

                <dl className="metrics-grid">
                  <div className="metric-box">
                    <dt className="metric-label">Disk Free / Total</dt>
                    <dd className="metric-value">
                      {formatBytes(storage.disk_free_bytes)} free of{" "}
                      {formatBytes(storage.disk_total_bytes)}
                    </dd>
                  </div>
                  <div className="metric-box">
                    <dt className="metric-label">Untouched Originals</dt>
                    <dd className="metric-value">{formatBytes(storage.photos_bytes)}</dd>
                  </div>
                  <div className="metric-box">
                    <dt className="metric-label">WebP Previews</dt>
                    <dd className="metric-value">{formatBytes(storage.previews_bytes)}</dd>
                  </div>
                  <div className="metric-box">
                    <dt className="metric-label">Ephemeral MP4 Cache</dt>
                    <dd className="metric-value">{formatBytes(storage.renders_bytes)}</dd>
                  </div>
                  <div className="metric-box">
                    <dt className="metric-label">Total Frames</dt>
                    <dd className="metric-value">{storage.total_frames.toLocaleString()}</dd>
                  </div>
                  <div className="metric-box">
                    <dt className="metric-label">Active Renders</dt>
                    <dd className="metric-value">{storage.active_renders}</dd>
                  </div>
                </dl>
              </div>
            )}
          </section>

          <section className="card" aria-labelledby="system-defaults-heading">
            <div className="card-header">
              <h3 id="system-defaults-heading" className="subsection-title">
                System Defaults
              </h3>
            </div>

            <form className="form-stack" onSubmit={handleSaveSettings}>
              <div className="form-grid">
                <div className="form-field">
                  <label htmlFor="settings-default-timezone" className="form-label">
                    Default Timezone
                  </label>
                  <input
                    id="settings-default-timezone"
                    type="text"
                    className="form-input"
                    value={settings.default_timezone}
                    onChange={(event) =>
                      setSettings((prev) => ({
                        ...prev,
                        default_timezone: event.target.value,
                      }))
                    }
                    required
                  />
                </div>

                <div className="form-field">
                  <label htmlFor="settings-cache-ttl" className="form-label">
                    Render Cache TTL (hours)
                  </label>
                  <input
                    id="settings-cache-ttl"
                    type="number"
                    min={1}
                    max={720}
                    className="form-input"
                    value={settings.render_cache_ttl_hours}
                    onChange={(event) =>
                      setSettings((prev) => ({
                        ...prev,
                        render_cache_ttl_hours: Math.max(1, Number(event.target.value) || 24),
                      }))
                    }
                    required
                  />
                </div>

                <div className="form-field">
                  <label htmlFor="settings-default-tolerance" className="form-label">
                    Default Mode 2 Tolerance (minutes)
                  </label>
                  <input
                    id="settings-default-tolerance"
                    type="number"
                    min={0}
                    max={720}
                    className="form-input"
                    value={settings.default_tolerance_minutes}
                    onChange={(event) =>
                      setSettings((prev) => ({
                        ...prev,
                        default_tolerance_minutes: Math.max(0, Number(event.target.value) || 0),
                      }))
                    }
                    required
                  />
                </div>
              </div>

              <div className="form-actions">
                <button type="submit" className="btn btn--primary" disabled={savingSettings}>
                  {savingSettings ? "Saving..." : "Save Settings"}
                </button>
              </div>
            </form>
          </section>
        </>
      )}
    </div>
  );
}

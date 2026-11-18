import { type FormEvent, useEffect, useState } from "react";
import { changePassword } from "../api";

interface AccountModalProps {
  onClose: () => void;
}

export function AccountModal({ onClose }: AccountModalProps) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        onClose();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setSuccessMessage(null);

    if (!currentPassword || !newPassword) {
      setError("Both current and new passwords are required.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("New passwords do not match.");
      return;
    }

    setSubmitting(true);
    try {
      await changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setSuccessMessage("Password updated.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update password.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <div
        className="card modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="change-password-heading"
      >
        <div className="card-header">
          <h2 id="change-password-heading" className="section-title">
            Change Password
          </h2>
          <button type="button" className="btn btn--secondary" onClick={onClose}>
            Close
          </button>
        </div>

        <form data-testid="password-form" className="form-stack" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="account-current-password" className="form-label">
              Current password
            </label>
            <input
              id="account-current-password"
              type="password"
              className="form-input"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              required
            />
          </div>

          <div className="form-field">
            <label htmlFor="account-new-password" className="form-label">
              New password
            </label>
            <input
              id="account-new-password"
              type="password"
              className="form-input"
              autoComplete="new-password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              required
            />
          </div>

          <div className="form-field">
            <label htmlFor="account-confirm-password" className="form-label">
              Confirm new password
            </label>
            <input
              id="account-confirm-password"
              type="password"
              className="form-input"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              required
            />
          </div>

          {error && (
            <p role="alert" className="error-banner">
              {error}
            </p>
          )}

          {successMessage && (
            <p role="status" className="success-banner">
              {successMessage}
            </p>
          )}

          <div className="form-actions">
            <button type="submit" className="btn btn--primary" disabled={submitting}>
              {submitting ? "Updating..." : "Update Password"}
            </button>
            <button type="button" className="btn btn--secondary" onClick={onClose}>
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

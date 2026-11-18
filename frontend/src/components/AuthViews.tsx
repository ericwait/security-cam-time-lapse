import { type FormEvent, useState } from "react";
import { bootstrapAdmin, login } from "../api";
import type { User } from "../types";

interface AuthViewProps {
  onSuccess: (user: User) => void;
}

export function BootstrapView({ onSuccess }: AuthViewProps) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);

    const trimmedUsername = username.trim();
    if (!trimmedUsername) {
      setError("Username is required.");
      return;
    }
    if (!password) {
      setError("Password is required.");
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setSubmitting(true);
    try {
      const createdUser = await bootstrapAdmin(trimmedUsername, password);
      onSuccess(createdUser);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create initial admin account.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="card auth-card" aria-labelledby="bootstrap-heading">
      <div className="card-header">
        <div>
          <h2 id="bootstrap-heading" className="section-title">
            Initial Admin Setup
          </h2>
          <p className="section-copy">
            Create the primary administrator account for this self-hosted instance to manage
            cameras, schedules, and household viewers.
          </p>
        </div>
      </div>

      <form data-testid="bootstrap-form" className="form-stack" onSubmit={handleSubmit}>
        <div className="form-field">
          <label htmlFor="bootstrap-username" className="form-label">
            Username
          </label>
          <input
            id="bootstrap-username"
            name="username"
            type="text"
            className="form-input"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
          />
        </div>

        <div className="form-field">
          <label htmlFor="bootstrap-password" className="form-label">
            Password
          </label>
          <input
            id="bootstrap-password"
            name="password"
            type="password"
            className="form-input"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </div>

        <div className="form-field">
          <label htmlFor="bootstrap-confirm-password" className="form-label">
            Confirm password
          </label>
          <input
            id="bootstrap-confirm-password"
            name="confirmPassword"
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

        <div className="form-actions">
          <button type="submit" className="btn btn--primary" disabled={submitting}>
            {submitting ? "Creating Account..." : "Create Admin Account"}
          </button>
        </div>
      </form>
    </section>
  );
}

export function LoginView({ onSuccess }: AuthViewProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);

    const trimmedUsername = username.trim();
    if (!trimmedUsername || !password) {
      setError("Enter both username and password.");
      return;
    }

    setSubmitting(true);
    try {
      const authenticatedUser = await login(trimmedUsername, password);
      onSuccess(authenticatedUser);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to sign in.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="card auth-card" aria-labelledby="login-heading">
      <div className="card-header">
        <div>
          <h2 id="login-heading" className="section-title">
            Sign In
          </h2>
          <p className="section-copy">
            Authenticate with your Admin or Viewer account to scrub timelapses and export MP4 clips.
          </p>
        </div>
      </div>

      <form data-testid="login-form" className="form-stack" onSubmit={handleSubmit}>
        <div className="form-field">
          <label htmlFor="login-username" className="form-label">
            Username
          </label>
          <input
            id="login-username"
            name="username"
            type="text"
            className="form-input"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
          />
        </div>

        <div className="form-field">
          <label htmlFor="login-password" className="form-label">
            Password
          </label>
          <input
            id="login-password"
            name="password"
            type="password"
            className="form-input"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </div>

        {error && (
          <p role="alert" className="error-banner">
            {error}
          </p>
        )}

        <div className="form-actions">
          <button type="submit" className="btn btn--primary" disabled={submitting}>
            {submitting ? "Signing In..." : "Sign In"}
          </button>
        </div>
      </form>
    </section>
  );
}

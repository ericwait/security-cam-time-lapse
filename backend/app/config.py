import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

DEFAULT_DATA_DIR: Final[Path] = Path("/data")
DEFAULT_FRONTEND_DIST_DIR: Final[Path] = Path("/workspace/frontend/dist")
DEFAULT_VERSION: Final[str] = "0.1.0-alpha"
MIN_SECRET_KEY_LENGTH: Final[int] = 16


class SettingsValidationError(RuntimeError):
    """Raised when required secret configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable runtime configuration resolved from the environment contract."""

    secret_key: str = field(repr=False)
    bootstrap_admin_password: str | None = field(repr=False)
    data_dir: Path
    version: str
    frontend_dist_dir: Path

    @property
    def db_path(self) -> Path:
        """Return the SQLite database file path on the persistent data volume."""
        return self.data_dir / "db" / "timelapse.sqlite3"

    @property
    def photos_dir(self) -> Path:
        """Return the full-resolution original photos root directory."""
        return self.data_dir / "photos"

    @property
    def previews_dir(self) -> Path:
        """Return the lightweight WebP previews root directory."""
        return self.data_dir / "previews"

    @property
    def renders_dir(self) -> Path:
        """Return the ephemeral MP4 timelapse render cache directory."""
        return self.data_dir / "cache" / "renders"

    def ensure_storage_dirs(self) -> None:
        """Create the persistent volume subdirectories idempotently."""
        for directory in (
            self.db_path.parent,
            self.photos_dir,
            self.previews_dir,
            self.renders_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def compute_version(env: Mapping[str, str] | None = None) -> str:
    """Return APP_VERSION when provided, falling back to 0.1.0-alpha."""
    env_map = os.environ if env is None else env
    configured_version = env_map.get("APP_VERSION", "").strip()
    if configured_version:
        return configured_version
    return DEFAULT_VERSION


def load_settings(
    *,
    env: Mapping[str, str] | None = None,
    data_dir: Path | None = None,
    frontend_dist_dir: Path | None = None,
) -> Settings:
    """Validate and construct immutable runtime settings without echoing secrets."""
    env_map = os.environ if env is None else env
    raw_secret = env_map.get("APP_SECRET_KEY")
    if raw_secret is None or not raw_secret.strip():
        raise SettingsValidationError(
            "APP_SECRET_KEY is missing or empty. Copy .env.example to .env and populate "
            "APP_SECRET_KEY (for example: openssl rand -hex 32)."
        )

    secret_key = raw_secret.strip()
    if len(secret_key) < MIN_SECRET_KEY_LENGTH:
        raise SettingsValidationError(
            f"APP_SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters long. "
            "Update APP_SECRET_KEY in .env following .env.example "
            "(for example: openssl rand -hex 32)."
        )

    raw_bootstrap = env_map.get("BOOTSTRAP_ADMIN_PASSWORD")
    bootstrap_admin_password: str | None = None
    if raw_bootstrap is not None and raw_bootstrap.strip():
        bootstrap_admin_password = raw_bootstrap

    raw_data_dir = env_map.get("TIMELAPSE_DATA_DIR", "").strip()
    if data_dir is not None:
        resolved_data_dir = data_dir
    elif raw_data_dir:
        resolved_data_dir = Path(raw_data_dir)
    else:
        resolved_data_dir = DEFAULT_DATA_DIR

    resolved_frontend_dist_dir = DEFAULT_FRONTEND_DIST_DIR
    if frontend_dist_dir is not None:
        resolved_frontend_dist_dir = frontend_dist_dir

    return Settings(
        secret_key=secret_key,
        bootstrap_admin_password=bootstrap_admin_password,
        data_dir=resolved_data_dir,
        version=compute_version(env_map),
        frontend_dist_dir=resolved_frontend_dist_dir,
    )

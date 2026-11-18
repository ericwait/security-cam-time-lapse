from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.cli import build_parser
from app.config import (
    DEFAULT_DATA_DIR,
    DEFAULT_FRONTEND_DIST_DIR,
    DEFAULT_VERSION,
    SettingsValidationError,
    compute_version,
    load_settings,
)


def test_load_settings_rejects_missing_secret_key() -> None:
    with pytest.raises(SettingsValidationError, match=r"\.env\.example"):
        load_settings(env={})


@pytest.mark.parametrize("blank_value", ["", "   ", "\t\n"])
def test_load_settings_rejects_blank_secret_key(blank_value: str) -> None:
    with pytest.raises(SettingsValidationError, match="APP_SECRET_KEY is missing or empty"):
        load_settings(env={"APP_SECRET_KEY": blank_value})


def test_load_settings_rejects_short_secret_without_echoing_value() -> None:
    short_candidate = "short-dummy-000"
    with pytest.raises(SettingsValidationError, match="at least 16 characters") as exc_info:
        load_settings(env={"APP_SECRET_KEY": short_candidate})

    assert short_candidate not in str(exc_info.value)
    assert short_candidate not in repr(exc_info.value)


def test_load_settings_masks_secrets_in_repr_and_enforces_immutability(tmp_path: Path) -> None:
    dummy_secret = "unit-test-dummy-secret-0000000000"
    dummy_password = "unit-test-dummy-admin-0000"
    settings = load_settings(
        env={
            "APP_SECRET_KEY": f"  {dummy_secret}  ",
            "BOOTSTRAP_ADMIN_PASSWORD": dummy_password,
            "APP_VERSION": "0.1.0-alpha.7",
        },
        data_dir=tmp_path / "data",
        frontend_dist_dir=tmp_path / "dist",
    )

    assert settings.secret_key == dummy_secret
    assert settings.bootstrap_admin_password == dummy_password
    assert settings.version == "0.1.0-alpha.7"
    assert settings.data_dir == tmp_path / "data"
    assert settings.db_path == tmp_path / "data" / "db" / "timelapse.sqlite3"
    assert settings.frontend_dist_dir == tmp_path / "dist"

    rendered = repr(settings)
    assert dummy_secret not in rendered
    assert dummy_password not in rendered

    with pytest.raises(FrozenInstanceError):
        settings.version = "0.2.0"  # type: ignore[misc]


def test_load_settings_defaults_and_blank_bootstrap_password() -> None:
    settings = load_settings(
        env={
            "APP_SECRET_KEY": "unit-test-dummy-secret-0000000000",
            "BOOTSTRAP_ADMIN_PASSWORD": "   ",
        }
    )

    assert settings.bootstrap_admin_password is None
    assert settings.data_dir == DEFAULT_DATA_DIR
    assert settings.frontend_dist_dir == DEFAULT_FRONTEND_DIST_DIR
    assert settings.version == DEFAULT_VERSION


def test_load_settings_honors_timelapse_data_dir_override(tmp_path: Path) -> None:
    custom_dir = tmp_path / "mounted-data"
    settings = load_settings(
        env={
            "APP_SECRET_KEY": "unit-test-dummy-secret-0000000000",
            "TIMELAPSE_DATA_DIR": str(custom_dir),
        }
    )
    assert settings.data_dir == custom_dir


def test_ensure_storage_dirs_creates_expected_layout_idempotently(tmp_path: Path) -> None:
    settings = load_settings(
        env={"APP_SECRET_KEY": "unit-test-dummy-secret-0000000000"},
        data_dir=tmp_path / "volume",
    )
    settings.ensure_storage_dirs()
    settings.ensure_storage_dirs()

    assert (settings.data_dir / "db").is_dir()
    assert (settings.data_dir / "photos").is_dir()
    assert (settings.data_dir / "previews").is_dir()
    assert (settings.data_dir / "cache" / "renders").is_dir()


def test_compute_version_prefers_configured_env_value() -> None:
    assert compute_version({"APP_VERSION": " 1.2.3-alpha.1 "}) == "1.2.3-alpha.1"
    assert compute_version({"APP_VERSION": "   "}) == DEFAULT_VERSION
    assert compute_version({}) == DEFAULT_VERSION


def test_cli_parser_accepts_seed_dev_subcommand() -> None:
    parser = build_parser()
    parsed = parser.parse_args(["seed-dev"])
    assert parsed.command == "seed-dev"

    with pytest.raises(SystemExit):
        parser.parse_args([])

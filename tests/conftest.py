from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.config import Settings, load_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DUMMY_SECRET = "test-only-dummy-secret-key-00000000"
TEST_DUMMY_ADMIN_PASS = "test-only-dummy-admin-0000"


def build_alembic_config(db_path: Path) -> Config:
    """Create an Alembic Config bound to a specific on-disk SQLite file."""
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("script_location", str(REPO_ROOT / "backend" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture
def app_settings(tmp_path: Path) -> Settings:
    """Return validated runtime Settings backed by an isolated temporary directory."""
    return load_settings(
        env={
            "APP_SECRET_KEY": TEST_DUMMY_SECRET,
            "BOOTSTRAP_ADMIN_PASSWORD": TEST_DUMMY_ADMIN_PASS,
            "APP_VERSION": "0.1.0-alpha.test",
        },
        data_dir=tmp_path / "data",
        frontend_dist_dir=tmp_path / "frontend_dist",
    )


@pytest.fixture
def alembic_config(app_settings: Settings) -> Config:
    """Return an Alembic Config targeting the temporary test SQLite database."""
    app_settings.ensure_storage_dirs()
    return build_alembic_config(app_settings.db_path)


@pytest.fixture
def migrated_settings(app_settings: Settings, alembic_config: Config) -> Settings:
    """Return Settings after migrating the on-disk SQLite database to Alembic head."""
    command.upgrade(alembic_config, "head")
    return app_settings

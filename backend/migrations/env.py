import os
import subprocess
import sys
import tempfile
from logging.config import fileConfig
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import context
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, Engine, create_engine, pool
from sqlalchemy.engine import make_url
from sqlalchemy.sql.schema import Column
from sqlalchemy.types import TypeEngine

from app.db import create_sqlite_engine
from app.models import Base

DEFAULT_SQLITE_URL = "sqlite:////data/db/timelapse.sqlite3"

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _compare_sqlite_types(
    _migration_context: MigrationContext,
    _inspected_column: Column[Any],
    _metadata_column: Column[Any],
    inspected_type: TypeEngine[Any],
    metadata_type: TypeEngine[Any],
) -> bool | None:
    """Prevent false-positive timezone diffs on SQLite DATETIME reflection."""
    if isinstance(inspected_type, sa.DateTime) and isinstance(metadata_type, sa.DateTime):
        return False
    return None


def _is_cli_check_command(cfg: Config) -> bool:
    cmd_opts = cfg.cmd_opts
    if cmd_opts is None:
        return False
    cmd_attr: object = getattr(cmd_opts, "cmd", None)
    if isinstance(cmd_attr, tuple) and len(cmd_attr) > 0:
        func: object = cmd_attr[0]
        return getattr(func, "__name__", None) == "check"
    return False


def _resolve_database_url(cfg: Config) -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    x_db_url = x_args.get("db_url")
    if x_db_url:
        return x_db_url

    configured_url = cfg.get_main_option("sqlalchemy.url") or DEFAULT_SQLITE_URL
    data_dir_override = os.environ.get("TIMELAPSE_DATA_DIR", "").strip()
    if configured_url == DEFAULT_SQLITE_URL and data_dir_override:
        db_file = Path(data_dir_override) / "db" / "timelapse.sqlite3"
        return f"sqlite:///{db_file}"
    return configured_url


def _run_migrations_with_connection(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=_compare_sqlite_types,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_ephemeral_schema_check() -> None:
    with tempfile.TemporaryDirectory(prefix="timelapse-alembic-check-") as tmp_dir:
        temp_db_path = Path(tmp_dir) / "schema_check.sqlite3"
        temp_url = f"sqlite:///{temp_db_path}"
        cmd = [sys.executable, "-m", "alembic"]
        if config.config_file_name is not None:
            cmd.extend(["-c", config.config_file_name])
        cmd.extend(["-x", f"db_url={temp_url}", "upgrade", "head"])
        subprocess.run(cmd, check=True)

        engine = create_sqlite_engine(temp_db_path)
        try:
            with engine.connect() as connection:
                _run_migrations_with_connection(connection)
        finally:
            engine.dispose()


def run_migrations_offline() -> None:
    """Run migrations in offline mode without an active Engine."""
    url = _resolve_database_url(config)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=_compare_sqlite_types,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _create_migration_engine(url: str) -> Engine:
    parsed_url = make_url(url)
    if (
        parsed_url.get_backend_name() == "sqlite"
        and parsed_url.database
        and parsed_url.database != ":memory:"
    ):
        return create_sqlite_engine(Path(parsed_url.database))
    return create_engine(url, poolclass=pool.NullPool)


def run_migrations_online() -> None:
    """Run migrations in online mode against a real SQLite connection."""
    provided_connection = config.attributes.get("connection")
    if isinstance(provided_connection, Connection):
        _run_migrations_with_connection(provided_connection)
        return

    x_args = context.get_x_argument(as_dictionary=True)
    if "db_url" not in x_args and _is_cli_check_command(config):
        _run_ephemeral_schema_check()
        return

    engine = _create_migration_engine(_resolve_database_url(config))
    try:
        with engine.connect() as connection:
            _run_migrations_with_connection(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

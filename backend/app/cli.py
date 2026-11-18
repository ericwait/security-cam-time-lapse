import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime

from app.config import Settings, load_settings
from app.db import create_session_factory, create_sqlite_engine
from app.models import SystemSetting

SEED_SETTING_KEY = "substrate.seeded"


def seed_dev(settings: Settings | None = None) -> None:
    """Seed baseline Phase 0 operational metadata idempotently."""
    resolved_settings = settings if settings is not None else load_settings()
    resolved_settings.ensure_storage_dirs()

    engine = create_sqlite_engine(resolved_settings.db_path)
    session_factory = create_session_factory(engine)
    now = datetime.now(UTC)
    payload = json.dumps({"phase": 0})

    try:
        with session_factory() as session:
            existing = session.get(SystemSetting, SEED_SETTING_KEY)
            if existing is None:
                session.add(
                    SystemSetting(
                        key=SEED_SETTING_KEY,
                        value_json=payload,
                        updated_at=now,
                    )
                )
            else:
                existing.value_json = payload
                existing.updated_at = now
            session.commit()
    finally:
        engine.dispose()

    print(f"Seeded development substrate -> {resolved_settings.db_path}")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Security Cam Time-Lapse administrative CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "seed-dev",
        help="Idempotently seed the local development database",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point returning a process exit code."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "seed-dev":
        seed_dev()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

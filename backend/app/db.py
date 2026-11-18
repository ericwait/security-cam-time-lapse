import sqlite3
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def create_sqlite_engine(db_path: Path) -> Engine:
    """Create a SQLite SQLAlchemy engine configured for WAL concurrency."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite_pragmas(
        dbapi_connection: sqlite3.Connection,
        _connection_record: object,
    ) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.execute("PRAGMA busy_timeout=5000;")
        finally:
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to the provided SQLite engine."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

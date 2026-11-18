from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import Settings, load_settings
from app.db import create_session_factory, create_sqlite_engine


def _register_spa_routes(app: FastAPI, frontend_dist_dir: Path) -> None:
    dist_dir = frontend_dist_dir.resolve()
    index_file = dist_dir / "index.html"
    if not index_file.is_file():
        return

    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        if full_path:
            candidate = (dist_dir / full_path).resolve()
            if candidate.is_relative_to(dist_dir) and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index_file)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construct the FastAPI application with SQLite state and optional SPA assets."""
    resolved_settings = settings if settings is not None else load_settings()
    resolved_settings.ensure_storage_dirs()

    engine = create_sqlite_engine(resolved_settings.db_path)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Security Cam Time-Lapse",
        version=resolved_settings.version,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.engine = engine
    app.state.session_factory = session_factory

    @app.get("/api/health")
    def healthcheck() -> dict[str, str]:
        with session_factory() as session:
            session.execute(text("SELECT 1")).scalar_one()
            journal_mode = str(session.execute(text("PRAGMA journal_mode;")).scalar_one()).lower()
        return {
            "status": "ok",
            "version": resolved_settings.version,
            "database": "ok",
            "journal_mode": journal_mode,
        }

    _register_spa_routes(app, resolved_settings.frontend_dist_dir)
    return app


def __getattr__(name: str) -> FastAPI:
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

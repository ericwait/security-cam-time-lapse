"""Background capture scheduler and ephemeral render-cache TTL reaper."""

import logging
from datetime import UTC, datetime
from typing import Any, Final

import httpx
from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore[import-untyped]
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.capture import CaptureError
from app.config import Settings
from app.deps import ensure_utc
from app.models import Camera
from app.services import execute_camera_capture, reap_expired_render_jobs

logger = logging.getLogger(__name__)

CAPTURE_POLL_SECONDS: Final[int] = 15
RENDER_REAP_MINUTES: Final[int] = 10


class TimelapseScheduler:
    """Periodic background scheduler for due camera captures and expired render reaping."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings: Settings,
        http_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._http_transport = http_transport
        self._scheduler: Any = BackgroundScheduler(timezone=UTC)

    @property
    def running(self) -> bool:
        """Return True while the underlying APScheduler worker is running."""
        return bool(self._scheduler.running)

    def tick_captures(self, *, now_utc: datetime | None = None) -> int:
        """Execute scheduled captures for all enabled cameras due at now_utc."""
        captured_count = 0
        with self._session_factory() as session:
            if not inspect(session.get_bind()).has_table("cameras"):
                return 0

            cameras = session.scalars(
                select(Camera).where(Camera.is_enabled.is_(True)).order_by(Camera.id.asc())
            ).all()
            for camera in cameras:
                resolved_now = ensure_utc(now_utc) if now_utc is not None else datetime.now(UTC)
                if camera.last_capture_at is not None:
                    elapsed = (resolved_now - ensure_utc(camera.last_capture_at)).total_seconds()
                    if elapsed < camera.interval_seconds:
                        continue

                try:
                    execute_camera_capture(
                        session,
                        self._settings,
                        camera,
                        captured_at_utc=resolved_now,
                        http_transport=self._http_transport,
                    )
                    captured_count += 1
                except CaptureError as exc:
                    logger.warning("Scheduled capture failed for %s: %s", camera.slug, exc)

        return captured_count

    def tick_reap_renders(self, *, now_utc: datetime | None = None) -> int:
        """Reap expired MP4 render artifacts from disk and update job statuses."""
        with self._session_factory() as session:
            if not inspect(session.get_bind()).has_table("render_jobs"):
                return 0
            return reap_expired_render_jobs(session, self._settings, now_utc=now_utc)

    def start(self) -> None:
        """Register periodic capture and reaper jobs and start the background scheduler."""
        if self.running:
            return
        self._scheduler.add_job(
            self.tick_captures,
            "interval",
            seconds=CAPTURE_POLL_SECONDS,
            id="capture_tick",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.add_job(
            self.tick_reap_renders,
            "interval",
            minutes=RENDER_REAP_MINUTES,
            id="render_reap_tick",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.start()

    def shutdown(self) -> None:
        """Stop the background scheduler cleanly if running."""
        if self.running:
            self._scheduler.shutdown(wait=False)

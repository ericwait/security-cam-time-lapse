"""FastAPI request dependencies, cookie session lookup, and UTC normalization."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Final, cast

import httpx
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, sessionmaker

from app.config import Settings
from app.models import User, UserSession
from app.security import hash_session_token

SESSION_COOKIE_NAME: Final[str] = "timelapse_session"
SESSION_DURATION_DAYS: Final[int] = 7
SESSION_MAX_AGE_SECONDS: Final[int] = SESSION_DURATION_DAYS * 86400


def ensure_utc(dt: datetime) -> datetime:
    """Normalize a naive SQLite UTC datetime or aware datetime into aware UTC."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def coerce_utc(value: datetime | str) -> datetime:
    """Coerce a SQLite datetime object or ISO-8601 string into aware UTC."""
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    return ensure_utc(parsed)


def get_settings(request: Request) -> Settings:
    """Return the application Settings bound to FastAPI app state."""
    return cast(Settings, request.app.state.settings)


def get_http_transport(request: Request) -> httpx.BaseTransport | None:
    """Return an optional custom HTTP transport bound to FastAPI app state."""
    transport = getattr(request.app.state, "http_transport", None)
    return cast(httpx.BaseTransport | None, transport)


def get_db_session(request: Request) -> Iterator[Session]:
    """Yield a SQLAlchemy Session per request and close it on exit."""
    session_factory = cast(sessionmaker[Session], request.app.state.session_factory)
    with session_factory() as session:
        yield session


def get_optional_current_user(
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
) -> User | None:
    """Resolve the active authenticated User from the session cookie if valid."""
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        return None

    token_hash = hash_session_token(raw_token)
    stmt = (
        select(UserSession)
        .options(joinedload(UserSession.user))
        .where(UserSession.token_hash == token_hash)
    )
    user_session = session.execute(stmt).scalar_one_or_none()
    if user_session is None:
        return None

    if ensure_utc(user_session.expires_at) <= datetime.now(UTC):
        return None

    user = user_session.user
    if not user.is_active:
        return None

    return user


def require_authenticated_user(
    user: Annotated[User | None, Depends(get_optional_current_user)],
) -> User:
    """Require an active authenticated session (Admin or Viewer)."""
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def require_admin_user(
    user: Annotated[User, Depends(require_authenticated_user)],
) -> User:
    """Require an active authenticated Admin user."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user

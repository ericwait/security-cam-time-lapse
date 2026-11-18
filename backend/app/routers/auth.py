"""Authentication, first-run bootstrap, session cookie lifecycle, and password rotation."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.deps import (
    SESSION_COOKIE_NAME,
    SESSION_DURATION_DAYS,
    SESSION_MAX_AGE_SECONDS,
    get_db_session,
    get_optional_current_user,
    require_authenticated_user,
)
from app.models import User, UserSession
from app.schemas import (
    AuthStatusOut,
    BootstrapRequest,
    LoginRequest,
    PasswordChangeRequest,
    StatusOkOut,
    UserOut,
)
from app.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)
from app.services import user_to_out

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _issue_session_cookie(session: Session, response: Response, user_id: int) -> None:
    now = datetime.now(UTC)
    raw_token, token_hash = generate_session_token()
    session.add(
        UserSession(
            token_hash=token_hash,
            user_id=user_id,
            expires_at=now + timedelta(days=SESSION_DURATION_DAYS),
            created_at=now,
        )
    )
    session.commit()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )


@router.get("/status", response_model=AuthStatusOut)
def get_auth_status(
    session: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User | None, Depends(get_optional_current_user)],
) -> AuthStatusOut:
    """Return whether initial admin bootstrap is needed and the active session user."""
    user_count = session.scalar(select(func.count()).select_from(User)) or 0
    return AuthStatusOut(
        needs_bootstrap=user_count == 0,
        user=user_to_out(current_user) if current_user is not None else None,
    )


@router.post("/bootstrap", response_model=UserOut, status_code=201)
def bootstrap_initial_admin(
    payload: BootstrapRequest,
    response: Response,
    session: Annotated[Session, Depends(get_db_session)],
) -> UserOut:
    """Create the initial Admin account on first run and sign in immediately."""
    user_count = session.scalar(select(func.count()).select_from(User)) or 0
    if user_count > 0:
        raise HTTPException(status_code=409, detail="Bootstrap has already been completed")

    now = datetime.now(UTC)
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role="admin",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Bootstrap has already been completed",
        ) from None

    _issue_session_cookie(session, response, user.id)
    session.refresh(user)
    return user_to_out(user)


@router.post("/login", response_model=UserOut)
def login(
    payload: LoginRequest,
    response: Response,
    session: Annotated[Session, Depends(get_db_session)],
) -> UserOut:
    """Authenticate an active user and issue an HttpOnly session cookie."""
    user = session.scalar(select(User).where(User.username == payload.username))
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    _issue_session_cookie(session, response, user.id)
    return user_to_out(user)


@router.post("/logout", response_model=StatusOkOut)
def logout(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db_session)],
) -> StatusOkOut:
    """Revoke the current server-side session and clear the session cookie."""
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_token:
        token_hash = hash_session_token(raw_token)
        session.execute(delete(UserSession).where(UserSession.token_hash == token_hash))
        session.commit()

    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return StatusOkOut()


@router.post("/password", response_model=StatusOkOut)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_authenticated_user)],
) -> StatusOkOut:
    """Rotate the authenticated user's password and revoke other active sessions."""
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    current_user.password_hash = hash_password(payload.new_password)
    current_user.updated_at = datetime.now(UTC)

    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_token:
        current_hash = hash_session_token(raw_token)
        session.execute(
            delete(UserSession).where(
                UserSession.user_id == current_user.id,
                UserSession.token_hash != current_hash,
            )
        )
    else:
        session.execute(delete(UserSession).where(UserSession.user_id == current_user.id))

    session.commit()
    return StatusOkOut()

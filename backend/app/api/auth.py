"""User authentication and session endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.api.security import require_authenticated_user
from app.database import get_db
from app.models import AuthEvent, User, UserSession
from app.services.auth import (
    create_session,
    hash_password,
    normalize_email,
    session_token_hash,
    verify_password,
)


router = APIRouter(prefix="/auth", tags=["auth"])


class LoginCredentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=6, max_length=256)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = normalize_email(value)
        if "@" not in value or value.startswith("@"):  # Keep dependency-free validation conservative.
            raise ValueError("A valid email address is required")
        return value


class RegisterCredentials(LoginCredentials):
    username: str = Field(min_length=2, max_length=80)


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    role: str
    plan: str
    account_status: str


class AuthResponse(BaseModel):
    user: UserOut


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        plan=user.plan,
        account_status=user.account_status,
    )


def _cookie_kwargs() -> dict:
    return {
        "key": settings.auth_cookie_name,
        "httponly": True,
        "secure": settings.app_env == "production",
        "samesite": "lax",
        "max_age": settings.auth_session_days * 86400,
        "path": "/",
    }


async def _record_event(request: Request, db: AsyncSession, *, user_id: int | None, email: str, event_type: str, success: bool, failure_reason: str | None = None) -> None:
    db.add(
        AuthEvent(
            user_id=user_id,
            email_attempted=email,
            event_type=event_type,
            success=success,
            failure_reason=failure_reason,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    )


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterCredentials, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    if not settings.auth_allow_registration:
        raise HTTPException(status_code=404, detail="Registration is disabled")
    email = normalize_email(payload.email)
    existing = await db.scalar(select(User).where(User.email == email))
    if existing:
        await _record_event(request, db, user_id=existing.id, email=email, event_type="register", success=False, failure_reason="email_exists")
        await db.commit()
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(username=payload.username.strip(), email=email, password_hash=hash_password(payload.password), role="user", plan="free", account_status="active")
    db.add(user)
    await db.flush()
    token = await create_session(db, user)
    await _record_event(request, db, user_id=user.id, email=email, event_type="register", success=True)
    await db.commit()
    response.set_cookie(value=token, **_cookie_kwargs())
    return AuthResponse(user=_user_out(user))


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginCredentials, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    email = normalize_email(payload.email)
    user = await db.scalar(select(User).where(User.email == email))
    if not user or not verify_password(user.password_hash, payload.password):
        await _record_event(request, db, user_id=user.id if user else None, email=email, event_type="login", success=False, failure_reason="invalid_credentials")
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user.account_status != "active":
        await _record_event(request, db, user_id=user.id, email=email, event_type="login", success=False, failure_reason="account_inactive")
        await db.commit()
        raise HTTPException(status_code=403, detail="Account is not active")
    user.last_login_at = datetime.now(timezone.utc)
    token = await create_session(db, user)
    await _record_event(request, db, user_id=user.id, email=email, event_type="login", success=True)
    await db.commit()
    response.set_cookie(value=token, **_cookie_kwargs())
    return AuthResponse(user=_user_out(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get(settings.auth_cookie_name)
    if token:
        session = await db.scalar(select(UserSession).where(UserSession.token_hash == session_token_hash(token)))
        if session and session.revoked_at is None:
            session.revoked_at = datetime.now(timezone.utc)
            await db.commit()
    response.delete_cookie(settings.auth_cookie_name, path="/")


@router.get("/me", response_model=AuthResponse)
async def me(user: User = Depends(require_authenticated_user)):
    return AuthResponse(user=_user_out(user))

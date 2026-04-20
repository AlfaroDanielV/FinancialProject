"""User registration + identity endpoints (Phase 5a).

No password / no session / no email verification — channel tokens identify
the user. The shortcut_token is opaque and rotate-able; it's returned only
at registration and at rotation.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.enums import NotificationScope
from ..models.notification_rule import NotificationRule
from ..models.user import User
from ..schemas.users import (
    UserCreate,
    UserRegisterResponse,
    UserResponse,
)

router = APIRouter(prefix="/api/v1/users", tags=["users"])


# 48 bytes → ~64 char URL-safe string. Spec asks for ≥32 bytes of entropy.
_TOKEN_BYTES = 48


def _new_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


async def _seed_global_default_rule(user: User, db: AsyncSession) -> None:
    """Every user gets the same default advance-notice cadence at signup so
    the notification engine has something to resolve to before they tweak
    anything. Same shape as migration 0005's seeded row.
    """
    rule = NotificationRule(
        user_id=user.id,
        scope=NotificationScope.GLOBAL_DEFAULT.value,
        advance_days=[7, 3, 1, 0],
    )
    db.add(rule)


@router.post("/register", response_model=UserRegisterResponse, status_code=201)
async def register_user(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
):
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        phone_number=payload.phone_number,
        country=payload.country,
        timezone=payload.timezone,
        currency=payload.currency,
        locale=payload.locale,
        shortcut_token=_new_token(),
    )
    db.add(user)
    try:
        await db.flush()
        await _seed_global_default_rule(user, db)
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        # uq_users_email is the only realistic collision here; rotate-token
        # collisions are practically impossible at 48 bytes of entropy.
        if "uq_users_email" in str(e.orig):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya existe un usuario con ese email.",
            ) from e
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conflicto al registrar usuario.",
        ) from e
    await db.refresh(user)
    return user


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(current_user)):
    return user


@router.post("/me/rotate-shortcut-token", response_model=UserRegisterResponse)
async def rotate_shortcut_token(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Issue a fresh shortcut_token. The previous one is invalidated
    immediately — any caller still holding it gets 401 on the next request.
    """
    user.shortcut_token = _new_token()
    await db.commit()
    await db.refresh(user)
    return user

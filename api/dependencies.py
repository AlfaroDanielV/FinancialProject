"""Auth dependencies for Phase 5a.

Two resolvers:

- `current_user`: real path. Resolves the caller via `X-Shortcut-Token`.
  Falls back to the dev-only `X-User-Id` shim when the token isn't present.

- `current_user_via_token`: strict. Only accepts `X-Shortcut-Token`. Used
  by the iPhone Shortcut endpoint and the `/jobs/*` batch endpoints, which
  must work without dev tooling and must not be cross-user-spoofable.

`X-User-Id` is a stop-gap so we can hit the API from curl / docs / a future
admin tool while real auth (magic links / OAuth) is still pending. It WILL
be removed in Phase 6 (or Phase 5c if WhatsApp lands first). Do not build
features that depend on it surviving.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models.user import User


_AUTH_MISSING = "Falta autenticación: envíe X-Shortcut-Token o X-User-Id."
_TOKEN_INVALID = "Token inválido."
_USER_NOT_FOUND = "Usuario no encontrado."
_USER_SUSPENDED = "Usuario suspendido."
_TOKEN_REQUIRED = "Falta X-Shortcut-Token."


async def _user_by_token(token: str, db: AsyncSession) -> Optional[User]:
    result = await db.execute(
        select(User).where(User.shortcut_token == token)
    )
    return result.scalar_one_or_none()


async def _user_by_id(raw: str, db: AsyncSession) -> Optional[User]:
    try:
        uid = uuid.UUID(raw)
    except ValueError:
        return None
    result = await db.execute(select(User).where(User.id == uid))
    return result.scalar_one_or_none()


def _ensure_active(user: User) -> User:
    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=_USER_SUSPENDED
        )
    return user


async def current_user(
    db: AsyncSession = Depends(get_db),
    x_shortcut_token: Optional[str] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> User:
    """Resolve the caller via X-Shortcut-Token (preferred) or X-User-Id (dev shim)."""
    if x_shortcut_token:
        user = await _user_by_token(x_shortcut_token, db)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=_TOKEN_INVALID
            )
        return _ensure_active(user)

    if x_user_id:
        user = await _user_by_id(x_user_id, db)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_USER_NOT_FOUND,
            )
        return _ensure_active(user)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_MISSING
    )


async def current_user_via_token(
    db: AsyncSession = Depends(get_db),
    x_shortcut_token: Optional[str] = Header(default=None),
) -> User:
    """Strict resolver. Only X-Shortcut-Token; never accepts the dev shim."""
    if not x_shortcut_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_TOKEN_REQUIRED
        )
    user = await _user_by_token(x_shortcut_token, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_TOKEN_INVALID
        )
    return _ensure_active(user)

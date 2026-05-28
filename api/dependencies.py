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

Phase 6f B2: `current_user` also accepts `Authorization: Bearer <jwt>`.
The bearer JWT is the same HS256 token the `fa_session` cookie carries,
issued by `/api/v1/auth/magic-link/exchange`. Native clients (Expo) can't
share the HttpOnly cookie with axios calls, so they read the token from
the exchange response body and send it as a bearer header instead.
Resolution order: shortcut-token → bearer JWT → cookie → dev shim.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import get_db
from .models.user import User
from .services.auth.session import decode_session_jwt


_AUTH_MISSING = (
    "Falta autenticación: envíe X-Shortcut-Token, Authorization: Bearer, "
    "cookie de sesión o X-User-Id."
)
_TOKEN_INVALID = "Token inválido."
_USER_NOT_FOUND = "Usuario no encontrado."
_USER_SUSPENDED = "Usuario suspendido."
_TOKEN_REQUIRED = "Falta X-Shortcut-Token."


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    """Return the bare JWT from an `Authorization: Bearer <jwt>` header,
    or None if the header is missing or doesn't start with `Bearer `.

    The check is case-sensitive on the scheme per RFC 6750 §2.1 (clients
    canonicalize to `Bearer`), but we accept lowercase for flexibility
    since axios sometimes lower-cases."""
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


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
    authorization: Optional[str] = Header(default=None),
    session_token: Optional[str] = Cookie(
        default=None, alias=settings.session_cookie_name
    ),
    x_user_id: Optional[str] = Header(default=None),
) -> User:
    """Resolve caller auth in priority order.

    Order matters: Shortcut token stays the strict production API path;
    bearer JWT is the Phase 6f native client path; magic-link cookie is
    the SPA path; X-User-Id remains the dev shim.
    """
    if x_shortcut_token:
        user = await _user_by_token(x_shortcut_token, db)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=_TOKEN_INVALID
            )
        return _ensure_active(user)

    bearer = _extract_bearer(authorization)
    if bearer:
        claims = decode_session_jwt(bearer)
        if claims is None or not claims.get("sub"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=_TOKEN_INVALID
            )
        user = await _user_by_id(str(claims["sub"]), db)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_USER_NOT_FOUND,
            )
        return _ensure_active(user)

    if session_token:
        claims = decode_session_jwt(session_token)
        if claims is None or not claims.get("sub"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=_TOKEN_INVALID
            )
        user = await _user_by_id(str(claims["sub"]), db)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_USER_NOT_FOUND,
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

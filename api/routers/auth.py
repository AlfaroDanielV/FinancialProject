"""Phase 6d B3 — public auth endpoint for magic-link exchange.

Single public endpoint per Resolución 9.3: bot mints links via the service
directly; only the consumer (`/exchange`) is exposed.

On success: validates + atomically consumes the magic link, then sets an
HttpOnly session cookie containing a 4h JWT (Resolución 9.1). The cookie
is the only auth artifact the SPA holds; subsequent requests use it.
"""
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models.user import User
from ..schemas.auth import MagicLinkExchangeRequest, MagicLinkExchangeResponse
from ..services.auth.magic_link import GENERIC_REJECT, validate_and_consume
from ..services.auth.session import issue_session_jwt

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post(
    "/magic-link/exchange",
    response_model=MagicLinkExchangeResponse,
    status_code=200,
)
async def exchange_magic_link(
    payload: MagicLinkExchangeRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    consumed = await validate_and_consume(db, payload.token)
    if consumed is None:
        raise HTTPException(status_code=401, detail=GENERIC_REJECT)

    result = await db.execute(select(User).where(User.id == consumed.user_id))
    user = result.scalar_one_or_none()
    if user is None or user.status != "active":
        # The link was valid but the user vanished or was suspended
        # between mint and exchange. Still 401 with the generic message
        # so we don't leak account state.
        raise HTTPException(status_code=401, detail=GENERIC_REJECT)

    token = issue_session_jwt(user.id, consumed.jti)

    cookie_kwargs = {
        "key": settings.session_cookie_name,
        "value": token,
        "max_age": settings.session_cookie_ttl_s,
        "httponly": True,
        "secure": settings.session_cookie_secure,
        "samesite": "lax",
        "path": "/",
    }
    if settings.session_cookie_domain:
        cookie_kwargs["domain"] = settings.session_cookie_domain
    response.set_cookie(**cookie_kwargs)

    return MagicLinkExchangeResponse(
        user_id=user.id, email=user.email, full_name=user.full_name
    )

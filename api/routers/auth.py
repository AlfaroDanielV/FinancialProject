"""Phase 6d B3 — public auth endpoint for magic-link exchange.

Single public endpoint per Resolución 9.3: bot mints links via the service
directly; only the consumer (`/exchange`) is exposed.

On success: validates + atomically consumes the magic link, then sets an
HttpOnly session cookie containing a 4h JWT (Resolución 9.1). The cookie
is the only auth artifact the SPA holds; subsequent requests use it.

Phase 6f B2: the response body also returns `token` + `expires_at` so
native clients (Expo) can persist the JWT in secure storage and send
`Authorization: Bearer <token>`. The cookie path stays for SPA
backwards-compat; both auth surfaces co-exist until Phase 6f B16 retires
the SPA.

Phase 6f B3: device-code exchange added at
`POST /api/v1/auth/device-code/exchange`. Replaces magic-link UX as the
primary native login path because tapping links on mobile is friction-y.
Same JWT codec, same `current_user` resolution downstream; no cookie set
(the device-code path is native-only).
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models.user import User
from ..redis_client import get_redis
from ..schemas.auth import (
    DeviceCodeExchangeRequest,
    MagicLinkExchangeRequest,
    MagicLinkExchangeResponse,
)
from ..services.auth.device_code import consume_device_code
from ..services.auth.magic_link import GENERIC_REJECT, validate_and_consume
from ..services.auth.session import decode_session_jwt, issue_session_jwt


_DEVICE_CODE_REJECT = (
    "Código inválido o vencido. Pedile uno nuevo con /login en Telegram."
)

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
    # Read `exp` straight back from the JWT we just issued. Avoids a
    # second `datetime.now()` call that could drift microseconds away
    # from the cookie's actual expiry.
    claims = decode_session_jwt(token)
    assert claims is not None, "freshly issued JWT must decode"
    expires_at = int(claims["exp"])

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
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        token=token,
        expires_at=expires_at,
    )


@router.post(
    "/device-code/exchange",
    response_model=MagicLinkExchangeResponse,
    status_code=200,
)
async def exchange_device_code(
    payload: DeviceCodeExchangeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Phase 6f B3 — exchange a 6-char Telegram-issued code for a JWT.

    Atomic single-use; same JWT codec and lifetime as magic-link
    exchange. No cookie set — this path is native-only. Suspended
    users are rejected with the same generic 401 message so callers
    can't probe user state via the device-code endpoint.
    """
    redis: Redis = get_redis()
    user = await consume_device_code(code=payload.code, redis=redis, db=db)
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail=_DEVICE_CODE_REJECT)

    # The JWT `jti` ties a session to a unique identifier so a future
    # revocation flow can blacklist by jti. Magic-link exchange uses
    # the magic_link_tokens.jti for this; device codes have no durable
    # row to reference, so mint a fresh uuid4. The token is still
    # bounded by `exp`; explicit revocation can be added later if a
    # device is reported lost.
    token = issue_session_jwt(user.id, uuid.uuid4())
    claims = decode_session_jwt(token)
    assert claims is not None, "freshly issued JWT must decode"
    expires_at = int(claims["exp"])

    return MagicLinkExchangeResponse(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        token=token,
        expires_at=expires_at,
    )

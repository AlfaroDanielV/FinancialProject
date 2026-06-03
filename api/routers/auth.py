"""Phase 6d B3 — public auth endpoint for magic-link exchange.

Single public endpoint per Resolución 9.3: bot mints links via the service
directly; only the consumer (`/exchange`) is exposed.

On success: validates + atomically consumes the magic link and returns a
4h session JWT (`token` + `expires_at`) in the response body. Native
clients (Expo) persist it in secure storage and send
`Authorization: Bearer <token>`.

Phase 6f B16: the SPA `fa_session` HttpOnly cookie was removed with the
SPA. The exchange endpoint stays — the native `ledgercr://exchange` deep
link (B15) still consumes magic links via this path.

Phase 6f B3: device-code exchange added at
`POST /api/v1/auth/device-code/exchange`. Replaces magic-link UX as the
primary native login path because tapping links on mobile is friction-y.
Same JWT codec, same `current_user` resolution downstream; no cookie set
(the device-code path is native-only).
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    # Read `exp` straight back from the JWT we just issued.
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

"""Phase 6f B3 — device-login codes.

The native app doesn't have a clean magic-link UX (the operator's main
gripe with B3), so authentication on a new device goes:

1. Operator (already paired in Telegram) runs `/login` in the bot.
2. Bot mints a 6-character alphanumeric code (same alphabet as the
   Phase 5b pairing code — no 0/O/1/I/L) and stores it in Redis under
   `auth:device_code:{code}` → `user_id` with TTL 5 minutes.
3. Operator types the code into the app.
4. App posts the code to `POST /api/v1/auth/device-code/exchange`.
5. Backend atomically pops the Redis key (single-use), fetches the
   user, mints a session JWT — same `issue_session_jwt` the magic-link
   path uses — and returns it.

Side-by-side with magic-link exchange (Phase 6d B3 / 6f B2):

- Both endpoints terminate in `issue_session_jwt`. Identical token
  shape; identical `current_user` resolution downstream. Neither sets a
  cookie — the SPA `fa_session` cookie was removed at Phase 6f B16; both
  are bearer-token flows now.
- Magic links carry a `jti` from `magic_link_tokens.jti`. Device codes
  don't have a durable row to reference, so we mint a fresh `uuid4`
  as the JWT `jti`. The token still revokes by expiration; explicit
  revocation isn't built (same as magic-link sessions today).

Atomic pop semantics mirror `bot/pairing.py::consume_pairing_code` —
the `redis.get + redis.delete` is run as a pipeline so two simultaneous
exchanges of the same code can't both succeed.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User
from bot.redis_keys import DEVICE_CODE_TTL_S, device_code_key


log = logging.getLogger("api.services.auth.device_code")

# Same alphabet as `bot/pairing.py`: 31 unambiguous chars (no 0/O/1/I/L).
# 31^6 ≈ 887M codes; collisions in the 5-minute window are negligible
# for the dogfood use case and acceptable for the multi-user beta too.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LEN = 6


def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))


def normalize_code(raw: str) -> str:
    """Strip whitespace and uppercase the input. Mobile users may paste
    with leading/trailing spaces or in lowercase — fix it once at the
    boundary so the Redis key always matches."""
    return raw.strip().upper()


async def request_device_code(*, user: User, redis: Redis) -> str:
    """Mint a fresh device code for `user`. Returns the code (uppercase,
    6 chars). The caller is responsible for delivering the code to the
    user (via Telegram `/login` reply, in B3)."""
    code = _new_code()
    await redis.setex(device_code_key(code), DEVICE_CODE_TTL_S, str(user.id))
    return code


async def consume_device_code(
    *, code: str, redis: Redis, db: AsyncSession
) -> Optional[User]:
    """Single-use exchange: pops the Redis key atomically and returns
    the user, or None if the code is unknown / expired / already used.

    Atomicity matters: a slow exchange shouldn't allow a second tab
    (or a race against another exchanger) to also succeed. The Lua
    GETDEL primitive (added in Redis 6.2) is the right tool, but a
    `pipeline` with `get` + `delete` is equivalent for our purposes —
    only one of the two pipelines can observe the value before the
    other's delete lands.
    """
    key = device_code_key(normalize_code(code))
    async with redis.pipeline(transaction=True) as pipe:
        pipe.get(key)
        pipe.delete(key)
        results = await pipe.execute()
    raw = results[0]
    if not raw:
        return None
    try:
        uid = uuid.UUID(raw)
    except (ValueError, TypeError):
        log.warning("device_code_invalid_uuid_payload code=%s", code)
        return None
    result = await db.execute(select(User).where(User.id == uid))
    return result.scalar_one_or_none()

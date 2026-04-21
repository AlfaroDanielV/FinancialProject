"""Pairing-code lifecycle.

The REST endpoint issues a short alphanumeric code and stores it in Redis
keyed by the code (not the user) so the bot only has the code at /start
time. The bot validates, binds users.telegram_user_id, and deletes the
code. Code TTL is 5 minutes.

Ambiguous characters (0/O, 1/I/L) are excluded from the alphabet so the
user can type it without squinting.
"""
from __future__ import annotations

import secrets
import uuid
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user import User
from .redis_keys import PAIRING_TTL_S, pairing_key


_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
_CODE_LEN = 6


def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))


async def issue_pairing_code(
    *, user: User, redis: Redis
) -> str:
    """Mint a fresh code for the user. Any existing code is left to expire
    naturally — we don't track per-user codes because the code→user_id
    direction is all the bot needs. Collisions in the short code space are
    rare enough (30^6 ≈ 730M) that a single retry is overkill.
    """
    code = _new_code()
    await redis.setex(
        pairing_key(code), PAIRING_TTL_S, str(user.id)
    )
    return code


async def resolve_pairing_code(
    *, code: str, redis: Redis, db: AsyncSession
) -> Optional[User]:
    """Look up a code, return the User if valid. Does NOT delete the code —
    the caller does that after the binding succeeds, so a transient DB
    error doesn't burn the user's code."""
    val = await redis.get(pairing_key(code))
    if not val:
        return None
    try:
        uid = uuid.UUID(val)
    except ValueError:
        return None
    result = await db.execute(select(User).where(User.id == uid))
    return result.scalar_one_or_none()


async def consume_pairing_code(*, code: str, redis: Redis) -> None:
    await redis.delete(pairing_key(code))

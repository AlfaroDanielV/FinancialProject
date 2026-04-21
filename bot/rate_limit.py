"""Per-user message rate limit and daily LLM token budget.

Both are advisory — nothing prevents a determined attacker from bypassing
rate limits at the Telegram API level. The point is to protect the LLM
bill from a user looping on «test test test».
"""
from __future__ import annotations

import time
import uuid
from datetime import date

from redis.asyncio import Redis

from api.config import settings
from .redis_keys import (
    RATE_LIMIT_PER_WINDOW,
    RATE_WINDOW_S,
    rate_key,
    token_budget_key,
)


async def check_and_increment_rate(*, user_id: uuid.UUID, redis: Redis) -> bool:
    """Fixed-window rate limit with one counter per (user, minute). Cheaper
    than a sliding window and plenty for a single-user chat bot. Returns
    True if the message should proceed, False if the user is over the cap.
    """
    minute_bucket = int(time.time() // RATE_WINDOW_S)
    key = rate_key(user_id, minute_bucket)
    # INCR creates the key at 1 if missing. TTL needs to be set on the first
    # write — we set it unconditionally on every increment; Redis treats
    # EXPIRE on an existing key as a reset, which is fine for our purposes.
    n = await redis.incr(key)
    if n == 1:
        await redis.expire(key, RATE_WINDOW_S + 5)
    return n <= RATE_LIMIT_PER_WINDOW


async def check_token_budget(*, user_id: uuid.UUID, redis: Redis, today: date) -> bool:
    """True if the user still has budget. Does NOT increment — call
    `record_token_spend` after the LLM call returns so cache-read-heavy
    calls count correctly."""
    cap = settings.llm_daily_token_budget_per_user
    if cap <= 0:
        return True
    key = token_budget_key(user_id, today.strftime("%Y%m%d"))
    raw = await redis.get(key)
    spent = int(raw) if raw else 0
    return spent < cap


async def record_token_spend(
    *, user_id: uuid.UUID, redis: Redis, today: date, tokens: int
) -> None:
    if tokens <= 0:
        return
    key = token_budget_key(user_id, today.strftime("%Y%m%d"))
    new_total = await redis.incrby(key, tokens)
    if new_total == tokens:  # first write today
        # Expire slightly after midnight tomorrow; the exact time doesn't
        # matter because the key name includes yyyymmdd — TTL is just
        # janitorial.
        await redis.expire(key, 60 * 60 * 36)

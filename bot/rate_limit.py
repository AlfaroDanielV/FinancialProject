"""Per-user per-minute message rate limit.

The daily LLM token budget moved to api.services.budget in bloque 8.5
— it queries llm_extractions + llm_query_dispatches directly so the
DB is the source of truth instead of a parallel Redis counter that
could drift. Rate limit (this file) and budget (the service) remain
distinct concerns: rate limit caps requests/minute, budget caps
tokens/day.
"""
from __future__ import annotations

import time
import uuid

from redis.asyncio import Redis

from .redis_keys import RATE_LIMIT_PER_WINDOW, RATE_WINDOW_S, rate_key


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

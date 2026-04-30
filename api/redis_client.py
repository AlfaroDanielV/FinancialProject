"""Shared async Redis connection.

All Phase 5b durable state passes through here:

- `telegram:pairing:{code}` — pairing codes, TTL 300s
- `telegram:pending:{user_id}` — staged proposal awaiting confirm/edit, TTL 300s
- `telegram:last_action:{user_id}` — last committed action id for /undo, TTL 24h
- `telegram:rate:{user_id}:{minute}` — sliding-window rate counter
- `query_history:{user_id}` — query-layer conversation history, TTL 24h

aiogram's FSM is NOT used for any of the above — it's only allowed for
transient dialog bookkeeping inside a single handler. See the state storage
policy memo for why.
"""
from __future__ import annotations

from typing import Optional

from redis.asyncio import Redis, from_url

from .config import settings

_client: Optional[Redis] = None


def get_redis() -> Redis:
    """Return the shared async Redis client, creating it on first use.

    Reuses a single connection pool across the app. decode_responses=True
    so callers get `str` back from GET/HGET instead of `bytes` — every
    Phase 5b key stores text (JSON, UUIDs, codes, counters).
    """
    global _client
    if _client is None:
        _client = from_url(settings.redis_url, decode_responses=True)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None

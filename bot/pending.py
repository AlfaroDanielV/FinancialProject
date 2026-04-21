"""Pending action + last-action Redis plumbing.

Durable state only. aiogram's FSM is deliberately not used for either —
see the state storage policy memory for why.
"""
from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Optional

from redis.asyncio import Redis

from .redis_keys import (
    LAST_ACTION_TTL_S,
    PENDING_TTL_S,
    last_action_key,
    pending_key,
)


@dataclass
class PendingAction:
    """Staged proposal awaiting Sí / No / Editar. `short_id` is echoed into
    the inline keyboard callback payload so a stale button press (user had
    an old message on screen and tapped after a new proposal overwrote the
    key) is rejected instead of committing the wrong thing."""

    short_id: str
    action_type: str  # "log_expense" | "log_income"
    payload: dict[str, Any]
    summary_es: str

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "PendingAction":
        return cls(**json.loads(raw))


def new_short_id() -> str:
    """8-char opaque id. Embedded in the callback_data so tapping a stale
    button returns a non-matching id."""
    return secrets.token_urlsafe(6)[:8]


async def save_pending(
    *, user_id: uuid.UUID, pending: PendingAction, redis: Redis
) -> None:
    await redis.setex(pending_key(user_id), PENDING_TTL_S, pending.to_json())


async def load_pending(
    *, user_id: uuid.UUID, redis: Redis
) -> Optional[PendingAction]:
    raw = await redis.get(pending_key(user_id))
    if not raw:
        return None
    try:
        return PendingAction.from_json(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def clear_pending(*, user_id: uuid.UUID, redis: Redis) -> None:
    await redis.delete(pending_key(user_id))


async def save_last_action(
    *, user_id: uuid.UUID, action_type: str, record_id: uuid.UUID, redis: Redis
) -> None:
    payload = json.dumps({"action_type": action_type, "record_id": str(record_id)})
    await redis.setex(last_action_key(user_id), LAST_ACTION_TTL_S, payload)


async def load_last_action(
    *, user_id: uuid.UUID, redis: Redis
) -> Optional[dict[str, str]]:
    raw = await redis.get(last_action_key(user_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def clear_last_action(*, user_id: uuid.UUID, redis: Redis) -> None:
    await redis.delete(last_action_key(user_id))

"""Phase 6a — short-term conversation history for the query dispatcher.

Stores the last few `(user, assistant)` exchanges per user in Redis so
follow-ups like "y la semana pasada?" or "profundizá" have context.

Design notes (see docs/phase-6a-decisions.md for the why):

- Text-only (option A): we keep `{role, content, created_at}` per entry.
  Tool calls and tool results are NOT replayed. The auditable record of
  tool calls already lives in `llm_query_dispatches` (column
  `tools_used`); this module is a *prompt input*, not an audit log.
- Cap is 10 entries total (5 user/assistant round-trips). When the cap
  is exceeded, oldest entries are dropped.
- TTL 24h, rolling. Renewed on every `append_turn`.
- Errors do NOT persist a turn. The dispatcher only calls `append_turn`
  on successful completions.
- Ownership: query dispatcher only. The write dispatcher's pending
  state lives under `telegram:pending:{user_id}` and is unrelated.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis

HISTORY_TTL_S = 24 * 60 * 60
HISTORY_MAX_ENTRIES = 10


def history_key(user_id: uuid.UUID | str) -> str:
    """Redis key for a user's query conversation history.

    Note the prefix is `query_history`, not `telegram:` — the query
    dispatcher is channel-agnostic. A future WhatsApp or web frontend
    will reuse this same store.
    """
    return f"query_history:{user_id}"


class ConversationTurn(BaseModel):
    """One side of a conversational exchange.

    `tool_calls` and `tool_results` are reserved for a future option-B
    (full-replay) mode. They are absent in option A; treated as
    forward-compatible no-ops if present in older Redis values.
    """

    model_config = ConfigDict(extra="ignore")

    role: str = Field(pattern=r"^(user|assistant)$")
    content: str
    tool_calls: Optional[list[dict[str, Any]]] = None
    tool_results: Optional[list[dict[str, Any]]] = None
    created_at: str  # UTC ISO 8601


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _decode(raw: Optional[str]) -> list[ConversationTurn]:
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    out: list[ConversationTurn] = []
    for item in items:
        try:
            out.append(ConversationTurn.model_validate(item))
        except Exception:
            # Skip malformed entries silently — better than dropping the
            # whole history because one row got corrupted.
            continue
    return out


def _encode(turns: list[ConversationTurn]) -> str:
    return json.dumps(
        [t.model_dump(exclude_none=True) for t in turns],
        ensure_ascii=False,
    )


async def load_history(
    user_id: uuid.UUID | str, *, redis: Redis
) -> list[ConversationTurn]:
    """Return the user's history in oldest→newest order. Empty if no key."""
    raw = await redis.get(history_key(user_id))
    return _decode(raw)


async def append_turn(
    user_id: uuid.UUID | str,
    *,
    user_msg: str,
    assistant_msg: str,
    redis: Redis,
) -> list[ConversationTurn]:
    """Append a user→assistant exchange (2 entries) and refresh the TTL.

    Returns the persisted history (after truncation) so callers don't
    need a follow-up `load_history`.
    """
    existing = await load_history(user_id, redis=redis)
    now = _now_iso()
    existing.append(
        ConversationTurn(role="user", content=user_msg, created_at=now)
    )
    existing.append(
        ConversationTurn(role="assistant", content=assistant_msg, created_at=now)
    )
    if len(existing) > HISTORY_MAX_ENTRIES:
        existing = existing[-HISTORY_MAX_ENTRIES:]
    await redis.setex(history_key(user_id), HISTORY_TTL_S, _encode(existing))
    return existing


async def clear_history(user_id: uuid.UUID | str, *, redis: Redis) -> None:
    """Delete the history for `user_id`.

    Not currently called from the dispatcher — reserved for a future
    `/clear` Telegram command (out of scope for block 7).
    """
    await redis.delete(history_key(user_id))


def to_anthropic_messages(
    turns: list[ConversationTurn],
) -> list[dict[str, Any]]:
    """Convert turns to the dict shape Anthropic's messages API expects.

    Strictly text-only: each turn becomes `{role, content: <text>}`.
    Truncates to alternating user/assistant pairs starting with user, in
    case Redis somehow contains an orphan assistant entry at the head.
    """
    if not turns:
        return []
    # Drop leading assistant entries — Anthropic rejects a messages array
    # that starts with anything but `user`.
    while turns and turns[0].role != "user":
        turns = turns[1:]
    return [{"role": t.role, "content": t.content} for t in turns]

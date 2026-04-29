"""Phase 6a — bloque 9.4: /clear command handler.

The /clear command wipes the user's query conversation history (Redis
key `query_history:{user_id}`). It does NOT touch pending writes or
clarification state — those are owned by /cancel.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import handlers, messages_es


class _FakeUser:
    def __init__(self, user_id: uuid.UUID) -> None:
        self.id = user_id


class _FakeFromUser:
    def __init__(self, tg_id: int) -> None:
        self.id = tg_id


class _FakeMessage:
    """Stand-in for aiogram.types.Message — only the bits the handler reads."""

    def __init__(self, from_user_id: int) -> None:
        self.from_user = _FakeFromUser(from_user_id) if from_user_id else None
        self.answers: list[str] = []

    async def answer(self, text: str, reply_markup=None) -> None:
        self.answers.append(text)


@pytest.mark.asyncio
async def test_on_clear_calls_clear_history_with_user_id():
    user_id = uuid.uuid4()
    fake_user = _FakeUser(user_id)
    msg = _FakeMessage(from_user_id=99887766)

    fake_redis = MagicMock()

    async def fake_user_lookup(*, telegram_user_id, db):
        assert telegram_user_id == 99887766
        return fake_user

    captured = {}

    async def fake_clear_history(uid, *, redis):
        captured["user_id"] = uid
        captured["redis"] = redis

    fake_session = AsyncMock()
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch.object(handlers, "AsyncSessionLocal", lambda: fake_session_cm),
        patch.object(handlers, "user_by_telegram_id", fake_user_lookup),
        patch.object(handlers, "get_redis", lambda: fake_redis),
        patch.object(handlers, "clear_history", fake_clear_history),
    ):
        await handlers.on_clear(msg)

    assert captured["user_id"] == user_id
    assert captured["redis"] is fake_redis
    assert msg.answers == [messages_es.CONTEXT_CLEARED]


@pytest.mark.asyncio
async def test_on_clear_idempotent_when_no_history():
    """clear_history is a Redis DELETE — already idempotent. The handler
    must reply with the same confirmation regardless."""
    user_id = uuid.uuid4()
    fake_user = _FakeUser(user_id)
    msg = _FakeMessage(from_user_id=1)

    async def fake_user_lookup(**kwargs):
        return fake_user

    call_count = {"n": 0}

    async def fake_clear_history(uid, *, redis):
        call_count["n"] += 1
        # Simulate "no key" — function returns None either way.
        return None

    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch.object(handlers, "AsyncSessionLocal", lambda: fake_session_cm),
        patch.object(handlers, "user_by_telegram_id", fake_user_lookup),
        patch.object(handlers, "get_redis", lambda: MagicMock()),
        patch.object(handlers, "clear_history", fake_clear_history),
    ):
        await handlers.on_clear(msg)
        await handlers.on_clear(msg)

    assert call_count["n"] == 2
    assert msg.answers == [
        messages_es.CONTEXT_CLEARED,
        messages_es.CONTEXT_CLEARED,
    ]


@pytest.mark.asyncio
async def test_on_clear_unpaired_user_gets_pair_prompt():
    """Unpaired users get the standard pairing prompt — not the
    confirmation. /clear is meaningful only after pairing."""
    msg = _FakeMessage(from_user_id=42)

    async def fake_user_lookup(**kwargs):
        return None  # not paired

    clear_calls = {"n": 0}

    async def fake_clear_history(*args, **kwargs):
        clear_calls["n"] += 1

    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch.object(handlers, "AsyncSessionLocal", lambda: fake_session_cm),
        patch.object(handlers, "user_by_telegram_id", fake_user_lookup),
        patch.object(handlers, "get_redis", lambda: MagicMock()),
        patch.object(handlers, "clear_history", fake_clear_history),
    ):
        await handlers.on_clear(msg)

    assert clear_calls["n"] == 0  # never called for unpaired users
    assert msg.answers == [messages_es.PAIR_PROMPT]

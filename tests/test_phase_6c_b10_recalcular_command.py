"""Phase 6c B10 — /recalcular_memoria bot command tests.

Cooldown enforcement, success path (start + done messages), failure
path (start + failure message). Background task is awaited directly to
make the assertions deterministic — production code uses
asyncio.create_task which is fire-and-forget.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.insights.nightly_runner import NightlyRunStats
from bot import memory_handlers, messages_es


class _FakeFromUser:
    def __init__(self, tg_id: int) -> None:
        self.id = tg_id


class _FakeMessage:
    def __init__(self, from_user_id: int) -> None:
        self.from_user = _FakeFromUser(from_user_id)
        self.chat = MagicMock()
        self.chat.id = 99999
        self.answers: list[str] = []

    async def answer(self, text: str, reply_markup=None) -> None:
        self.answers.append(text)


class _FakeUser:
    def __init__(self, user_id: uuid.UUID) -> None:
        self.id = user_id


class _FakeRedis:
    def __init__(self, *, already_set: bool = False, ttl_seconds: int = 1800) -> None:
        self._already_set = already_set
        self._ttl = ttl_seconds

    async def set(self, key, value, ex=None, nx=None):
        if nx and self._already_set:
            return False
        return True

    async def ttl(self, key):
        return self._ttl

    async def delete(self, key):
        return None

    async def get(self, key):
        return None


# ── cooldown ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recalcular_memoria_cooldown_replies_with_minutes():
    user_id = uuid.uuid4()
    msg = _FakeMessage(from_user_id=42)
    fake_redis = _FakeRedis(already_set=True, ttl_seconds=1800)

    async def fake_user_lookup(*, telegram_user_id, db):
        return _FakeUser(user_id)

    with (
        patch.object(memory_handlers, "AsyncSessionLocal", lambda: AsyncMock()),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
        patch.object(memory_handlers, "get_redis", lambda: fake_redis),
    ):
        await memory_handlers.on_recalcular_memoria(msg)

    assert len(msg.answers) == 1
    assert "30" in msg.answers[0]  # 1800s / 60 = 30 min
    assert "Probá de nuevo en" in msg.answers[0]


# ── happy path ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recalcular_memoria_success_sends_started_then_done():
    """Drive the full pipeline by awaiting the spawned task."""
    user_id = uuid.uuid4()
    msg = _FakeMessage(from_user_id=42)
    fake_redis = _FakeRedis(already_set=False)

    async def fake_user_lookup(*, telegram_user_id, db):
        return _FakeUser(user_id)

    fake_bot = AsyncMock()
    sent: list[dict] = []

    async def fake_send_message(*, chat_id, text, parse_mode=None):
        sent.append({"chat_id": chat_id, "text": text})

    fake_bot.send_message = fake_send_message

    captured_tasks: list[asyncio.Task] = []
    real_create_task = asyncio.create_task

    def capturing_create_task(coro):
        task = real_create_task(coro)
        captured_tasks.append(task)
        return task

    with (
        patch.object(memory_handlers, "AsyncSessionLocal", lambda: AsyncMock()),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
        patch.object(memory_handlers, "get_redis", lambda: fake_redis),
        patch.object(memory_handlers.asyncio, "create_task", capturing_create_task),
        patch(
            "bot.memory_handlers.run_nightly_for_user",
            AsyncMock(
                return_value=NightlyRunStats(
                    user_id=user_id,
                    persisted_created=3,
                    persisted_updated=1,
                    persisted_reinforced=2,
                    gaps_emitted=1,
                )
            ),
        ),
    ):
        # Patch the lazy bot lookup inside the safe runner.
        with patch("bot.app.get_bot", lambda: fake_bot):
            await memory_handlers.on_recalcular_memoria(msg)
            for task in captured_tasks:
                await task

    # Ack reply.
    assert msg.answers == [messages_es.RECALCULAR_MEMORIA_STARTED]
    # Done message via bot.send_message.
    assert len(sent) == 1
    text = sent[0]["text"]
    # 3 created + 1 updated + 2 reinforced = 6 written.
    assert "6" in text
    assert "Listo, recalculé tu memoria" in text


# ── failure path ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recalcular_memoria_runner_failure_sends_failed_message():
    user_id = uuid.uuid4()
    msg = _FakeMessage(from_user_id=42)
    fake_redis = _FakeRedis(already_set=False)

    async def fake_user_lookup(*, telegram_user_id, db):
        return _FakeUser(user_id)

    fake_bot = AsyncMock()
    sent: list[dict] = []

    async def fake_send_message(*, chat_id, text, parse_mode=None):
        sent.append({"text": text})

    fake_bot.send_message = fake_send_message

    captured_tasks: list[asyncio.Task] = []
    real_create_task = asyncio.create_task

    def capturing_create_task(coro):
        task = real_create_task(coro)
        captured_tasks.append(task)
        return task

    async def boom(*args, **kwargs):
        raise RuntimeError("simulated compute failure")

    with (
        patch.object(memory_handlers, "AsyncSessionLocal", lambda: AsyncMock()),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
        patch.object(memory_handlers, "get_redis", lambda: fake_redis),
        patch.object(memory_handlers.asyncio, "create_task", capturing_create_task),
        patch("bot.memory_handlers.run_nightly_for_user", boom),
    ):
        with patch("bot.app.get_bot", lambda: fake_bot):
            await memory_handlers.on_recalcular_memoria(msg)
            for task in captured_tasks:
                await task

    assert msg.answers == [messages_es.RECALCULAR_MEMORIA_STARTED]
    assert len(sent) == 1
    assert sent[0]["text"] == messages_es.RECALCULAR_MEMORIA_FAILED


# ── unpaired user ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recalcular_memoria_unpaired_user_gets_pair_prompt():
    msg = _FakeMessage(from_user_id=42)

    async def fake_user_lookup(**kwargs):
        return None

    with (
        patch.object(memory_handlers, "AsyncSessionLocal", lambda: AsyncMock()),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_recalcular_memoria(msg)

    assert msg.answers == [messages_es.PAIR_PROMPT]

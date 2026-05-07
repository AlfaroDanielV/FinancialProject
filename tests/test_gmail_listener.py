"""Tests for the OAuth callback → Telegram bridge in bot/gmail_listener.

We exercise `_handle_message` directly. The pubsub `listen()` loop is a
thin shim around it and not worth mocking; if the dispatch is right, the
listen-loop is just plumbing.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import pytest

from bot import gmail_listener, gmail_onboarding, messages_es


class StubRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float | None]] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = (value, None)
        return True

    async def get(self, key):
        entry = self.store.get(key)
        if entry is None:
            return None
        return entry[0]

    async def delete(self, *keys):
        removed = 0
        for k in keys:
            if self.store.pop(k, None) is not None:
                removed += 1
        return removed


@dataclass
class _SentMessage:
    chat_id: int
    text: str


class StubBot:
    def __init__(self) -> None:
        self.sent: list[_SentMessage] = []

    async def send_message(self, *, chat_id: int, text: str) -> None:
        self.sent.append(_SentMessage(chat_id=chat_id, text=text))


@pytest.fixture(autouse=True)
def _patch_redis_and_bot(monkeypatch):
    redis = StubRedis()
    bot = StubBot()

    # bot.gmail_listener calls get_redis() and get_bot() at runtime.
    monkeypatch.setattr(gmail_listener, "get_redis", lambda: redis)
    monkeypatch.setattr(gmail_listener, "get_bot", lambda: bot)
    yield redis, bot


# ── success path ─────────────────────────────────────────────────────────────


async def test_success_advances_state_and_sends_prompt(
    _patch_redis_and_bot, monkeypatch
):
    """Post-addenda: success transitions to selecting_banks and delegates
    rendering of the prompt to gmail_handlers.send_bank_selection_prompt.
    The listener itself doesn't talk to bot.send_message anymore for the
    success path, so we patch the helper and assert it was called."""
    redis, _bot = _patch_redis_and_bot
    user_id = uuid.uuid4()
    chat_id = 9001

    await gmail_onboarding.begin(
        user_id=user_id, telegram_chat_id=chat_id, redis=redis
    )

    calls: list[dict] = []

    async def fake_send_prompt(*, bot, chat_id, user_id, redis):
        calls.append(
            {"chat_id": chat_id, "user_id": user_id, "redis": redis}
        )

    # The listener does a deferred import of gmail_handlers; patch the
    # symbol on the module so the import returns our fake.
    from bot import gmail_handlers as gh

    monkeypatch.setattr(gh, "send_bank_selection_prompt", fake_send_prompt)

    await gmail_listener._handle_message(
        f"gmail_callback:{user_id}",
        json.dumps({"status": "success"}),
    )

    state = await gmail_onboarding.get(user_id, redis)
    assert state is not None
    assert state.state == "selecting_banks"

    assert len(calls) == 1
    assert calls[0]["chat_id"] == chat_id
    assert calls[0]["user_id"] == user_id


# ── denied / error paths ─────────────────────────────────────────────────────


async def test_denied_clears_state_and_notifies(_patch_redis_and_bot):
    redis, bot = _patch_redis_and_bot
    user_id = uuid.uuid4()
    chat_id = 1234

    await gmail_onboarding.begin(
        user_id=user_id, telegram_chat_id=chat_id, redis=redis
    )

    await gmail_listener._handle_message(
        f"gmail_callback:{user_id}",
        json.dumps({"status": "denied", "detail": "access_denied"}),
    )

    assert await gmail_onboarding.get(user_id, redis) is None
    assert len(bot.sent) == 1
    assert bot.sent[0].text == messages_es.GMAIL_CALLBACK_DENIED


async def test_error_clears_state_and_notifies(_patch_redis_and_bot):
    redis, bot = _patch_redis_and_bot
    user_id = uuid.uuid4()
    chat_id = 222

    await gmail_onboarding.begin(
        user_id=user_id, telegram_chat_id=chat_id, redis=redis
    )

    await gmail_listener._handle_message(
        f"gmail_callback:{user_id}",
        json.dumps({"status": "error", "detail": "exchange_failed"}),
    )

    assert await gmail_onboarding.get(user_id, redis) is None
    assert bot.sent[0].text == messages_es.GMAIL_CALLBACK_ERROR


# ── defensive cases ──────────────────────────────────────────────────────────


async def test_no_session_drops_silently(_patch_redis_and_bot):
    """If the user already cleared state (e.g. /desconectar_gmail), the
    callback message arrives with nothing to advance. Don't crash, don't
    spam the chat."""
    _, bot = _patch_redis_and_bot
    await gmail_listener._handle_message(
        f"gmail_callback:{uuid.uuid4()}",
        json.dumps({"status": "success"}),
    )
    assert bot.sent == []


async def test_unknown_status_is_ignored(_patch_redis_and_bot):
    redis, bot = _patch_redis_and_bot
    user_id = uuid.uuid4()
    await gmail_onboarding.begin(
        user_id=user_id, telegram_chat_id=1, redis=redis
    )
    await gmail_listener._handle_message(
        f"gmail_callback:{user_id}",
        json.dumps({"status": "weird"}),
    )
    # No bot send, state untouched.
    assert bot.sent == []
    state = await gmail_onboarding.get(user_id, redis)
    assert state is not None
    assert state.state == "awaiting_oauth"


async def test_malformed_payload_is_swallowed(_patch_redis_and_bot):
    _, bot = _patch_redis_and_bot
    await gmail_listener._handle_message(
        f"gmail_callback:{uuid.uuid4()}", "not-json"
    )
    assert bot.sent == []


async def test_bad_channel_is_ignored(_patch_redis_and_bot):
    _, bot = _patch_redis_and_bot
    await gmail_listener._handle_message(
        "gmail_callback:not-a-uuid", json.dumps({"status": "success"})
    )
    assert bot.sent == []

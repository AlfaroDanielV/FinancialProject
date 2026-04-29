"""Integration tests for query dispatcher + history.

Verifies the contract:
- dispatcher prepends history to the LLM messages array
- successful runs append a (user, assistant) pair to Redis
- failed runs (LLMClientError, IterationCapExceeded, empty text) do NOT
  pollute the history
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from redis.asyncio import from_url as redis_from_url

from api.config import settings
from app.queries import dispatcher
from app.queries.history import (
    HISTORY_TTL_S,
    append_turn,
    history_key,
    load_history,
)
from app.queries.llm_client import (
    AnthropicQueryClient,
    QueryLLMClientError,
    QueryLLMResponse,
)
from app.queries.tools.base import _reset_registry_for_tests


@dataclass
class _Usage:
    input_tokens: int = 100
    output_tokens: int = 30
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _Text:
    text: str
    type: str = "text"


@dataclass
class _Response:
    content: list[Any]
    usage: _Usage = field(default_factory=_Usage)
    stop_reason: str = "end_turn"


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest_asyncio.fixture
async def redis_client():
    client = redis_from_url(settings.redis_url, decode_responses=True)
    yield client
    await client.aclose()


def _patch_dispatcher_session(monkeypatch, session):
    monkeypatch.setattr(
        dispatcher,
        "AsyncSessionLocal",
        lambda: _SessionContext(session),
    )


@pytest.mark.asyncio
async def test_dispatcher_appends_turn_on_successful_run(
    db_with_user, monkeypatch, redis_client
):
    session, user_id = db_with_user
    _patch_dispatcher_session(monkeypatch, session)
    _reset_registry_for_tests()

    captured_messages: list[list[dict]] = []

    class _FakeMessages:
        async def create(self, **kwargs):
            captured_messages.append(kwargs["messages"])
            return _Response(content=[_Text(text="Listo, ₡85.000.")])

    class _FakeAnthropic:
        def __init__(self):
            self.messages = _FakeMessages()

    dispatcher.set_query_llm_client(
        AnthropicQueryClient(api_key="", anthropic_client=_FakeAnthropic())
    )
    try:
        await dispatcher.handle(
            user_id=user_id,
            message_text="cuánto gasté",
            telegram_chat_id=42,
        )

        history = await load_history(user_id, redis=redis_client)
        assert len(history) == 2
        assert history[0].role == "user"
        assert history[0].content == "cuánto gasté"
        assert history[1].role == "assistant"
        assert history[1].content == "Listo, ₡85.000."
        # First call had no prior history.
        assert captured_messages[0] == [{"role": "user", "content": "cuánto gasté"}]
    finally:
        dispatcher.set_query_llm_client(None)
        await redis_client.delete(history_key(user_id))


@pytest.mark.asyncio
async def test_dispatcher_prepends_existing_history_to_llm_call(
    db_with_user, monkeypatch, redis_client
):
    session, user_id = db_with_user
    _patch_dispatcher_session(monkeypatch, session)
    _reset_registry_for_tests()

    # Pre-seed Redis with a prior turn.
    await append_turn(
        user_id,
        user_msg="qué gasté esta semana",
        assistant_msg="Llevás ₡85.000.",
        redis=redis_client,
    )

    captured_messages: list[list[dict]] = []

    class _FakeMessages:
        async def create(self, **kwargs):
            captured_messages.append(kwargs["messages"])
            return _Response(content=[_Text(text="La semana pasada: ₡42.000.")])

    class _FakeAnthropic:
        def __init__(self):
            self.messages = _FakeMessages()

    dispatcher.set_query_llm_client(
        AnthropicQueryClient(api_key="", anthropic_client=_FakeAnthropic())
    )
    try:
        await dispatcher.handle(
            user_id=user_id,
            message_text="y la pasada?",
            telegram_chat_id=42,
        )

        # The LLM saw the prior 2 turns + the new user message.
        assert captured_messages[0] == [
            {"role": "user", "content": "qué gasté esta semana"},
            {"role": "assistant", "content": "Llevás ₡85.000."},
            {"role": "user", "content": "y la pasada?"},
        ]

        # Now Redis has 4 entries.
        history = await load_history(user_id, redis=redis_client)
        assert len(history) == 4
    finally:
        dispatcher.set_query_llm_client(None)
        await redis_client.delete(history_key(user_id))


@pytest.mark.asyncio
async def test_dispatcher_does_not_persist_on_llm_error(
    db_with_user, monkeypatch, redis_client
):
    session, user_id = db_with_user
    _patch_dispatcher_session(monkeypatch, session)
    _reset_registry_for_tests()

    class _RaisingClient:
        async def run_query_loop(self, **kwargs):
            raise QueryLLMClientError("simulated_failure")

    dispatcher.set_query_llm_client(_RaisingClient())
    try:
        response = await dispatcher.handle(
            user_id=user_id,
            message_text="no respondas bien",
            telegram_chat_id=42,
        )
        # Bloque 8: dispatcher now routes errors through
        # delivery.handle_query_error. A QueryLLMClientError without an
        # explicit category falls into the "unknown" bucket → generic
        # admin-facing message.
        assert "Algo se rompió" in response

        # No history written.
        history = await load_history(user_id, redis=redis_client)
        assert history == []
    finally:
        dispatcher.set_query_llm_client(None)
        await redis_client.delete(history_key(user_id))


@pytest.mark.asyncio
async def test_dispatcher_does_not_persist_on_empty_response_text(
    db_with_user, monkeypatch, redis_client
):
    session, user_id = db_with_user
    _patch_dispatcher_session(monkeypatch, session)
    _reset_registry_for_tests()

    class _EmptyTextClient:
        async def run_query_loop(self, **kwargs):
            return QueryLLMResponse(
                text="",
                total_iterations=0,
                total_input_tokens=10,
                total_output_tokens=0,
                tools_used=[],
                duration_ms=5,
            )

    dispatcher.set_query_llm_client(_EmptyTextClient())
    try:
        await dispatcher.handle(
            user_id=user_id, message_text="vacío", telegram_chat_id=42
        )
        history = await load_history(user_id, redis=redis_client)
        assert history == []
    finally:
        dispatcher.set_query_llm_client(None)
        await redis_client.delete(history_key(user_id))


@pytest.mark.asyncio
async def test_dispatcher_renews_history_ttl_on_each_call(
    db_with_user, monkeypatch, redis_client
):
    session, user_id = db_with_user
    _patch_dispatcher_session(monkeypatch, session)
    _reset_registry_for_tests()

    class _OkClient:
        async def run_query_loop(self, **kwargs):
            return QueryLLMResponse(
                text="ok",
                total_iterations=0,
                total_input_tokens=10,
                total_output_tokens=2,
                tools_used=[],
                duration_ms=5,
            )

    dispatcher.set_query_llm_client(_OkClient())
    try:
        await dispatcher.handle(
            user_id=user_id, message_text="primero", telegram_chat_id=42
        )
        await redis_client.expire(history_key(user_id), 60)
        ttl_before = await redis_client.ttl(history_key(user_id))
        assert ttl_before <= 60

        await dispatcher.handle(
            user_id=user_id, message_text="segundo", telegram_chat_id=42
        )
        ttl_after = await redis_client.ttl(history_key(user_id))
        assert ttl_after > HISTORY_TTL_S - 30
    finally:
        dispatcher.set_query_llm_client(None)
        await redis_client.delete(history_key(user_id))

"""Tests for cache token metric parsing in app.queries.llm_client.

The Anthropic SDK exposes four token counters on each response:
input_tokens, output_tokens, cache_read_input_tokens,
cache_creation_input_tokens. The query dispatcher persists all four into
llm_query_dispatches. These tests pin the parsing on both shapes the
client may receive (dict from a JSON response or attribute-style from the
typed SDK object) and confirm that an end-to-end run propagates the
counters into QueryLLMResponse.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import select

from api.models.llm_query_dispatch import LLMQueryDispatch
from app.queries import dispatcher
from app.queries.llm_client import (
    AnthropicQueryClient,
    QueryLLMResponse,
    _usage,
)
from app.queries.tools._test_only import register_echo_tool
from app.queries.tools.base import _reset_registry_for_tests


# ── _usage() — direct unit ────────────────────────────────────────────────────


def test_usage_parses_object_with_cache_attrs() -> None:
    @dataclass
    class _Usage:
        input_tokens: int = 100
        output_tokens: int = 50
        cache_read_input_tokens: int = 800
        cache_creation_input_tokens: int = 300

    @dataclass
    class _Resp:
        usage: _Usage = field(default_factory=_Usage)

    out = _usage(_Resp())
    assert out == {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_input_tokens": 800,
        "cache_creation_input_tokens": 300,
    }


def test_usage_parses_dict_response() -> None:
    resp = {
        "usage": {
            "input_tokens": 12,
            "output_tokens": 6,
            "cache_read_input_tokens": 1500,
            "cache_creation_input_tokens": 0,
        }
    }
    out = _usage(resp)
    assert out["cache_read_input_tokens"] == 1500
    assert out["cache_creation_input_tokens"] == 0


def test_usage_handles_missing_cache_fields_gracefully() -> None:
    # Older SDK responses or non-cached calls don't include the cache keys.
    @dataclass
    class _UsageNoCache:
        input_tokens: int = 7
        output_tokens: int = 3

    @dataclass
    class _Resp:
        usage: _UsageNoCache = field(default_factory=_UsageNoCache)

    out = _usage(_Resp())
    assert out == {
        "input_tokens": 7,
        "output_tokens": 3,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


def test_usage_handles_missing_usage_block() -> None:
    @dataclass
    class _Resp:
        usage: Any = None

    out = _usage(_Resp())
    assert out == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


# ── End-to-end: cache fields propagate to QueryLLMResponse + DB row ───────────


@dataclass
class _Usage:
    input_tokens: int = 100
    output_tokens: int = 30
    cache_read_input_tokens: int = 1800
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


class _FakeMessages:
    async def create(self, **kwargs):
        return _Response(content=[_Text(text="Listo.")])


class _FakeAnthropic:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setenv("ENABLE_QUERY_TEST_TOOLS", "1")
    _reset_registry_for_tests()
    register_echo_tool()
    dispatcher.set_query_llm_client(
        AnthropicQueryClient(api_key="", anthropic_client=_FakeAnthropic())
    )
    yield
    dispatcher.set_query_llm_client(None)
    _reset_registry_for_tests()


@pytest.mark.asyncio
async def test_cache_tokens_persist_in_llm_query_dispatches(
    db_with_user, monkeypatch
):
    session, user_id = db_with_user
    monkeypatch.setattr(
        dispatcher,
        "AsyncSessionLocal",
        lambda: _SessionContext(session),
    )

    await dispatcher.handle(
        user_id=user_id,
        message_text="hola",
        telegram_chat_id=42,
    )

    rows = await session.execute(
        select(LLMQueryDispatch).where(LLMQueryDispatch.user_id == user_id)
    )
    row = rows.scalar_one()

    assert row.cache_read_input_tokens == 1800
    assert row.cache_creation_input_tokens == 0
    assert row.total_input_tokens == 100
    assert row.total_output_tokens == 30

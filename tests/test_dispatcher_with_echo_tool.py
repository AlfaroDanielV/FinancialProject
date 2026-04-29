from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import select

from app.queries import dispatcher
from app.queries.llm_client import AnthropicQueryClient
from app.queries.tools._test_only import register_echo_tool
from app.queries.tools.base import _reset_registry_for_tests
from api.models.llm_query_dispatch import LLMQueryDispatch


@dataclass
class _Usage:
    input_tokens: int = 5
    output_tokens: int = 4


@dataclass
class _ToolUse:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


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
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return _Response(
                content=[
                    _ToolUse(
                        id="tool-1",
                        name="echo",
                        input={"text": "hola"},
                    )
                ],
                stop_reason="tool_use",
            )
        return _Response(content=[_Text(text="Echo listo.")])


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
    fake = _FakeAnthropic()
    dispatcher.set_query_llm_client(
        AnthropicQueryClient(api_key="", anthropic_client=fake)
    )
    yield
    dispatcher.set_query_llm_client(None)
    _reset_registry_for_tests()


@pytest.mark.asyncio
async def test_dispatcher_executes_echo_tool_and_persists_usage(
    db_with_user,
    monkeypatch,
):
    session, user_id = db_with_user
    monkeypatch.setattr(
        dispatcher,
        "AsyncSessionLocal",
        lambda: _SessionContext(session),
    )

    response = await dispatcher.handle(
        user_id=user_id,
        message_text="probá echo",
        telegram_chat_id=123,
    )

    rows = await session.execute(
        select(LLMQueryDispatch).where(LLMQueryDispatch.user_id == user_id)
    )
    row = rows.scalar_one()

    assert response == "Echo listo."
    assert row.total_iterations == 1
    assert row.tools_used[0]["name"] == "echo"
    assert row.tools_used[0]["args_summary"] == {"text": "hola"}
    assert "error" not in row.tools_used[0]

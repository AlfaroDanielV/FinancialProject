from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.queries.llm_client import AnthropicQueryClient


@dataclass
class _Usage:
    input_tokens: int = 3
    output_tokens: int = 2


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
                    _ToolUse(id="tool-1", name="explode", input={"text": "x"})
                ],
                stop_reason="tool_use",
            )
        return _Response(content=[_Text(text="Ya pude seguir.")])


class _FakeAnthropic:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


@pytest.mark.asyncio
async def test_tool_error_is_returned_to_llm_and_recorded():
    fake = _FakeAnthropic()
    client = AnthropicQueryClient(api_key="", anthropic_client=fake)

    async def _execute(name: str, args: dict[str, Any], user_id: uuid.UUID):
        raise ValueError("boom")

    result = await client.run_query_loop(
        system_prompt="test",
        user_message="error",
        user_id=uuid.uuid4(),
        tools=[{"name": "explode", "description": "Explode", "input_schema": {}}],
        tool_executor=_execute,
        model="fake",
        max_iterations=4,
    )

    assert result.text == "Ya pude seguir."
    assert result.tools_used[0]["name"] == "explode"
    assert "ValueError: boom" == result.tools_used[0]["error"]

    second_call_messages = fake.messages.calls[1]["messages"]
    tool_result = second_call_messages[-1]["content"][0]
    assert tool_result["is_error"] is True
    assert tool_result["content"] == "ValueError: boom"

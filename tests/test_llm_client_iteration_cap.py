from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.queries.llm_client import AnthropicQueryClient, IterationCapExceeded
from app.queries.tools.base import _reset_registry_for_tests, query_tool


@dataclass
class _Usage:
    input_tokens: int = 1
    output_tokens: int = 1


@dataclass
class _ToolUse:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class _Response:
    content: list[Any]
    usage: _Usage = field(default_factory=_Usage)
    stop_reason: str = "tool_use"


class _FakeMessages:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Response(
            content=[
                _ToolUse(
                    id=f"tool-{len(self.calls)}",
                    name="echo",
                    input={"text": "x"},
                )
            ]
        )


class _FakeAnthropic:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


@pytest.fixture(autouse=True)
def _clean_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


@pytest.mark.asyncio
async def test_iteration_cap_exceeded_after_four_tool_iterations():
    @query_tool(name="echo", description="Echo")
    async def echo(text: str, user_id: uuid.UUID) -> dict[str, str]:  # noqa: ARG001
        return {"echo": text}

    client = AnthropicQueryClient(api_key="", anthropic_client=_FakeAnthropic())

    async def _execute(name: str, args: dict[str, Any], user_id: uuid.UUID):
        return await echo(**args, user_id=user_id)

    with pytest.raises(IterationCapExceeded) as exc:
        await client.run_query_loop(
            system_prompt="test",
            user_message="loop",
            user_id=uuid.uuid4(),
            tools=[{"name": "echo", "description": "Echo", "input_schema": {}}],
            tool_executor=_execute,
            model="fake",
            max_iterations=4,
        )

    assert exc.value.total_iterations == 4
    assert len(exc.value.tools_used) == 4

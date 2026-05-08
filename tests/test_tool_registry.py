from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.queries.tools.base import (
    _reset_registry_for_tests,
    execute_tool,
    list_tools_for_anthropic,
    query_tool,
)
from app.queries.tools import register_builtin_tools


@pytest.fixture(autouse=True)
def _clean_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


@pytest.mark.asyncio
async def test_tool_registry_hides_user_id_and_executes_with_injection():
    seen: dict[str, Any] = {}

    @query_tool(name="dummy", description="Dummy test tool")
    async def dummy(text: str, user_id: uuid.UUID) -> dict[str, Any]:
        seen["user_id"] = user_id
        return {"echo": text}

    specs = list_tools_for_anthropic()
    assert specs[0]["name"] == "dummy"
    assert "text" in specs[0]["input_schema"]["properties"]
    assert "user_id" not in specs[0]["input_schema"]["properties"]

    user_id = uuid.uuid4()
    result = await execute_tool("dummy", {"text": "hola"}, user_id)

    assert result == {"echo": "hola"}
    assert seen["user_id"] == user_id


def test_builtin_tool_registry_includes_memory_tool_before_compare_periods():
    register_builtin_tools()
    specs = list_tools_for_anthropic()
    names = [spec["name"] for spec in specs]

    assert "get_user_context" in names
    assert names[-1] == "compare_periods"
    assert names.index("get_user_context") < names.index("compare_periods")

    spec = next(spec for spec in specs if spec["name"] == "get_user_context")
    props = spec["input_schema"]["properties"]
    assert "user_id" not in props
    assert "insight_types" in props
    assert "max_insights" in props

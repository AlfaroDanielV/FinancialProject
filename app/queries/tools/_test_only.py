from __future__ import annotations

import os
import uuid
from typing import Any

from .base import query_tool


def register_echo_tool() -> None:
    if os.getenv("ENABLE_QUERY_TEST_TOOLS") != "1":
        raise RuntimeError("ENABLE_QUERY_TEST_TOOLS=1 is required for test tools")

    @query_tool(
        name="echo",
        description="Echo test tool. Only available in tests.",
    )
    async def echo(text: str, user_id: uuid.UUID) -> dict[str, Any]:  # noqa: ARG001
        return {"echo": text}

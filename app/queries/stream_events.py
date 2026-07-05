"""P10.S — streaming event types for the query LLM path.

The query dispatcher's LLM loop (`app/queries/llm_client.py::run_query_loop`)
accepts an optional `on_event` async callback. When set, it emits these
semantic events as the model generates — a `Delta` per chunk of assistant
text, and a `ToolBatch` when the model turns to tool use between narration
turns. `on_event=None` (Telegram, the classic `/chat/message` endpoint) is
byte-identical to the pre-streaming path: no events, one blocking call.

These are transport-neutral SEMANTIC events. Spanish/user-facing copy and the
SSE wire framing live in the route (`api/routers/chat.py`), never here — so
`ToolBatch.tool_names` is for server logs + a neutral status label, never
surfaced verbatim to the client.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Union


@dataclass(frozen=True)
class Delta:
    """A chunk of streamed assistant text (one or more tokens)."""

    text: str


@dataclass(frozen=True)
class ToolBatch:
    """Emitted when the model turns to tool use between narration turns.

    The client treats it as the signal to DISCARD any streamed draft (a
    turn's pre-tool 'preamble' text is not the final answer) and show a
    neutral 'working' status. `tool_names` is carried for server logs only;
    the route maps it to a neutral Spanish label — raw tool names never reach
    the client.
    """

    tool_names: list[str] = field(default_factory=list)


StreamEvent = Union[Delta, ToolBatch]
OnEvent = Callable[[StreamEvent], Awaitable[None]]

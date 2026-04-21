"""LLM client abstraction.

`LLMClient` is a tiny protocol so the runner can call either the real
Anthropic SDK or an in-memory fixture client without branching. Fixture
tests never hit the network — they inject `RecordedLLMResponse` objects
that mimic what the real API would return.

Prompt caching is wired at construction time, not "later": the tool schema
and system prompt both carry cache_control=ephemeral. The first call per
deploy incurs cache creation cost; every subsequent call reads from cache.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from anthropic import AsyncAnthropic
from anthropic import APIError as AnthropicAPIError
from anthropic import APITimeoutError as AnthropicTimeoutError


class LLMClientError(RuntimeError):
    """Raised when the LLM call fails in a way the bot should surface."""


@dataclass
class RecordedLLMResponse:
    """Fixture-recording shape. Mirrors what the runner needs from Anthropic
    so tests can freeze a previously-captured response.
    """

    tool_input: dict[str, Any]
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    stop_reason: str = "tool_use"
    extras: dict[str, Any] = field(default_factory=dict)


class LLMClient(Protocol):
    async def extract(
        self,
        *,
        user_message: str,
        prior_turns: list[dict[str, str]],
        system_prompt: str,
        tool: dict[str, Any],
        model: str,
        timeout_s: float,
    ) -> RecordedLLMResponse:  # pragma: no cover - protocol
        ...


class AnthropicLLMClient:
    """Real Anthropic client. Single tool, forced tool_choice, cache on."""

    def __init__(self, api_key: str):
        if not api_key:
            raise LLMClientError("ANTHROPIC_API_KEY missing; cannot start extractor.")
        self._client = AsyncAnthropic(api_key=api_key)

    async def extract(
        self,
        *,
        user_message: str,
        prior_turns: list[dict[str, str]],
        system_prompt: str,
        tool: dict[str, Any],
        model: str,
        timeout_s: float,
    ) -> RecordedLLMResponse:
        # Cache on the tool definition (large, rarely changes) and on the
        # system prompt (very large, never changes between calls). Anthropic
        # requires cache_control on the specific block, not on the message.
        cached_tool = {**tool, "cache_control": {"type": "ephemeral"}}
        system_blocks = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        messages: list[dict[str, Any]] = []
        for turn in prior_turns:
            messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": user_message})

        try:
            resp = await self._client.messages.create(
                model=model,
                max_tokens=512,
                system=system_blocks,
                tools=[cached_tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                messages=messages,
                timeout=timeout_s,
            )
        except AnthropicTimeoutError as e:
            raise LLMClientError(f"extractor_timeout: {e}") from e
        except AnthropicAPIError as e:
            raise LLMClientError(f"extractor_api_error: {e}") from e

        tool_block = _first_tool_use_block(resp)
        if tool_block is None:
            raise LLMClientError(
                f"extractor_no_tool_use: stop_reason={resp.stop_reason!r}"
            )

        usage = resp.usage
        return RecordedLLMResponse(
            tool_input=dict(tool_block.input),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_input_tokens=
                getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=
                getattr(usage, "cache_creation_input_tokens", 0) or 0,
            stop_reason=resp.stop_reason or "tool_use",
        )


def _first_tool_use_block(resp: Any) -> Optional[Any]:
    for block in getattr(resp, "content", []):
        if getattr(block, "type", None) == "tool_use":
            return block
    return None


class FixtureLLMClient:
    """Test double. Returns a pre-recorded response regardless of input.

    Used by fixture tests so they run deterministically and for free.
    Accepts a dict keyed by user_message for slightly smarter test setups.
    """

    def __init__(
        self,
        *,
        default: Optional[RecordedLLMResponse] = None,
        by_message: Optional[dict[str, RecordedLLMResponse]] = None,
    ):
        self._default = default
        self._by_message = by_message or {}

    async def extract(
        self,
        *,
        user_message: str,
        prior_turns: list[dict[str, str]],
        system_prompt: str,
        tool: dict[str, Any],
        model: str,
        timeout_s: float,
    ) -> RecordedLLMResponse:
        if user_message in self._by_message:
            return self._by_message[user_message]
        if self._default is not None:
            return self._default
        raise LLMClientError(
            f"FixtureLLMClient has no response for message: {user_message!r}"
        )

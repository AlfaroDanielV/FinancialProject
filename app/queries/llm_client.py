from __future__ import annotations

import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Optional

from anthropic import AsyncAnthropic
from anthropic import APIError as AnthropicAPIError
from anthropic import APITimeoutError as AnthropicTimeoutError
from anthropic import (
    AuthenticationError as AnthropicAuthError,
    BadRequestError as AnthropicBadRequestError,
    InternalServerError as AnthropicInternalError,
    NotFoundError as AnthropicNotFoundError,
    PermissionDeniedError as AnthropicPermissionError,
    RateLimitError as AnthropicRateLimitError,
)

from api.config import settings


# Error categories used by app/queries/delivery.handle_query_error to
# pick the user-facing message and log level. Kept as plain strings so
# tests can assert on them without importing typed enums.
ERR_TIMEOUT = "timeout"
ERR_RATE_LIMIT = "rate_limit"
ERR_SERVER_ERROR = "server_error"
ERR_CLIENT_ERROR = "client_error"
ERR_AUTH_ERROR = "auth_error"
ERR_UNKNOWN = "unknown"


class QueryLLMClientError(RuntimeError):
    """Raised when the query LLM call fails.

    `category` is one of the ERR_* constants above. The dispatcher
    surfaces this exception unchanged; `delivery.handle_query_error`
    maps category → Spanish user message + log level.
    """

    def __init__(self, message: str, *, category: str = ERR_UNKNOWN) -> None:
        super().__init__(message)
        self.category = category


@dataclass
class IterationCapExceeded(RuntimeError):
    total_iterations: int
    total_input_tokens: int
    total_output_tokens: int
    tools_used: list[dict[str, Any]]
    duration_ms: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __str__(self) -> str:
        return "query_iteration_cap_exceeded"


@dataclass
class QueryLLMResponse:
    text: str
    total_iterations: int
    total_input_tokens: int
    total_output_tokens: int
    tools_used: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


ToolExecutor = Callable[[str, dict[str, Any], uuid.UUID], Awaitable[dict[str, Any]]]


class AnthropicQueryClient:
    """Anthropic client for Phase 6a query tool use.

    Unlike the Phase 5b extractor, this client uses `tool_choice=auto` and
    supports repeated tool-use turns up to a configurable cap.
    """

    def __init__(self, api_key: str, anthropic_client: Any | None = None):
        if anthropic_client is not None:
            self._client = anthropic_client
            return
        if not api_key:
            raise QueryLLMClientError(
                "ANTHROPIC_API_KEY missing; cannot run query dispatcher."
            )
        self._client = AsyncAnthropic(api_key=api_key)

    async def run_query_loop(
        self,
        *,
        system_prompt: str,
        user_message: str,
        user_id: uuid.UUID,
        tools: list[dict[str, Any]],
        tool_executor: ToolExecutor,
        model: str | None = None,
        max_iterations: int | None = None,
        timeout_s: float = 20.0,
        prior_messages: list[dict[str, Any]] | None = None,
    ) -> QueryLLMResponse:
        """Run one tool-use loop.

        `prior_messages` lets the caller prepend conversation history.
        Each entry must already be in the Anthropic-API shape
        (`{"role": "user"|"assistant", "content": str}`); see
        `app.queries.history.to_anthropic_messages` for the converter.
        """
        model = model or settings.llm_query_model
        max_iterations = max_iterations or settings.llm_query_iteration_cap
        messages: list[dict[str, Any]] = list(prior_messages or [])
        messages.append({"role": "user", "content": user_message})
        tool_iterations = 0
        total_input = 0
        total_output = 0
        total_cache_read = 0
        total_cache_creation = 0
        tools_used: list[dict[str, Any]] = []
        started = time.perf_counter()

        while True:
            resp = await self._create_message(
                model=model,
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
                timeout_s=timeout_s,
            )
            usage = _usage(resp)
            total_input += usage["input_tokens"]
            total_output += usage["output_tokens"]
            total_cache_read += usage["cache_read_input_tokens"]
            total_cache_creation += usage["cache_creation_input_tokens"]

            tool_blocks = [b for b in _content(resp) if _block_type(b) == "tool_use"]
            if not tool_blocks:
                return QueryLLMResponse(
                    text=_text_from_response(resp),
                    total_iterations=tool_iterations,
                    total_input_tokens=total_input,
                    total_output_tokens=total_output,
                    tools_used=tools_used,
                    duration_ms=_elapsed_ms(started),
                    cache_read_input_tokens=total_cache_read,
                    cache_creation_input_tokens=total_cache_creation,
                )

            if tool_iterations >= max_iterations:
                raise IterationCapExceeded(
                    total_iterations=tool_iterations,
                    total_input_tokens=total_input,
                    total_output_tokens=total_output,
                    tools_used=tools_used,
                    duration_ms=_elapsed_ms(started),
                    cache_read_input_tokens=total_cache_read,
                    cache_creation_input_tokens=total_cache_creation,
                )

            messages.append({"role": "assistant", "content": _content(resp)})
            result_blocks: list[dict[str, Any]] = []
            for block in tool_blocks:
                result = await self._run_tool_block(
                    block=block,
                    user_id=user_id,
                    tool_executor=tool_executor,
                    tools_used=tools_used,
                )
                result_blocks.append(result)
            messages.append({"role": "user", "content": result_blocks})
            tool_iterations += 1

    async def _create_message(
        self,
        *,
        model: str,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout_s: float,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 700,
            "system": [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": messages,
            "timeout": timeout_s,
        }
        if tools:
            # Anthropic caps cache_control at 4 breakpoints per request. Mark
            # only the last tool — that single breakpoint covers the entire
            # tools block before it. The system prompt has its own breakpoint.
            tool_blocks = [dict(tool) for tool in tools]
            tool_blocks[-1]["cache_control"] = {"type": "ephemeral"}
            kwargs["tools"] = tool_blocks
            kwargs["tool_choice"] = {"type": "auto"}
        try:
            return await self._client.messages.create(**kwargs)
        except AnthropicTimeoutError as e:
            raise QueryLLMClientError(
                f"query_timeout: {e}", category=ERR_TIMEOUT
            ) from e
        except AnthropicRateLimitError as e:
            raise QueryLLMClientError(
                f"query_rate_limit: {e}", category=ERR_RATE_LIMIT
            ) from e
        except (AnthropicAuthError, AnthropicPermissionError) as e:
            # 401/403 — almost always a bad/missing key or revoked
            # billing access. Surface as auth_error so the user gets the
            # admin-facing message.
            raise QueryLLMClientError(
                f"query_auth_error: {e}", category=ERR_AUTH_ERROR
            ) from e
        except AnthropicInternalError as e:
            raise QueryLLMClientError(
                f"query_server_error: {e}", category=ERR_SERVER_ERROR
            ) from e
        except (AnthropicBadRequestError, AnthropicNotFoundError) as e:
            raise QueryLLMClientError(
                f"query_client_error: {e}", category=ERR_CLIENT_ERROR
            ) from e
        except AnthropicAPIError as e:
            # Catch-all for any APIError subclass we didn't enumerate above.
            # Distinguish 5xx from 4xx via status_code when available.
            status = getattr(e, "status_code", None)
            if isinstance(status, int) and 500 <= status < 600:
                category = ERR_SERVER_ERROR
            elif isinstance(status, int) and 400 <= status < 500:
                category = ERR_CLIENT_ERROR
            else:
                category = ERR_UNKNOWN
            raise QueryLLMClientError(
                f"query_api_error: {e}", category=category
            ) from e

    async def _run_tool_block(
        self,
        *,
        block: Any,
        user_id: uuid.UUID,
        tool_executor: ToolExecutor,
        tools_used: list[dict[str, Any]],
    ) -> dict[str, Any]:
        tool_name = _block_name(block)
        raw_args = _block_input(block)
        args = raw_args if isinstance(raw_args, dict) else {}
        started = time.perf_counter()
        usage_entry: dict[str, Any] = {
            "name": tool_name,
            "args_summary": _args_summary(args),
            "duration_ms": 0,
        }
        try:
            result = await tool_executor(tool_name, args, user_id)
            usage_entry["duration_ms"] = _elapsed_ms(started)
            tools_used.append(usage_entry)
            return {
                "type": "tool_result",
                "tool_use_id": _block_id(block),
                "content": json.dumps(result, default=str),
            }
        except Exception as e:  # noqa: BLE001 - tool errors are model-visible
            usage_entry["duration_ms"] = _elapsed_ms(started)
            usage_entry["error"] = f"{type(e).__name__}: {e}"
            tools_used.append(usage_entry)
            return {
                "type": "tool_result",
                "tool_use_id": _block_id(block),
                "content": usage_entry["error"],
                "is_error": True,
            }


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _content(resp: Any) -> list[Any]:
    if isinstance(resp, dict):
        return list(resp.get("content", []))
    return list(getattr(resp, "content", []))


def _usage(resp: Any) -> dict[str, int]:
    """Extract token + cache counters from the SDK response.

    Anthropic returns four token counts: `input_tokens` (uncached),
    `output_tokens`, `cache_read_input_tokens` (re-used from a prior
    cached prefix), and `cache_creation_input_tokens` (new tokens just
    written into the cache). The cache fields can be missing on older
    SDK versions or when caching is off — coerce to 0 instead of raising.
    """
    usage = resp.get("usage", {}) if isinstance(resp, dict) else getattr(resp, "usage", None)
    if usage is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
    if isinstance(usage, dict):
        return {
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "cache_read_input_tokens": int(
                usage.get("cache_read_input_tokens", 0) or 0
            ),
            "cache_creation_input_tokens": int(
                usage.get("cache_creation_input_tokens", 0) or 0
            ),
        }
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read_input_tokens": int(
            getattr(usage, "cache_read_input_tokens", 0) or 0
        ),
        "cache_creation_input_tokens": int(
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        ),
    }


def _block_type(block: Any) -> Optional[str]:
    return block.get("type") if isinstance(block, dict) else getattr(block, "type", None)


def _block_name(block: Any) -> str:
    return str(block.get("name") if isinstance(block, dict) else getattr(block, "name"))


def _block_id(block: Any) -> str:
    return str(block.get("id") if isinstance(block, dict) else getattr(block, "id"))


def _block_input(block: Any) -> Any:
    return block.get("input") if isinstance(block, dict) else getattr(block, "input", {})


def _text_from_response(resp: Any) -> str:
    parts: list[str] = []
    for block in _content(resp):
        if _block_type(block) == "text":
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", "")
            if text:
                parts.append(str(text))
    return "\n".join(parts).strip()


def _args_summary(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in args.items():
        if key == "user_id":
            continue
        if isinstance(value, str) and len(value) > 120:
            out[key] = value[:117] + "..."
        else:
            out[key] = value
    return out

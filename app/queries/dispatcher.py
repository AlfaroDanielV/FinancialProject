from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.database import AsyncSessionLocal
from api.models.llm_query_dispatch import LLMQueryDispatch
from api.models.user import User
from api.redis_client import get_redis
from api.services.budget import assert_within_budget

from .delivery import BudgetExceeded, handle_query_error
from .history import append_turn, load_history, to_anthropic_messages
from .llm_client import (
    AnthropicQueryClient,
    IterationCapExceeded,
    QueryLLMClientError,
    QueryLLMResponse,
)
from .prompts import build_system_prompt
from .tools.base import execute_tool, list_tools_for_anthropic
from .tools import register_builtin_tools


@dataclass
class DispatchOutcome:
    """Rich return value from `run_dispatch` — used by `/queries/test` and
    anyone else who needs counters alongside the user-facing text.

    `dispatch_id` is None when the user can't be resolved or when budget
    rejection short-circuited before a row was inserted (we don't log
    rejected requests — see assert_within_budget).
    """

    text: str
    dispatch_id: Optional[uuid.UUID] = None
    total_iterations: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    tools_used: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0
    error_category: Optional[str] = None  # "user_not_found"|"budget"|"iteration_cap"|"llm_error"

log = logging.getLogger("app.queries.dispatcher")

_USER_NOT_FOUND_RESPONSE = (
    "No te encuentro en el sistema. Reintentá en un momento."
)

_query_client: Optional[AnthropicQueryClient] = None


def get_query_llm_client() -> AnthropicQueryClient:
    global _query_client
    if _query_client is None:
        _query_client = AnthropicQueryClient(api_key=settings.anthropic_api_key)
    return _query_client


def set_query_llm_client(client: AnthropicQueryClient | None) -> None:
    global _query_client
    _query_client = client


async def handle(
    user_id: uuid.UUID,
    message_text: str,
    telegram_chat_id: int | None = None,
) -> str:
    """Backward-compat entry: returns just the user-facing text.

    Bot pipeline + the existing dispatcher tests rely on this str-returning
    shape. New callers (e.g. /api/v1/queries/test) use `run_dispatch` to
    get the full `DispatchOutcome` with iteration / token counters.
    """
    outcome = await run_dispatch(
        user_id=user_id,
        message_text=message_text,
        telegram_chat_id=telegram_chat_id,
    )
    return outcome.text


async def run_dispatch(
    *,
    user_id: uuid.UUID,
    message_text: str,
    telegram_chat_id: int | None = None,
) -> DispatchOutcome:
    """Run one read-only query dispatch and return rich metadata.

    Loads the user, builds the formal Phase 6a system prompt with date
    context anchored in the user's timezone, runs the tool-use loop, and
    logs one llm_query_dispatches row.
    """
    log.info(
        "query_dispatcher_invoked user_id=%s message_len=%d telegram_chat_id=%s",
        user_id,
        len(message_text),
        telegram_chat_id,
    )
    started = time.perf_counter()
    message_hash = _hash_message(message_text)
    register_builtin_tools()

    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            log.warning("query_dispatcher_user_not_found user_id=%s", user_id)
            return DispatchOutcome(
                text=_USER_NOT_FOUND_RESPONSE,
                error_category="user_not_found",
            )

        # Budget gate: pre-check before any LLM cost is incurred. We do
        # NOT log a llm_query_dispatches row for rejected requests — the
        # budget service already logs the rejection at INFO level and an
        # empty row would muddy future budget calcs.
        tz_name = getattr(user, "timezone", None) or "America/Costa_Rica"
        try:
            await assert_within_budget(
                user_id=user_id, db=db, tz_name=tz_name
            )
        except BudgetExceeded as e:
            return DispatchOutcome(
                text=handle_query_error(e, user_id=user_id),
                error_category="budget",
            )

        row = LLMQueryDispatch(
            user_id=user_id,
            message_hash=message_hash,
            tools_used=[],
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)

        redis = get_redis()
        prior_turns = await load_history(user_id, redis=redis)
        prior_messages = to_anthropic_messages(prior_turns)

        try:
            system_prompt = build_system_prompt(
                user=user,
                now=datetime.now(timezone.utc),
            )
            result = await get_query_llm_client().run_query_loop(
                system_prompt=system_prompt,
                user_message=message_text,
                user_id=user_id,
                tools=list_tools_for_anthropic(),
                tool_executor=execute_tool,
                model=settings.llm_query_model,
                max_iterations=settings.llm_query_iteration_cap,
                prior_messages=prior_messages,
            )
        except IterationCapExceeded as e:
            await _update_error(
                db=db,
                row=row,
                error=str(e),
                total_iterations=e.total_iterations,
                total_input_tokens=e.total_input_tokens,
                total_output_tokens=e.total_output_tokens,
                tools_used=e.tools_used,
                duration_ms=e.duration_ms,
                cache_read_input_tokens=e.cache_read_input_tokens,
                cache_creation_input_tokens=e.cache_creation_input_tokens,
            )
            return DispatchOutcome(
                text=handle_query_error(e, user_id=user_id, query_id=row.id),
                dispatch_id=row.id,
                total_iterations=e.total_iterations,
                total_input_tokens=e.total_input_tokens,
                total_output_tokens=e.total_output_tokens,
                cache_read_input_tokens=e.cache_read_input_tokens,
                cache_creation_input_tokens=e.cache_creation_input_tokens,
                tools_used=e.tools_used,
                duration_ms=e.duration_ms,
                error_category="iteration_cap",
            )
        except QueryLLMClientError as e:
            await _update_error(
                db=db,
                row=row,
                error=str(e),
                duration_ms=_elapsed_ms(started),
            )
            return DispatchOutcome(
                text=handle_query_error(e, user_id=user_id, query_id=row.id),
                dispatch_id=row.id,
                duration_ms=_elapsed_ms(started),
                error_category="llm_error",
            )

        await _update_success(db=db, row=row, result=result)
        text = result.text or (
            "Aún estoy aprendiendo a responder consultas financieras."
        )
        if result.text:
            # Persist only successful, non-empty exchanges. The empty-response
            # fallback above is a placeholder, not real conversation content.
            await append_turn(
                user_id,
                user_msg=message_text,
                assistant_msg=result.text,
                redis=redis,
            )
        return DispatchOutcome(
            text=text,
            dispatch_id=row.id,
            total_iterations=result.total_iterations,
            total_input_tokens=result.total_input_tokens,
            total_output_tokens=result.total_output_tokens,
            cache_read_input_tokens=result.cache_read_input_tokens,
            cache_creation_input_tokens=result.cache_creation_input_tokens,
            tools_used=result.tools_used,
            duration_ms=result.duration_ms,
        )


def _hash_message(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


async def _update_success(
    *,
    db: AsyncSession,
    row: LLMQueryDispatch,
    result: QueryLLMResponse,
) -> None:
    row.total_iterations = result.total_iterations
    row.total_input_tokens = result.total_input_tokens
    row.total_output_tokens = result.total_output_tokens
    row.cache_read_input_tokens = result.cache_read_input_tokens
    row.cache_creation_input_tokens = result.cache_creation_input_tokens
    row.tools_used = result.tools_used
    row.final_response_chars = len(result.text)
    row.duration_ms = result.duration_ms
    await db.commit()


async def _update_error(
    *,
    db: AsyncSession,
    row: LLMQueryDispatch,
    error: str,
    duration_ms: int,
    total_iterations: int = 0,
    total_input_tokens: int = 0,
    total_output_tokens: int = 0,
    tools_used: list[dict] | None = None,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> None:
    row.total_iterations = total_iterations
    row.total_input_tokens = total_input_tokens
    row.total_output_tokens = total_output_tokens
    row.cache_read_input_tokens = cache_read_input_tokens
    row.cache_creation_input_tokens = cache_creation_input_tokens
    row.tools_used = tools_used or []
    row.error = error
    row.duration_ms = duration_ms
    await db.commit()

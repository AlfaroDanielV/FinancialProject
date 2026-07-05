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
from api.models.llm_query_dispatch import LLMQueryDispatch
from api.models.user import User
from api.redis_client import get_redis
from api.services.budget import assert_within_budget
from api.services.envelopes import (
    count_unassigned_month_expenses,
    list_unattached_obligations,
)
from api.services.insights.extractor import (
    compact_transaction_context_from_tools,
    enqueue_insight_extraction,
)

from .delivery import BudgetExceeded, handle_query_error
from .history import append_turn, load_history, to_anthropic_messages
from .llm_client import (
    AnthropicQueryClient,
    IterationCapExceeded,
    QueryLLMClientError,
    QueryLLMResponse,
)
from .prompts import build_system_prompt
from .session import AsyncSessionLocal
from .stream_events import OnEvent
from .tools.base import execute_tool, list_tools_for_anthropic
from .tools import ADVISORY_TOOLSET, BASE_TOOLSET, register_builtin_tools


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
    # Native-only UI handoff hint (Telegram ignores it). The dispatcher — NOT the
    # LLM — sets this deterministically from which read tool ran. Shape:
    # {"screen": str, "prefill": dict}. Used to offer the 'Sin cuenta' assignment
    # screen after listing unassigned movements.
    open_screen: Optional[dict] = None

log = logging.getLogger("app.queries.dispatcher")

_USER_NOT_FOUND_RESPONSE = (
    "No te encuentro en el sistema. Reintentá en un momento."
)

# Deterministic native handoff: when the orphan-listing read tool ran, offer the
# 'Sin cuenta' assignment screen. The LLM never sets this — the dispatcher does,
# by inspecting which tool was used. Telegram ignores open_screen.
_ORPHAN_TOOL = "list_unassigned_transactions"
_ASSIGN_ACCOUNT_OPEN_SCREEN = {
    "screen": "assign_account",
    "prefill": {"filter": "no_account"},
}


def _handoff_open_screen(tools_used: list | None) -> Optional[dict]:
    """Deterministic native handoff: offer the 'Sin cuenta' assignment screen
    when the orphan-listing read tool ran. The LLM never decides this — it's
    derived from which tool was used. Returns None for every other answer."""
    used = {
        (t.get("name") if isinstance(t, dict) else t) for t in (tools_used or [])
    }
    if _ORPHAN_TOOL in used:
        return dict(_ASSIGN_ACCOUNT_OPEN_SCREEN)
    return None

_query_client: Optional[AnthropicQueryClient] = None


def get_query_llm_client() -> AnthropicQueryClient:
    global _query_client
    if _query_client is None:
        _query_client = AnthropicQueryClient(api_key=settings.anthropic_api_key)
    return _query_client


def set_query_llm_client(client: AnthropicQueryClient | None) -> None:
    global _query_client
    _query_client = client


# B4 — deterministic "tenés gastos fijos sin sobre" suggestion. Fired by the
# dispatcher (NOT the LLM) after a cashflow tool runs and finds obligations with
# no envelope, rate-limited once per conversation window. The LLM never decides
# whether to suggest; it only narrates its own answer.
_CASHFLOW_TOOLS = frozenset({"assess_purchase", "get_savings_capacity", "assess_goal"})
_ATTACH_SUGGEST_KEY = "chat:fixed_expense_suggested:{user_id}"
_ATTACH_SUGGEST_TTL_S = 3600  # ~ one conversation window; expires on its own

# B6 — deterministic "tenés N gastos sin sobre" suggestion. Same mechanism as the
# attach nudge above (own once-per-conversation key), but it counts current-month
# expenses with no envelope. The dispatcher decides; the LLM never does.
_UNASSIGNED_SUGGEST_KEY = "chat:unassigned_expenses_suggested:{user_id}"
_UNASSIGNED_SUGGEST_TTL_S = 3600


async def _maybe_append_attach_suggestion(
    text: str, *, db: AsyncSession, user: User, redis, tools_used: list
) -> str:
    """Append the canned attach suggestion to the reply when (a) the answer used
    a cashflow tool, (b) the user has unattached fixed expenses, and (c) we
    haven't already suggested this conversation. Deterministic + rate-limited.

    ``tools_used`` is the dispatcher's list of per-tool usage dicts (each carries
    a ``name`` key), not a list of names."""
    used_names = {
        (t.get("name") if isinstance(t, dict) else t) for t in (tools_used or [])
    }
    if not (used_names & _CASHFLOW_TOOLS):
        return text
    key = _ATTACH_SUGGEST_KEY.format(user_id=user.id)
    if await redis.get(key):
        return text
    unattached = await list_unattached_obligations(db, user_id=user.id)
    if not unattached:
        return text
    await redis.setex(key, _ATTACH_SUGGEST_TTL_S, "1")
    names = ", ".join(name for name, _amount, _src in unattached[:5])
    return (
        f"{text}\n\nDe paso: tenés {len(unattached)} gasto(s) fijo(s) sin sobre "
        f"asignado ({names}). Asignalos a un sobre para que tu presupuesto refleje "
        "tu situación real."
    )


async def _maybe_append_unassigned_suggestion(
    text: str, *, db: AsyncSession, user: User, redis, tools_used: list
) -> str:
    """Append a gentle "tenés N gastos sin sobre" nudge when (a) the answer used a
    cashflow tool, (b) the user has ≥ 1 current-month expense with no envelope,
    and (c) we haven't already suggested this conversation. Deterministic +
    rate-limited, exactly like `_maybe_append_attach_suggestion`. The LLM never
    decides to suggest; it only narrates its own answer."""
    used_names = {
        (t.get("name") if isinstance(t, dict) else t) for t in (tools_used or [])
    }
    if not (used_names & _CASHFLOW_TOOLS):
        return text
    key = _UNASSIGNED_SUGGEST_KEY.format(user_id=user.id)
    if await redis.get(key):
        return text
    count = await count_unassigned_month_expenses(db, user=user)
    if count < 1:
        return text
    await redis.setex(key, _UNASSIGNED_SUGGEST_TTL_S, "1")
    return (
        f"{text}\n\nTenés {count} gasto(s) sin sobre este mes — asignalos en "
        "30 segundos para que tu presupuesto refleje lo real."
    )


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
    advisory: bool = False,
    on_event: OnEvent | None = None,
) -> DispatchOutcome:
    """Run one read-only query dispatch and return rich metadata.

    Loads the user, builds the formal Phase 6a system prompt with date
    context anchored in the user's timezone, runs the tool-use loop, and
    logs one llm_query_dispatches row.

    P10 B2/B3: `advisory=True` (resolved per turn by `bot/advisory.py`)
    swaps in the advisory persona prompt, the ADVISORY_TOOLSET allowlist and
    the higher iteration cap. The write path is untouched either way — the
    advisory mode is a read-only persona variant, never a new pipeline.
    """
    log.info(
        "query_dispatcher_invoked user_id=%s message_len=%d telegram_chat_id=%s "
        "advisory=%s",
        user_id,
        len(message_text),
        telegram_chat_id,
        advisory,
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
                advisory=advisory,
            )
            # P10 B3: explicit per-mode allowlist (the global-registry trap) —
            # normal turns keep the byte-locked BASE_TOOLSET wire order;
            # advisory turns add the assessment/framing tools. Filtering
            # preserves registry order, so compare_periods stays the cache
            # breakpoint anchor in BOTH modes.
            toolset = ADVISORY_TOOLSET if advisory else BASE_TOOLSET
            result = await get_query_llm_client().run_query_loop(
                system_prompt=system_prompt,
                user_message=message_text,
                user_id=user_id,
                tools=list_tools_for_anthropic(allowed=toolset),
                tool_executor=execute_tool,
                model=settings.llm_query_model,
                max_iterations=(
                    settings.llm_advisory_iteration_cap
                    if advisory
                    else settings.llm_query_iteration_cap
                ),
                prior_messages=prior_messages,
                on_event=on_event,
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
        except Exception as e:
            # Catch-all so an unanticipated failure (build_system_prompt, a
            # tool-loop edge case, anything not wrapped above) returns a
            # handled Spanish message instead of bubbling to a raw 500.
            # handle_query_error logs it as `unhandled_query_exception`.
            try:
                await _update_error(
                    db=db, row=row, error=str(e), duration_ms=_elapsed_ms(started)
                )
            except Exception:
                log.exception("query_update_error_failed user_id=%s", user_id)
            return DispatchOutcome(
                text=handle_query_error(e, user_id=user_id, query_id=row.id),
                dispatch_id=row.id,
                duration_ms=_elapsed_ms(started),
                error_category="unhandled",
            )

        text = result.text or (
            "Aún estoy aprendiendo a responder consultas financieras."
        )
        # The LLM already produced the answer. Persistence + the attach nudge
        # are side effects: if any of them hiccups (DB/Redis transient, a tool
        # context edge case), we MUST still return the answer — never turn a
        # good response into an error. "No silent failures": logged loudly.
        try:
            await _update_success(db=db, row=row, result=result)
            if result.text:
                # Persist only successful, non-empty exchanges. The empty-
                # response fallback above is a placeholder, not real content.
                history_after = await append_turn(
                    user_id,
                    user_msg=message_text,
                    assistant_msg=result.text,
                    redis=redis,
                )
                enqueue_insight_extraction(
                    user_id=user_id,
                    conversation_window=history_after,
                    transaction_context=compact_transaction_context_from_tools(
                        result.tools_used
                    ),
                    source_event="post_query",
                    origin_dispatch_id=row.id,
                )
                # B4: ephemeral attach nudge — appended to the RETURNED text
                # only, never persisted to history.
                text = await _maybe_append_attach_suggestion(
                    text, db=db, user=user, redis=redis, tools_used=result.tools_used
                )
                # B6: ephemeral "gastos sin sobre" nudge — same ephemeral,
                # rate-limited mechanism (own key). Both can fire in one turn if
                # the user has BOTH unattached obligations AND unassigned
                # expenses; each then stays silent for the rest of the window.
                text = await _maybe_append_unassigned_suggestion(
                    text, db=db, user=user, redis=redis, tools_used=result.tools_used
                )
        except Exception:
            log.exception(
                "query_post_success_side_effects_failed user_id=%s query_id=%s",
                user_id,
                row.id,
            )
        return DispatchOutcome(
            text=text,
            dispatch_id=row.id,
            # Deterministic UI handoff (not the LLM): offer the 'Sin cuenta'
            # assignment screen when the orphan-listing tool ran.
            open_screen=_handoff_open_screen(result.tools_used),
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

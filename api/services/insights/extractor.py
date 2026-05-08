"""LLM-extracted user-memory insights for Phase 6c B4.

This writer is deliberately separate from computed insights:
- it only reads a caller-supplied conversation window and transaction
  context summary;
- it never queries ledger aggregates;
- it accepts only the LLM-allowed insight types; and
- any validation failure discards the whole output.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.database import AsyncSessionLocal
from api.models.llm_query_dispatch import LLMQueryDispatch
from api.services.llm_extractor import (
    AnthropicLLMClient,
    LLMClient,
    LLMClientError,
)
from api.schemas.insights import (
    LLM_EXTRACTABLE_TYPES,
    InsightContent,
    validate_insight_content,
)
from prompts.insight_extractor import SYSTEM_PROMPT, TOOL_DEFINITION

from .persister import PersistStats, persist_insights

log = logging.getLogger("api.services.insights.extractor")

_DEFAULT_TIMEOUT_S = 8.0
_MAX_WINDOW_MESSAGES = 10
_LLM_CONFIDENCE_CAP = Decimal("0.85")

InsightSourceEvent = Literal["post_query", "post_clarification", "manual"]
SessionFactory = Callable[[], Any]

_insight_llm_client: LLMClient | None = None


@dataclass
class InsightExtractionRun:
    insights: list[InsightContent] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    duration_ms: int = 0
    estimated_cost_usd: Decimal = Decimal("0.000000")
    error: str | None = None


@dataclass
class InsightExtractionJobResult:
    run_id: uuid.UUID | None
    stats: PersistStats
    insights: list[InsightContent]
    error: str | None = None


class _LLMInsightCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "stated_preference",
        "stated_goal",
        "archetype",
        "risk_posture",
        "decision_style",
        "financial_literacy",
    ]
    confidence: Decimal = Field(..., ge=0, le=_LLM_CONFIDENCE_CAP)
    content: dict[str, Any]


class _LLMInsightOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insights: list[_LLMInsightCandidate] = Field(default_factory=list, max_length=6)


def get_insight_llm_client() -> LLMClient:
    global _insight_llm_client
    if _insight_llm_client is None:
        _insight_llm_client = AnthropicLLMClient(api_key=settings.anthropic_api_key)
    return _insight_llm_client


def set_insight_llm_client(client: LLMClient | None) -> None:
    global _insight_llm_client
    _insight_llm_client = client


async def extract_insights(
    user_id: uuid.UUID,
    conversation_window: Sequence[Any],
    transaction_context: str | dict[str, Any] | None,
    *,
    client: LLMClient | None = None,
    model: str | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> list[InsightContent]:
    """Return validated LLM-extracted insights, or [] on failure.

    This public function has no side effects. The background job below
    handles persistence and cost logging for production hooks.
    """
    run = await _extract_insights_with_usage(
        user_id=user_id,
        conversation_window=conversation_window,
        transaction_context=transaction_context,
        client=client,
        model=model,
        timeout_s=timeout_s,
    )
    return run.insights


def enqueue_insight_extraction(
    *,
    user_id: uuid.UUID,
    conversation_window: Sequence[Any],
    transaction_context: str | dict[str, Any] | None = None,
    source_event: InsightSourceEvent,
    origin_dispatch_id: uuid.UUID | None = None,
    client: LLMClient | None = None,
    model: str | None = None,
    enabled: bool | None = None,
    session_factory: SessionFactory = AsyncSessionLocal,
) -> asyncio.Task[InsightExtractionJobResult] | None:
    """Schedule extraction in the current event loop without blocking reply.

    Local/dev default is controlled by INSIGHTS_EXTRACTOR_ENABLED. Tests can
    pass a fixture client and `enabled=True` for deterministic coverage.
    """
    should_run = settings.insights_extractor_enabled if enabled is None else enabled
    if not should_run:
        log.debug("insight_extractor_skip disabled user_id=%s", user_id)
        return None
    if client is None and not settings.anthropic_api_key:
        log.info("insight_extractor_skip missing_api_key user_id=%s", user_id)
        return None

    task = asyncio.create_task(
        run_insight_extraction_job(
            user_id=user_id,
            conversation_window=list(conversation_window),
            transaction_context=transaction_context,
            source_event=source_event,
            origin_dispatch_id=origin_dispatch_id,
            client=client,
            model=model,
            session_factory=session_factory,
        )
    )
    task.add_done_callback(_log_task_result)
    return task


async def run_insight_extraction_job(
    *,
    user_id: uuid.UUID,
    conversation_window: Sequence[Any],
    transaction_context: str | dict[str, Any] | None,
    source_event: InsightSourceEvent,
    origin_dispatch_id: uuid.UUID | None = None,
    client: LLMClient | None = None,
    model: str | None = None,
    session_factory: SessionFactory = AsyncSessionLocal,
) -> InsightExtractionJobResult:
    """Run extractor, log usage, persist insights, and link origin row."""
    async with session_factory() as session:
        run = await _extract_insights_with_usage(
            user_id=user_id,
            conversation_window=conversation_window,
            transaction_context=transaction_context,
            client=client,
            model=model,
        )
        run_id = await _log_extractor_run(
            session=session,
            user_id=user_id,
            run=run,
            conversation_window=conversation_window,
            source_event=source_event,
            model=model or settings.llm_extraction_model,
        )
        stats = PersistStats()
        if run.insights:
            stats = await persist_insights(
                session,
                user_id=user_id,
                insights=run.insights,
                source="llm_extracted",
            )
        if origin_dispatch_id is not None and run_id is not None:
            origin = await session.get(LLMQueryDispatch, origin_dispatch_id)
            if origin is not None:
                origin.extractor_run_id = run_id
        await session.commit()
        return InsightExtractionJobResult(
            run_id=run_id,
            stats=stats,
            insights=run.insights,
            error=run.error,
        )


async def _extract_insights_with_usage(
    *,
    user_id: uuid.UUID,
    conversation_window: Sequence[Any],
    transaction_context: str | dict[str, Any] | None,
    client: LLMClient | None,
    model: str | None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> InsightExtractionRun:
    effective_model = model or settings.llm_extraction_model
    effective_client = client
    started = time.perf_counter()
    try:
        if effective_client is None:
            effective_client = get_insight_llm_client()
        raw = await effective_client.extract(
            user_message=_build_user_message(
                user_id=user_id,
                conversation_window=conversation_window,
                transaction_context=transaction_context,
            ),
            prior_turns=[],
            system_prompt=SYSTEM_PROMPT,
            tool=TOOL_DEFINITION,
            model=effective_model,
            timeout_s=timeout_s,
        )
    except LLMClientError as e:
        log.warning(
            "insight_extractor_transport_failure user_id=%s err=%s",
            user_id,
            type(e).__name__,
        )
        return InsightExtractionRun(
            duration_ms=_elapsed_ms(started),
            error=f"transport:{type(e).__name__}",
        )

    duration_ms = _elapsed_ms(started)
    cost = _estimate_cost_usd(
        input_tokens=raw.input_tokens,
        output_tokens=raw.output_tokens,
        cache_read_input_tokens=raw.cache_read_input_tokens,
        cache_creation_input_tokens=raw.cache_creation_input_tokens,
    )
    try:
        insights = _validate_llm_output(raw.tool_input)
    except (ValidationError, ValueError) as e:
        log.warning(
            "insight_extractor_validation_failure user_id=%s message_hash=%s errors=%s",
            user_id,
            _hash_text(_context_for_hash(conversation_window)),
            _safe_validation_errors(e),
        )
        return InsightExtractionRun(
            input_tokens=raw.input_tokens,
            output_tokens=raw.output_tokens,
            cache_read_input_tokens=raw.cache_read_input_tokens,
            cache_creation_input_tokens=raw.cache_creation_input_tokens,
            duration_ms=duration_ms,
            estimated_cost_usd=cost,
            error="validation",
        )

    return InsightExtractionRun(
        insights=insights,
        input_tokens=raw.input_tokens,
        output_tokens=raw.output_tokens,
        cache_read_input_tokens=raw.cache_read_input_tokens,
        cache_creation_input_tokens=raw.cache_creation_input_tokens,
        duration_ms=duration_ms,
        estimated_cost_usd=cost,
    )


def _validate_llm_output(tool_input: dict[str, Any]) -> list[InsightContent]:
    parsed = _LLMInsightOutput.model_validate(tool_input)
    insights: list[InsightContent] = []
    for candidate in parsed.insights:
        payload = {"type": candidate.type, **candidate.content}
        content = validate_insight_content(payload)
        if content.type not in LLM_EXTRACTABLE_TYPES:
            raise ValueError(f"llm produced non-extractable type {content.type!r}")
        content_confidence = getattr(content, "confidence", None)
        if (
            isinstance(content_confidence, Decimal)
            and content_confidence > _LLM_CONFIDENCE_CAP
        ):
            raise ValueError("llm content confidence exceeds cap")
        setattr(content, "_llm_confidence", candidate.confidence)
        insights.append(content)
    return insights


async def _log_extractor_run(
    *,
    session: AsyncSession,
    user_id: uuid.UUID,
    run: InsightExtractionRun,
    conversation_window: Sequence[Any],
    source_event: InsightSourceEvent,
    model: str,
) -> uuid.UUID:
    message_hash = _hash_text(_context_for_hash(conversation_window))
    row = LLMQueryDispatch(
        user_id=user_id,
        message_hash=message_hash,
        total_iterations=1 if run.error is None else 0,
        total_input_tokens=run.input_tokens,
        total_output_tokens=run.output_tokens,
        cache_read_input_tokens=run.cache_read_input_tokens,
        cache_creation_input_tokens=run.cache_creation_input_tokens,
        estimated_cost_usd=run.estimated_cost_usd,
        tools_used=[
            {
                "name": "insight_extractor",
                "args_summary": {
                    "source_event": source_event,
                    "model": model,
                    "insights_returned": len(run.insights),
                },
                "duration_ms": run.duration_ms,
                "estimated_cost_usd": str(run.estimated_cost_usd),
                "error": run.error,
            }
        ],
        final_response_chars=0,
        error=run.error,
        duration_ms=run.duration_ms,
    )
    session.add(row)
    await session.flush()
    return row.id


def _build_user_message(
    *,
    user_id: uuid.UUID,
    conversation_window: Sequence[Any],
    transaction_context: str | dict[str, Any] | None,
) -> str:
    payload = {
        "user_id": str(user_id),
        "conversation_window": _normalise_window(conversation_window),
        "transaction_context": transaction_context or "",
        "instructions": (
            "Extrae solo memoria derivada permitida. Si no hay señal durable, "
            "devolvé insights vacío."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def _normalise_window(conversation_window: Sequence[Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for turn in list(conversation_window)[-_MAX_WINDOW_MESSAGES:]:
        if isinstance(turn, dict):
            role = str(turn.get("role", "user"))
            content = str(turn.get("content", ""))
        else:
            role = str(getattr(turn, "role", "user"))
            content = str(getattr(turn, "content", ""))
        if role not in {"user", "assistant"}:
            role = "user"
        content = content.strip()
        if content:
            out.append({"role": role, "content": content[:2000]})
    return out


def compact_transaction_context_from_tools(
    tools_used: Sequence[dict[str, Any]],
) -> str:
    if not tools_used:
        return "No hubo herramientas de consulta; usar solo la conversación."
    compact: list[dict[str, Any]] = []
    for tool in tools_used[:6]:
        compact.append(
            {
                "name": tool.get("name"),
                "args_summary": tool.get("args_summary", {}),
                "error": tool.get("error"),
            }
        )
    return json.dumps({"query_tools_used": compact}, ensure_ascii=False, default=str)


def post_clarification_context() -> str:
    return (
        "Flujo de aclaración de escritura. No hay agregados SQL; extraé solo "
        "preferencias, metas o señales declaradas por el usuario."
    )


def _estimate_cost_usd(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int,
    cache_creation_input_tokens: int,
) -> Decimal:
    mtok = Decimal("1000000")
    cost = (
        (Decimal(input_tokens) * settings.llm_insight_input_usd_per_mtok)
        + (Decimal(output_tokens) * settings.llm_insight_output_usd_per_mtok)
        + (
            Decimal(cache_read_input_tokens)
            * settings.llm_insight_cache_read_usd_per_mtok
        )
        + (
            Decimal(cache_creation_input_tokens)
            * settings.llm_insight_cache_write_usd_per_mtok
        )
    ) / mtok
    return cost.quantize(Decimal("0.000001"))


def _context_for_hash(conversation_window: Sequence[Any]) -> str:
    return json.dumps(_normalise_window(conversation_window), ensure_ascii=False)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _safe_validation_errors(e: Exception) -> list[dict[str, Any]]:
    if not isinstance(e, ValidationError):
        return [{"error": type(e).__name__}]
    return e.errors(include_context=False, include_input=False)


def _log_task_result(task: asyncio.Task[InsightExtractionJobResult]) -> None:
    try:
        result = task.result()
    except Exception as e:  # pragma: no cover - defensive background logging
        log.warning("insight_extractor_task_failed err=%s", type(e).__name__)
        return
    if result.error:
        log.info(
            "insight_extractor_finished run_id=%s insights=%d error=%s",
            result.run_id,
            len(result.insights),
            result.error,
        )

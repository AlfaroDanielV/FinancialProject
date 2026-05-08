"""Haiku-driven parser for /editar_memoria (Phase 6c B7).

The user has already selected the insight they want to correct (we know
its `target_type`). This module takes their free-text reply and returns
exactly ONE validated `InsightContent` of that type, or raises.

Strict by design:
- Output `type` must equal `target_type`. Anything else → ValueError.
- Validation failures bubble up. There is no partial salvage and no
  fallback to a different type.
- Cost is logged via `llm_query_dispatches` (same audit table as the B4
  extractor) so /editar_memoria spend is visible alongside everything else.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.models.llm_query_dispatch import LLMQueryDispatch
from api.schemas.insights import (
    USER_EDITABLE_TYPES,
    InsightContent,
    validate_insight_content,
)
from api.services.llm_extractor.client import (
    LLMClient,
    LLMClientError,
    RecordedLLMResponse,
)
from prompts.insight_editor import SYSTEM_PROMPT, TOOL_DEFINITION

log = logging.getLogger("api.services.insights.editor_parser")

_DEFAULT_TIMEOUT_S = 8.0


class EditorParseError(RuntimeError):
    """User-correction parsing failed. Caller should re-prompt."""


@dataclass
class EditorParseResult:
    content: InsightContent
    run: "EditorParseRun"


@dataclass
class EditorParseRun:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    duration_ms: int = 0
    estimated_cost_usd: Decimal = Decimal("0.000000")
    error: str | None = None


async def parse_edit(
    *,
    target_type: str,
    user_text: str,
    client: LLMClient,
    model: str | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> EditorParseResult:
    """Call Haiku, validate, return one InsightContent of `target_type`."""
    if target_type not in USER_EDITABLE_TYPES:
        raise EditorParseError(f"target_type {target_type!r} is not user-editable")
    cleaned = user_text.strip()
    if not cleaned:
        raise EditorParseError("empty user text")

    effective_model = model or settings.llm_extraction_model
    started = time.perf_counter()

    user_message = json.dumps(
        {
            "target_type": target_type,
            "user_text": cleaned[:1000],
            "now_iso": datetime.now(timezone.utc).date().isoformat(),
        },
        ensure_ascii=False,
    )

    try:
        raw: RecordedLLMResponse = await client.extract(
            user_message=user_message,
            prior_turns=[],
            system_prompt=SYSTEM_PROMPT,
            tool=TOOL_DEFINITION,
            model=effective_model,
            timeout_s=timeout_s,
        )
    except LLMClientError as e:
        log.warning(
            "editor_parse_transport_failure target=%s err=%s",
            target_type,
            type(e).__name__,
        )
        raise EditorParseError(f"transport:{type(e).__name__}") from e

    duration_ms = int((time.perf_counter() - started) * 1000)
    cost = _estimate_cost_usd(
        input_tokens=raw.input_tokens,
        output_tokens=raw.output_tokens,
        cache_read_input_tokens=raw.cache_read_input_tokens,
        cache_creation_input_tokens=raw.cache_creation_input_tokens,
    )

    try:
        content = _validate_output(raw.tool_input, target_type=target_type)
    except (ValidationError, ValueError) as e:
        log.warning(
            "editor_parse_validation_failure target=%s err=%s",
            target_type,
            type(e).__name__,
        )
        raise EditorParseError(f"validation:{type(e).__name__}") from e

    return EditorParseResult(
        content=content,
        run=EditorParseRun(
            input_tokens=raw.input_tokens,
            output_tokens=raw.output_tokens,
            cache_read_input_tokens=raw.cache_read_input_tokens,
            cache_creation_input_tokens=raw.cache_creation_input_tokens,
            duration_ms=duration_ms,
            estimated_cost_usd=cost,
        ),
    )


def _validate_output(
    tool_input: dict[str, Any], *, target_type: str
) -> InsightContent:
    output_type = tool_input.get("type")
    if output_type != target_type:
        raise ValueError(
            f"editor returned type {output_type!r}, expected {target_type!r}"
        )
    raw_content = tool_input.get("content")
    if not isinstance(raw_content, dict):
        raise ValueError("editor output missing content object")
    payload = {"type": target_type, **raw_content}
    content = validate_insight_content(payload)
    if content.type not in USER_EDITABLE_TYPES:
        raise ValueError(f"editor returned non-editable type {content.type!r}")
    return content


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


async def log_editor_run(
    *,
    session: AsyncSession,
    user_id: uuid.UUID,
    run: EditorParseRun,
    target_type: str,
    model: str,
    user_text_hash: str,
    success: bool,
) -> uuid.UUID:
    """Record one row in `llm_query_dispatches` for cost visibility."""
    row = LLMQueryDispatch(
        user_id=user_id,
        message_hash=user_text_hash,
        total_iterations=1 if success else 0,
        total_input_tokens=run.input_tokens,
        total_output_tokens=run.output_tokens,
        cache_read_input_tokens=run.cache_read_input_tokens,
        cache_creation_input_tokens=run.cache_creation_input_tokens,
        estimated_cost_usd=run.estimated_cost_usd,
        tools_used=[
            {
                "name": "editar_memoria_parser",
                "args_summary": {
                    "target_type": target_type,
                    "model": model,
                    "success": success,
                },
                "duration_ms": run.duration_ms,
                "estimated_cost_usd": str(run.estimated_cost_usd),
                "error": run.error,
            }
        ],
        final_response_chars=0,
        error=run.error if not success else None,
        duration_ms=run.duration_ms,
    )
    session.add(row)
    await session.flush()
    return row.id

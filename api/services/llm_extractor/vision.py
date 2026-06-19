"""Phase 6f B6 — vision extraction for receipt photo uploads.

Two-pass strategy: Haiku first, Sonnet retry if confidence < 0.65.
Returns the same ExtractionResult the text path produces, so the write
dispatcher handles it identically — no special-casing downstream.

The base64-encoded image is stored inside llm_extractions.extraction
under the key "image_b64" so receipts can be audited later. Azure Blob
migration is tracked as P8 tech debt in CLAUDE.md.
"""
from __future__ import annotations

import base64
import time
from typing import Optional

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.llm_extraction import LLMExtraction
from ...models.user import User
from .client import LLMClient, LLMClientError
from .prompt import SYSTEM_PROMPT, TOOL_DEFINITION
from .schema import ExtractionResult

_CONFIDENCE_THRESHOLD = 0.65
_VISION_TIMEOUT_S = 15.0
_VISION_PROMPT = (
    "El usuario adjuntó una foto de un recibo. "
    "Extraé los detalles de la transacción de la imagen usando la herramienta "
    "extract_finance_intent. "
    "Si es un comprobante de transferencia (SINPE Móvil, transferencia bancaria) "
    "que nombra a un tercero, seguí la regla de comprobantes: poné "
    "is_transfer_receipt=true y llená sender_name/sender_phone (quien envía) y "
    "recipient_name/recipient_phone (quien recibe) tal cual aparecen, sin decidir "
    "si es ingreso o gasto. "
    "Si la imagen no es un recibo o no podés leer el monto, "
    "usá intent='unknown' y confidence=0."
)


async def extract_vision(
    *,
    user: User,
    image_bytes: bytes,
    image_media_type: str,
    client: LLMClient,
    haiku_model: str,
    sonnet_model: str,
    db: AsyncSession,
) -> ExtractionResult:
    """Run vision extraction with Haiku-first, Sonnet retry on low confidence."""
    image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    content_blocks: list[dict] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image_media_type,
                "data": image_b64,
            },
        },
        {"type": "text", "text": _VISION_PROMPT},
    ]

    result = await _run_one(
        user=user,
        content_blocks=content_blocks,
        image_b64=image_b64,
        image_media_type=image_media_type,
        client=client,
        model=haiku_model,
        db=db,
        is_retry=False,
    )

    if result.confidence < _CONFIDENCE_THRESHOLD:
        result = await _run_one(
            user=user,
            content_blocks=content_blocks,
            image_b64=image_b64,
            image_media_type=image_media_type,
            client=client,
            model=sonnet_model,
            db=db,
            is_retry=True,
        )

    return result


async def _run_one(
    *,
    user: User,
    content_blocks: list[dict],
    image_b64: str,
    image_media_type: str,
    client: LLMClient,
    model: str,
    db: AsyncSession,
    is_retry: bool,
) -> ExtractionResult:
    t0 = time.perf_counter()
    try:
        raw = await client.extract(
            user_message=content_blocks,
            prior_turns=[],
            system_prompt=SYSTEM_PROMPT,
            tool=TOOL_DEFINITION,
            model=model,
            timeout_s=_VISION_TIMEOUT_S,
        )
    except LLMClientError:
        raise

    latency_ms = int((time.perf_counter() - t0) * 1000)

    try:
        result = ExtractionResult.model_validate(raw.tool_input)
    except ValidationError as e:
        await _log(
            db=db,
            user=user,
            image_media_type=image_media_type,
            intent="unknown",
            confidence=None,
            extraction={
                "invalid": True,
                "errors": e.errors(include_context=False),
                "raw": raw.tool_input,
                "vision": True,
                "is_retry": is_retry,
            },
            latency_ms=latency_ms,
            input_tokens=raw.input_tokens,
            output_tokens=raw.output_tokens,
            cache_read_tokens=raw.cache_read_input_tokens,
            cache_creation_tokens=raw.cache_creation_input_tokens,
            model=model,
        )
        raise

    payload = result.model_dump(mode="json")
    payload["image_b64"] = image_b64
    payload["vision"] = True
    payload["is_retry"] = is_retry

    await _log(
        db=db,
        user=user,
        image_media_type=image_media_type,
        intent=result.intent.value,
        confidence=result.confidence,
        extraction=payload,
        latency_ms=latency_ms,
        input_tokens=raw.input_tokens,
        output_tokens=raw.output_tokens,
        cache_read_tokens=raw.cache_read_input_tokens,
        cache_creation_tokens=raw.cache_creation_input_tokens,
        model=model,
    )
    return result


async def _log(
    *,
    db: AsyncSession,
    user: User,
    image_media_type: str,
    intent: str,
    confidence: Optional[float],
    extraction: dict,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    model: str,
) -> None:
    row = LLMExtraction(
        user_id=user.id,
        message_hash=f"vision:{image_media_type}",
        intent=intent,
        confidence=confidence,
        extraction=extraction,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        model=model,
    )
    db.add(row)
    await db.commit()

"""Glue between an LLMClient and the Telegram handler.

Owns:
- Hashing the raw message (so logs stay PII-free at INFO).
- Calling the LLM client with the cached system prompt + tool schema.
- Parsing the tool_use block into ExtractionResult (Pydantic validation is
  the boundary between untrusted model output and trusted downstream code).
- Writing one `llm_extractions` row per call for later evaluation.

What it deliberately doesn't do:
- Decide what the bot should say.
- Rewrite, normalize, or second-guess extraction fields beyond what
  Pydantic enforces.
- Retry on soft failures. One try at 8s; the caller handles the error by
  asking the user to rephrase.
"""
from __future__ import annotations

import hashlib
import time
from typing import Optional

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.llm_extraction import LLMExtraction
from ...models.user import User
from .client import LLMClient, LLMClientError
from .prompt import SYSTEM_PROMPT, TOOL_DEFINITION
from .schema import ExtractionResult

_DEFAULT_TIMEOUT_S = 8.0


def _hash_message(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def extract_finance_intent(
    *,
    user: User,
    text: str,
    prior_turns: Optional[list[dict[str, str]]] = None,
    client: LLMClient,
    model: str,
    db: AsyncSession,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> ExtractionResult:
    """Run one extraction. Raises LLMClientError on transport failure or
    pydantic.ValidationError on malformed tool output — the caller converts
    both into a Spanish "try again" reply.

    Every call produces exactly one llm_extractions row, even on validation
    failure (with intent='unknown' and the raw payload), so we can debug
    misfires later.
    """
    prior_turns = prior_turns or []
    msg_hash = _hash_message(text)
    t0 = time.perf_counter()

    try:
        raw = await client.extract(
            user_message=text,
            prior_turns=prior_turns,
            system_prompt=SYSTEM_PROMPT,
            tool=TOOL_DEFINITION,
            model=model,
            timeout_s=timeout_s,
        )
    except LLMClientError:
        # No row written — we have no tool_input to store. The caller logs
        # latency separately via its own INFO line.
        raise

    latency_ms = int((time.perf_counter() - t0) * 1000)

    try:
        result = ExtractionResult.model_validate(raw.tool_input)
    except ValidationError as e:
        await _log_extraction(
            db=db,
            user=user,
            message_hash=msg_hash,
            intent="unknown",
            confidence=None,
            extraction={
                "invalid": True,
                "errors": e.errors(include_context=False),
                "raw": raw.tool_input,
            },
            latency_ms=latency_ms,
            input_tokens=raw.input_tokens,
            output_tokens=raw.output_tokens,
            cache_read_tokens=raw.cache_read_input_tokens,
            cache_creation_tokens=raw.cache_creation_input_tokens,
            model=model,
        )
        raise

    await _log_extraction(
        db=db,
        user=user,
        message_hash=msg_hash,
        intent=result.intent.value,
        confidence=result.confidence,
        extraction=result.model_dump(mode="json"),
        latency_ms=latency_ms,
        input_tokens=raw.input_tokens,
        output_tokens=raw.output_tokens,
        cache_read_tokens=raw.cache_read_input_tokens,
        cache_creation_tokens=raw.cache_creation_input_tokens,
        model=model,
    )
    return result


async def _log_extraction(
    *,
    db: AsyncSession,
    user: User,
    message_hash: str,
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
        message_hash=message_hash,
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

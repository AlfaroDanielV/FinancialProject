"""Phase 6f debt slice (D2) — PDF loan-term extraction.

The user uploads a loan contract/statement (PDF); we send it to Claude as a
`document` content block and extract the loan terms into a validated
`DebtTermsExtraction`. The interest rate is the hardest field for a real user
to know off the top of their head — reading it from the contract removes that
friction.

Two-pass strategy mirrors `vision.py`: Haiku first, Sonnet retry if confidence
< 0.65. This obeys "LLM extracts; rules decide": the model only PROPOSES terms
to pre-fill the native form; the deterministic `POST /debts` is still the only
write path. Nothing is created here.

The base64-encoded PDF is stored inside `llm_extractions.extraction` under the
key "pdf_b64" so uploads can be audited later. Azure Blob migration is tracked
as P8 tech debt in CLAUDE.md (same as receipt images).
"""
from __future__ import annotations

import base64
import time
from typing import Optional

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.llm_extraction import LLMExtraction
from ...models.user import User
from ...schemas.debts import DebtTermsExtraction
from .client import LLMClient, LLMClientError

_CONFIDENCE_THRESHOLD = 0.65
_DOCUMENT_TIMEOUT_S = 30.0
_PDF_MEDIA_TYPE = "application/pdf"

_DOCUMENT_SYSTEM_PROMPT = (
    "Sos un extractor de términos de préstamos para un sistema financiero "
    "costarricense. Tu único trabajo es leer el documento adjunto (contrato o "
    "estado de cuenta de un préstamo) y llamar la herramienta extract_loan_terms. "
    "No respondas con texto — siempre llamá la herramienta. Si un dato no está "
    "en el documento, dejalo en null; NO inventes. Preferimos una extracción "
    "parcial honesta a una completa inventada."
)

_DOCUMENT_PROMPT = (
    "Adjunté el documento de mi préstamo. Extraé los términos usando la "
    "herramienta extract_loan_terms. La tasa de interés (interest_rate) debe "
    "ir como FRACCIÓN anual entre 0 y 1: por ejemplo '18%' → 0.18, '12,5%' → "
    "0.125. Si no encontrás algún dato, dejalo en null."
)

_LOAN_TERMS_TOOL = {
    "name": "extract_loan_terms",
    "description": (
        "Extract loan terms from the attached Costa Rican loan contract or "
        "statement PDF. Always call this tool; never reply in free text. Leave "
        "any field that is not present in the document as null."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["confidence"],
        "properties": {
            "original_amount": {
                "type": ["number", "null"],
                "description": "Original principal of the loan (positive).",
            },
            "interest_rate": {
                "type": ["number", "null"],
                "description": (
                    "Annual interest rate as a FRACTION 0–1 (18% → 0.18). NOT a "
                    "percent."
                ),
            },
            "term_months": {
                "type": ["integer", "null"],
                "description": "Loan term in months (5 years → 60).",
            },
            "minimum_payment": {
                "type": ["number", "null"],
                "description": "Monthly payment / cuota (positive).",
            },
            "lender": {
                "type": ["string", "null"],
                "description": "Lending institution ('BAC', 'Banco Nacional').",
            },
            "start_date": {
                "type": ["string", "null"],
                "description": "Loan start/disbursement date as YYYY-MM-DD.",
            },
            "rate_type": {
                "type": ["string", "null"],
                "enum": ["fixed", "variable", None],
                "description": "Fixed or variable rate.",
            },
            "includes_insurance": {
                "type": ["boolean", "null"],
                "description": "Whether the payment includes insurance.",
            },
            "insurance_monthly": {
                "type": ["number", "null"],
                "description": "Monthly insurance amount, if itemized.",
            },
            "currency": {
                "type": ["string", "null"],
                "enum": ["CRC", "USD", None],
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": (
                    "How confident the extraction is. Low when the document is "
                    "a scan, unclear, or not a loan contract."
                ),
            },
        },
    },
}


async def extract_debt_terms(
    *,
    user: User,
    pdf_bytes: bytes,
    client: LLMClient,
    haiku_model: str,
    sonnet_model: str,
    db: AsyncSession,
) -> DebtTermsExtraction:
    """Run PDF loan-term extraction with Haiku-first, Sonnet retry on low
    confidence. Returns a validated `DebtTermsExtraction` (does NOT create a
    debt)."""
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
    content_blocks: list[dict] = [
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": _PDF_MEDIA_TYPE,
                "data": pdf_b64,
            },
        },
        {"type": "text", "text": _DOCUMENT_PROMPT},
    ]

    result = await _run_one(
        user=user,
        content_blocks=content_blocks,
        pdf_b64=pdf_b64,
        client=client,
        model=haiku_model,
        db=db,
        is_retry=False,
    )

    if result.confidence < _CONFIDENCE_THRESHOLD:
        result = await _run_one(
            user=user,
            content_blocks=content_blocks,
            pdf_b64=pdf_b64,
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
    pdf_b64: str,
    client: LLMClient,
    model: str,
    db: AsyncSession,
    is_retry: bool,
) -> DebtTermsExtraction:
    t0 = time.perf_counter()
    raw = await client.extract(
        user_message=content_blocks,
        prior_turns=[],
        system_prompt=_DOCUMENT_SYSTEM_PROMPT,
        tool=_LOAN_TERMS_TOOL,
        model=model,
        timeout_s=_DOCUMENT_TIMEOUT_S,
    )

    latency_ms = int((time.perf_counter() - t0) * 1000)

    try:
        result = DebtTermsExtraction.model_validate(raw.tool_input)
    except ValidationError as e:
        await _log(
            db=db,
            user=user,
            confidence=None,
            extraction={
                "invalid": True,
                "errors": e.errors(include_context=False),
                "raw": raw.tool_input,
                "document": True,
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
    payload["pdf_b64"] = pdf_b64
    payload["document"] = True
    payload["is_retry"] = is_retry

    await _log(
        db=db,
        user=user,
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
        message_hash=f"document:{_PDF_MEDIA_TYPE}",
        intent="parse_debt_document",
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

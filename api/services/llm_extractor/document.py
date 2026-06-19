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
from ...schemas.card_terms import CardTermsExtraction
from ...schemas.debts import DebtTermsExtraction
from ...schemas.statements import StatementExtraction
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


# ── Phase 7b B3: credit-card statement/contract extraction ────────────────────

_CARD_SYSTEM_PROMPT = (
    "Sos un extractor de términos de tarjetas de crédito para un sistema "
    "financiero costarricense. Tu único trabajo es leer el documento adjunto "
    "(el CONTRATO de la tarjeta, o un estado de cuenta si eso fue lo que "
    "subieron) y llamar la herramienta extract_card_terms. No respondas con "
    "texto — siempre llamá la herramienta. Si un dato no está en el "
    "documento, dejalo en null; NO inventes. Preferimos una extracción "
    "parcial honesta a una completa inventada."
)

_CARD_PROMPT = (
    "Adjunté el contrato (o estado de cuenta) de mi tarjeta de crédito. "
    "Extraé los términos usando la herramienta extract_card_terms. Las tasas "
    "(annual_interest_rate, cash_advance_rate, minimum_payment_pct) van como "
    "FRACCIÓN entre 0 y 1: '45%' → 0.45, '2,5%' → 0.025. statement_day es el "
    "día de corte y payment_due_day el día límite de pago (1–31). OJO: muchas "
    "tarjetas costarricenses operan en AMBAS monedas — si el documento "
    "distingue términos en colones y en dólares, poné los de colones en los "
    "campos base y los de dólares en los campos *_usd; si la tarjeta es de "
    "una sola moneda, dejá los *_usd en null. Si no encontrás algún dato, "
    "dejalo en null."
)

_CARD_TERMS_TOOL = {
    "name": "extract_card_terms",
    "description": (
        "Extract credit-card terms from the attached Costa Rican card "
        "contract (or statement) PDF. Always call this tool; never reply in "
        "free text. Leave any field that is not present in the document as "
        "null. Many CR cards operate in BOTH currencies: base fields are the "
        "COLONES terms; fill the *_usd fields only when the document lists "
        "separate dollar terms."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["confidence"],
        "properties": {
            "issuer": {
                "type": ["string", "null"],
                "description": "Issuing bank ('BAC', 'Promerica').",
            },
            "credit_limit": {
                "type": ["number", "null"],
                "description": (
                    "Credit limit / límite de crédito in COLONES (positive)."
                ),
            },
            "statement_balance": {
                "type": ["number", "null"],
                "description": (
                    "Statement balance owed / saldo al corte in COLONES "
                    "(positive magnitude). Usually only on statements, not "
                    "contracts."
                ),
            },
            "annual_interest_rate": {
                "type": ["number", "null"],
                "description": (
                    "Annual PURCHASE interest rate in COLONES as a FRACTION "
                    "0–1 (45% → 0.45). NOT a percent."
                ),
            },
            "cash_advance_rate": {
                "type": ["number", "null"],
                "description": (
                    "Annual CASH-ADVANCE rate as a FRACTION 0–1, if listed."
                ),
            },
            "annual_interest_rate_usd": {
                "type": ["number", "null"],
                "description": (
                    "Annual PURCHASE rate in DOLLARS as a FRACTION 0–1, only "
                    "when the document lists a separate USD rate (CR dual-"
                    "currency cards usually do; it's often lower than the "
                    "colones rate)."
                ),
            },
            "credit_limit_usd": {
                "type": ["number", "null"],
                "description": (
                    "Credit limit in DOLLARS, only when listed separately."
                ),
            },
            "statement_balance_usd": {
                "type": ["number", "null"],
                "description": (
                    "Statement balance owed in DOLLARS (positive magnitude), "
                    "only when the statement carries a separate USD balance."
                ),
            },
            "minimum_payment_pct": {
                "type": ["number", "null"],
                "description": (
                    "Minimum payment as a FRACTION of the balance 0–1 "
                    "('pago mínimo 2.5%' → 0.025), if the formula is listed."
                ),
            },
            "minimum_payment_amount": {
                "type": ["number", "null"],
                "description": (
                    "The minimum payment AMOUNT printed on this statement "
                    "(positive)."
                ),
            },
            "statement_day": {
                "type": ["integer", "null"],
                "description": "Día de corte (1–31).",
            },
            "payment_due_day": {
                "type": ["integer", "null"],
                "description": "Día límite de pago (1–31).",
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
                    "How confident the extraction is. Low when the document "
                    "is a scan, unclear, or not a credit-card document."
                ),
            },
        },
    },
}


# ── Bank-statement reconciliation: read ending balance(s) per product ─────────

_STATEMENT_SYSTEM_PROMPT = (
    "Sos un extractor de estados de cuenta bancarios costarricenses. Tu único "
    "trabajo es leer el estado de cuenta adjunto (PDF) y llamar la herramienta "
    "extract_statement. No respondas con texto — siempre llamá la herramienta. "
    "Un estado puede traer VARIOS productos en un solo documento (por ejemplo "
    "el BAC trae varias cuentas a la vista más un crédito). Devolvé CADA "
    "producto con su saldo al corte. Si un dato no está, dejalo en null; NO "
    "inventes. Preferimos una extracción parcial honesta a una inventada."
)

_STATEMENT_PROMPT = (
    "Adjunté mi estado de cuenta. Extraé cada producto con la herramienta "
    "extract_statement. Para cada producto: kind = 'deposit' para una cuenta a "
    "la vista / ahorro / corriente, 'credit' para una tarjeta de crédito, "
    "'loan' para un préstamo o crédito. closing_balance = el SALDO AL CORTE de "
    "la cuenta, o el saldo adeudado / saldo al corte de la tarjeta o préstamo, "
    "como número POSITIVO tal como aparece impreso. account_last4 = los últimos "
    "4 dígitos del IBAN o número de cuenta. corte_date = la fecha de corte como "
    "YYYY-MM-DD (solo el día, sin hora). Si una tarjeta opera en colones y "
    "dólares con saldos separados, devolvé un producto por moneda."
)

_STATEMENT_TOOL = {
    "name": "extract_statement",
    "description": (
        "Extract every product (account / card / loan) and its closing balance "
        "from the attached Costa Rican bank statement PDF. Always call this "
        "tool; never reply in free text. A single statement may bundle several "
        "products — return one entry per product. Leave any absent field null."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["corte_date", "products", "confidence"],
        "properties": {
            "bank": {
                "type": ["string", "null"],
                "description": "Issuing bank ('BAC', 'Promerica', 'BCR').",
            },
            "corte_date": {
                "type": ["string", "null"],
                "description": (
                    "Fecha de corte as YYYY-MM-DD (day-level, no time)."
                ),
            },
            "products": {
                "type": "array",
                "description": "One entry per product on the statement.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "currency", "closing_balance"],
                    "properties": {
                        "label": {
                            "type": ["string", "null"],
                            "description": (
                                "Human label / product name as printed "
                                "('Cuenta a la vista', 'VISA Emerald')."
                            ),
                        },
                        "account_last4": {
                            "type": ["string", "null"],
                            "description": (
                                "Last 4 digits of the IBAN / account number."
                            ),
                        },
                        "iban": {
                            "type": ["string", "null"],
                            "description": "Full IBAN if printed (optional).",
                        },
                        "currency": {
                            "type": "string",
                            "enum": ["CRC", "USD"],
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["deposit", "credit", "loan"],
                            "description": (
                                "deposit = cuenta a la vista/ahorro/corriente; "
                                "credit = tarjeta de crédito; loan = préstamo."
                            ),
                        },
                        "closing_balance": {
                            "type": "number",
                            "description": (
                                "SALDO AL CORTE (account) or saldo adeudado "
                                "(card/loan) as a POSITIVE magnitude."
                            ),
                        },
                    },
                },
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": (
                    "How confident the extraction is. Low when the document is "
                    "a scan, unclear, or not a bank statement."
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
    return await _extract_document(
        user=user,
        pdf_bytes=pdf_bytes,
        client=client,
        haiku_model=haiku_model,
        sonnet_model=sonnet_model,
        db=db,
        system_prompt=_DOCUMENT_SYSTEM_PROMPT,
        user_prompt=_DOCUMENT_PROMPT,
        tool=_LOAN_TERMS_TOOL,
        result_model=DebtTermsExtraction,
        intent="parse_debt_document",
    )


async def extract_card_terms(
    *,
    user: User,
    pdf_bytes: bytes,
    client: LLMClient,
    haiku_model: str,
    sonnet_model: str,
    db: AsyncSession,
) -> CardTermsExtraction:
    """Phase 7b B3 — card-term extraction from a statement/contract PDF.
    Same Haiku-first / Sonnet-retry contract as `extract_debt_terms`. Returns
    a validated `CardTermsExtraction` (does NOT create anything)."""
    return await _extract_document(
        user=user,
        pdf_bytes=pdf_bytes,
        client=client,
        haiku_model=haiku_model,
        sonnet_model=sonnet_model,
        db=db,
        system_prompt=_CARD_SYSTEM_PROMPT,
        user_prompt=_CARD_PROMPT,
        tool=_CARD_TERMS_TOOL,
        result_model=CardTermsExtraction,
        intent="parse_card_document",
    )


async def extract_statement(
    *,
    user: User,
    pdf_bytes: bytes,
    client: LLMClient,
    haiku_model: str,
    sonnet_model: str,
    db: AsyncSession,
) -> StatementExtraction:
    """Read every product + its closing balance from a bank statement PDF.
    Same Haiku-first / Sonnet-retry contract as `extract_debt_terms`. Returns a
    validated `StatementExtraction` (does NOT write anything — the deterministic
    reconcile path appends the anchors)."""
    return await _extract_document(
        user=user,
        pdf_bytes=pdf_bytes,
        client=client,
        haiku_model=haiku_model,
        sonnet_model=sonnet_model,
        db=db,
        system_prompt=_STATEMENT_SYSTEM_PROMPT,
        user_prompt=_STATEMENT_PROMPT,
        tool=_STATEMENT_TOOL,
        result_model=StatementExtraction,
        intent="parse_statement",
    )


async def _extract_document(
    *,
    user: User,
    pdf_bytes: bytes,
    client: LLMClient,
    haiku_model: str,
    sonnet_model: str,
    db: AsyncSession,
    system_prompt: str,
    user_prompt: str,
    tool: dict,
    result_model,
    intent: str,
):
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
        {"type": "text", "text": user_prompt},
    ]

    result = await _run_one(
        user=user,
        content_blocks=content_blocks,
        pdf_b64=pdf_b64,
        client=client,
        model=haiku_model,
        db=db,
        is_retry=False,
        system_prompt=system_prompt,
        tool=tool,
        result_model=result_model,
        intent=intent,
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
            system_prompt=system_prompt,
            tool=tool,
            result_model=result_model,
            intent=intent,
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
    system_prompt: str = _DOCUMENT_SYSTEM_PROMPT,
    tool: dict = _LOAN_TERMS_TOOL,
    result_model=DebtTermsExtraction,
    intent: str = "parse_debt_document",
):
    t0 = time.perf_counter()
    raw = await client.extract(
        user_message=content_blocks,
        prior_turns=[],
        system_prompt=system_prompt,
        tool=tool,
        model=model,
        timeout_s=_DOCUMENT_TIMEOUT_S,
    )

    latency_ms = int((time.perf_counter() - t0) * 1000)

    try:
        result = result_model.model_validate(raw.tool_input)
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
            intent=intent,
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
        intent=intent,
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
    intent: str = "parse_debt_document",
) -> None:
    row = LLMExtraction(
        user_id=user.id,
        message_hash=f"document:{_PDF_MEDIA_TYPE}",
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

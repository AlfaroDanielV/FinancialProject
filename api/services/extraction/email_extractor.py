"""Extract transaction data from a bank notification email body.

Reuses the LLMClient protocol from `api.services.llm_extractor` so the
fixture client patterns (FixtureLLMClient) work here too without code
duplication. Everything else (prompt, tool schema, response shape) is
specific to bank emails.

Output: `ExtractedEmailTransaction`. The reconciler (`reconciler.py`)
applies the sign convention based on `transaction_type`.

Failure mode: if the email body doesn't look like a transaction
(marketing, statements, OTPs), the extractor is allowed to return
`confidence=0.0` and other fields can be null. The reconciler then
maps that to `skipped_low_confidence`. This is by design — we'd
rather miss a transaction than insert garbage.
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..llm_extractor.client import LLMClient, LLMClientError


log = logging.getLogger("api.services.extraction.email")


# ── transaction-type taxonomy ────────────────────────────────────────────────

TRANSACTION_TYPES: tuple[str, ...] = (
    "charge",
    "withdrawal",
    "fee",
    "payment",
    "transfer",
    "deposit",
    "refund",
    "unknown",
)
EXPENSE_TYPES: frozenset[str] = frozenset(
    {"charge", "withdrawal", "fee", "payment", "transfer"}
)
INCOME_TYPES: frozenset[str] = frozenset({"deposit", "refund"})


# ── result schema ────────────────────────────────────────────────────────────


class ExtractedEmailTransaction(BaseModel):
    """Structured result from one email body.

    Validation rules (enforced by Pydantic):
    - amount must be positive when present.
    - transaction_type must be in TRANSACTION_TYPES.
    - currency normalized to CRC | USD | None.
    - confidence clamped to 0..1.
    """

    model_config = {"extra": "forbid"}

    amount: Optional[Decimal] = Field(default=None, description="Positive monetary amount")
    currency: Optional[str] = Field(default=None)
    merchant: Optional[str] = Field(default=None, max_length=255)
    transaction_date: Optional[date] = Field(default=None)
    last4: Optional[str] = Field(default=None, max_length=8)
    description: Optional[str] = Field(default=None, max_length=500)
    transaction_type: str = Field(default="unknown")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("amount")
    @classmethod
    def _amount_positive_or_none(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is None:
            return v
        if v <= 0:
            return None
        return v

    @field_validator("currency", mode="before")
    @classmethod
    def _normalize_currency(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if not isinstance(v, str):
            return None
        cleaned = v.strip().upper()
        # Common variants we want to fold into canonical CRC.
        if cleaned in {"CRC", "₡", "COLONES", "COLON", "CR$", "₡R"}:
            return "CRC"
        if cleaned in {"USD", "$", "US$", "DOLAR", "DÓLAR", "DOLARES", "DÓLARES"}:
            return "USD"
        return None

    @field_validator("transaction_type", mode="before")
    @classmethod
    def _normalize_type(cls, v: Any) -> str:
        if v is None:
            return "unknown"
        if not isinstance(v, str):
            return "unknown"
        norm = v.strip().lower()
        if norm in TRANSACTION_TYPES:
            return norm
        # Tolerate Spanish synonyms the LLM might pick up.
        spanish_map = {
            "compra": "charge",
            "cargo": "charge",
            "retiro": "withdrawal",
            "comision": "fee",
            "comisión": "fee",
            "pago": "payment",
            "transferencia": "transfer",
            "deposito": "deposit",
            "depósito": "deposit",
            "reembolso": "refund",
            "devolucion": "refund",
            "devolución": "refund",
        }
        return spanish_map.get(norm, "unknown")

    @field_validator("last4", mode="before")
    @classmethod
    def _normalize_last4(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        # Strip leading "*" or "x" the LLM might include from a masked
        # display ("****1234" or "x1234").
        s = s.lstrip("*xX-• ").strip()
        if not s:
            return None
        # Accept anything 3-6 digits — some banks use 5- or 6-digit
        # truncations. Reject if non-digit content remains.
        if not s.isdigit() or not (3 <= len(s) <= 6):
            return None
        return s


# ── exceptions ───────────────────────────────────────────────────────────────


class EmailExtractionError(RuntimeError):
    """Raised by the runner on transport / validation failure."""


# ── prompts ──────────────────────────────────────────────────────────────────

EMAIL_SYSTEM_PROMPT = """Sos un parser de notificaciones bancarias por correo de Costa Rica.

Recibís el cuerpo (texto plano) de un correo y devolvés, vía la herramienta
report_email_transaction, los datos estructurados de la transacción que
ese correo está notificando.

Reglas:
- amount es siempre POSITIVO. El signo lo aplica el sistema más adelante
  según transaction_type.
- transaction_type debe ser uno de: charge, withdrawal, fee, payment,
  transfer, deposit, refund, unknown.
  - charge: compra con tarjeta, cargo recurrente, débito automático.
  - withdrawal: retiro de cajero o ATM.
  - fee: comisión, cargo por mantenimiento, intereses.
  - payment: pago de tarjeta de crédito, pago de servicios.
  - transfer: SINPE, transferencia entre cuentas, salida de fondos.
  - deposit: depósito recibido, salario, ingreso de SINPE.
  - refund: devolución, reembolso, anulación.
  - unknown: si no podés clasificar con razonable certeza.
- currency: usa "CRC" para colones (₡), "USD" para dólares ($).
- transaction_date: la fecha del cargo/movimiento, en formato YYYY-MM-DD.
  Si el correo no la menciona, dejala en null.
- last4: últimos 3-6 dígitos de la tarjeta o cuenta SI aparecen
  ("****1234", "tarjeta 5678", "cuenta-CR05-0152..."). Si no, null.
- merchant: el comercio o destinatario (Walmart, Uber, AyA, "transferencia
  a Juan Pérez"). Para deposits, el origen.
- description: una oración corta neutral. NO inventes detalles.
- confidence: tu certeza de que extrajiste bien (0.0–1.0).
  - <0.6 si el correo no es una transacción (marketing, estados de
    cuenta resumen, OTPs, alertas de login).
  - 0.7–0.85 para transacciones claras pero con algún campo faltante.
  - 0.9+ sólo cuando todos los campos críticos (amount, type, date)
    están claros.

NO inventes datos. Si un campo no está en el correo, devolvelo como null.
La sobreinterpretación es peor que dejar el campo vacío."""


EMAIL_TOOL_DEFINITION = {
    "name": "report_email_transaction",
    "description": "Reporta los campos extraídos de un correo bancario.",
    "input_schema": {
        "type": "object",
        "properties": {
            "amount": {
                "type": ["number", "string", "null"],
                "description": "Monto positivo. Number ideal; string aceptado para banks que mandan '5,000.00'.",
            },
            "currency": {
                "type": ["string", "null"],
                "description": "CRC | USD | null",
            },
            "merchant": {"type": ["string", "null"]},
            "transaction_date": {
                "type": ["string", "null"],
                "description": "ISO-8601 date (YYYY-MM-DD), or null.",
            },
            "last4": {
                "type": ["string", "null"],
                "description": "3-6 digits, no asterisks.",
            },
            "description": {"type": ["string", "null"]},
            "transaction_type": {
                "type": "string",
                "enum": list(TRANSACTION_TYPES),
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
            },
        },
        "required": ["transaction_type", "confidence"],
    },
}


# ── runner ───────────────────────────────────────────────────────────────────


_DEFAULT_TIMEOUT_S = 8.0


def _hash_body(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


async def extract_from_email_body(
    *,
    body: str,
    client: LLMClient,
    model: str,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> ExtractedEmailTransaction:
    """One LLM call. Returns ExtractedEmailTransaction.

    Raises EmailExtractionError on transport / validation failure. The
    scanner catches that and records `outcome='failed'` for the message.

    Hashes the body for the log line so personal finance never lands at
    INFO level. Body itself is never logged.
    """
    # Guard against giant emails that would inflate token cost. 4000 chars
    # ~ 1000 tokens. Real bank notifications are well under that — anything
    # bigger is likely a marketing email with a transaction snippet, and
    # the trim still includes the typical preamble.
    body_trimmed = body[:4000] if body else ""
    body_hash = _hash_body(body_trimmed)

    t0 = time.perf_counter()
    try:
        raw = await client.extract(
            user_message=body_trimmed,
            prior_turns=[],
            system_prompt=EMAIL_SYSTEM_PROMPT,
            tool=EMAIL_TOOL_DEFINITION,
            model=model,
            timeout_s=timeout_s,
        )
    except LLMClientError as e:
        log.warning(
            "email_extraction_transport_error hash=%s err=%s", body_hash, e
        )
        raise EmailExtractionError(f"transport: {e}") from e

    latency_ms = int((time.perf_counter() - t0) * 1000)

    try:
        result = ExtractedEmailTransaction.model_validate(raw.tool_input)
    except ValidationError as e:
        log.warning(
            "email_extraction_validation_error hash=%s errors=%s",
            body_hash,
            e.errors(include_context=False),
        )
        raise EmailExtractionError(f"validation: {e}") from e

    log.info(
        "email_extraction_ok hash=%s type=%s conf=%.2f amount=%s "
        "currency=%s latency_ms=%d in=%d out=%d cache_r=%d cache_c=%d",
        body_hash,
        result.transaction_type,
        result.confidence,
        result.amount if result.amount is not None else "?",
        result.currency or "?",
        latency_ms,
        raw.input_tokens,
        raw.output_tokens,
        raw.cache_read_input_tokens,
        raw.cache_creation_input_tokens,
    )
    return result

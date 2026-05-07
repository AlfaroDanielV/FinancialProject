"""Tests for the email extractor service.

We use FixtureLLMClient (already in api.services.llm_extractor) so no
network. Coverage:
- Pydantic-level normalization (currency variants, last4 stripping,
  Spanish transaction_type synonyms, confidence clamping).
- Round-trip via extract_from_email_body with a mocked tool_input.
- Error paths (transport, validation).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from api.services.extraction.email_extractor import (
    EXPENSE_TYPES,
    INCOME_TYPES,
    EmailExtractionError,
    ExtractedEmailTransaction,
    extract_from_email_body,
)
from api.services.llm_extractor.client import (
    FixtureLLMClient,
    LLMClientError,
    RecordedLLMResponse,
)


# ── Pydantic normalization ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("CRC", "CRC"),
        ("crc", "CRC"),
        ("₡", "CRC"),
        ("colones", "CRC"),
        ("USD", "USD"),
        ("$", "USD"),
        ("dolares", "USD"),
        ("Dólares", "USD"),
        ("EUR", None),
        ("", None),
        (None, None),
        (123, None),
    ],
)
def test_currency_normalization(raw, expected):
    out = ExtractedEmailTransaction.model_validate(
        {"transaction_type": "charge", "confidence": 0.5, "currency": raw}
    )
    assert out.currency == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1234", "1234"),
        ("****1234", "1234"),
        ("xx1234", "1234"),
        ("****12345", "12345"),
        ("**5678", "5678"),
        ("12", None),  # too short
        ("1234567890", None),  # too long
        ("12ab", None),  # non-numeric
        ("", None),
        (None, None),
    ],
)
def test_last4_normalization(raw, expected):
    out = ExtractedEmailTransaction.model_validate(
        {"transaction_type": "charge", "confidence": 0.5, "last4": raw}
    )
    assert out.last4 == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("charge", "charge"),
        ("CHARGE", "charge"),
        ("compra", "charge"),
        ("Cargo", "charge"),
        ("retiro", "withdrawal"),
        ("comisión", "fee"),
        ("comision", "fee"),
        ("Pago", "payment"),
        ("transferencia", "transfer"),
        ("depósito", "deposit"),
        ("DEPOSITO", "deposit"),
        ("reembolso", "refund"),
        ("devolución", "refund"),
        ("garbage_value", "unknown"),
        (None, "unknown"),
        (42, "unknown"),
    ],
)
def test_transaction_type_normalization(raw, expected):
    out = ExtractedEmailTransaction.model_validate(
        {"transaction_type": raw, "confidence": 0.5}
    )
    assert out.transaction_type == expected


def test_amount_negative_becomes_none():
    out = ExtractedEmailTransaction.model_validate(
        {"transaction_type": "charge", "confidence": 0.8, "amount": -50}
    )
    assert out.amount is None


def test_amount_zero_becomes_none():
    out = ExtractedEmailTransaction.model_validate(
        {"transaction_type": "charge", "confidence": 0.8, "amount": 0}
    )
    assert out.amount is None


def test_amount_positive_passes_through():
    out = ExtractedEmailTransaction.model_validate(
        {"transaction_type": "charge", "confidence": 0.8, "amount": "5000.50"}
    )
    assert out.amount == Decimal("5000.50")


def test_confidence_clamping():
    """Pydantic ge/le rejects out-of-range; assert the contract."""
    with pytest.raises(Exception):
        ExtractedEmailTransaction.model_validate(
            {"transaction_type": "charge", "confidence": 1.5}
        )
    with pytest.raises(Exception):
        ExtractedEmailTransaction.model_validate(
            {"transaction_type": "charge", "confidence": -0.1}
        )


def test_extra_fields_rejected():
    """Schema is strict — Anthropic occasionally adds explanatory keys.
    extra='forbid' guards against silent drift."""
    with pytest.raises(Exception):
        ExtractedEmailTransaction.model_validate(
            {
                "transaction_type": "charge",
                "confidence": 0.8,
                "_internal_note": "trust me",
            }
        )


# ── EXPENSE_TYPES / INCOME_TYPES taxonomy ────────────────────────────────────


def test_taxonomy_partition():
    """Every type except 'unknown' is exactly in one of the sign sets."""
    from api.services.extraction.email_extractor import TRANSACTION_TYPES

    for t in TRANSACTION_TYPES:
        if t == "unknown":
            assert t not in EXPENSE_TYPES
            assert t not in INCOME_TYPES
            continue
        assert (t in EXPENSE_TYPES) ^ (t in INCOME_TYPES), (
            f"{t} should be in exactly one of EXPENSE_TYPES / INCOME_TYPES"
        )


# ── runner: extract_from_email_body ──────────────────────────────────────────


async def test_extract_happy_path():
    fixture = FixtureLLMClient(
        default=RecordedLLMResponse(
            tool_input={
                "amount": "5000.00",
                "currency": "CRC",
                "merchant": "Walmart",
                "transaction_date": "2026-05-06",
                "last4": "****1234",
                "description": "Compra con tarjeta",
                "transaction_type": "charge",
                "confidence": 0.92,
            },
            input_tokens=200,
            output_tokens=80,
        )
    )
    result = await extract_from_email_body(
        body="Notificación: compra por ₡5,000 en Walmart...",
        client=fixture,
        model="claude-haiku-4-5",
    )
    assert result.amount == Decimal("5000.00")
    assert result.currency == "CRC"
    assert result.merchant == "Walmart"
    assert result.transaction_type == "charge"
    assert result.last4 == "1234"
    assert result.confidence == pytest.approx(0.92)


async def test_extract_low_confidence_marketing_email():
    """The extractor is allowed to return confidence=0 for non-transactions."""
    fixture = FixtureLLMClient(
        default=RecordedLLMResponse(
            tool_input={
                "transaction_type": "unknown",
                "confidence": 0.05,
            }
        )
    )
    result = await extract_from_email_body(
        body="¡Aprovechá nuestra promoción de tarjetas...",
        client=fixture,
        model="x",
    )
    assert result.confidence < 0.6
    assert result.amount is None
    assert result.transaction_type == "unknown"


async def test_extract_propagates_transport_error_as_extraction_error():
    class _Boom:
        async def extract(self, **kwargs):
            raise LLMClientError("api blew up")

    with pytest.raises(EmailExtractionError, match="transport"):
        await extract_from_email_body(
            body="x", client=_Boom(), model="x"
        )


async def test_extract_propagates_validation_error_as_extraction_error():
    """Confidence > 1 trips Pydantic ge/le validation — not the kind of
    drift the model layer tolerates silently."""
    fixture = FixtureLLMClient(
        default=RecordedLLMResponse(
            tool_input={
                "transaction_type": "charge",
                "confidence": 1.4,  # out of range
            }
        )
    )
    with pytest.raises(EmailExtractionError, match="validation"):
        await extract_from_email_body(
            body="x", client=fixture, model="x"
        )


async def test_extract_truncates_huge_body():
    """A 50KB email shouldn't blow up token cost. The runner trims to 4000
    chars before passing to the LLM. We verify by capturing what the
    fixture sees."""
    captured: dict = {}

    class _Capturing:
        async def extract(self, *, user_message, **kwargs):
            captured["len"] = len(user_message)
            return RecordedLLMResponse(
                tool_input={"transaction_type": "charge", "confidence": 0.5}
            )

    big = "x" * 50_000
    await extract_from_email_body(body=big, client=_Capturing(), model="x")
    assert captured["len"] == 4000

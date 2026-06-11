"""Phase 7b B3 — credit-card PDF term extraction + parse-card-document endpoint.

Mirrors the Phase 6f debt-parse suite:
1. `extract_card_terms()` single Haiku pass when confidence >= threshold.
2. `extract_card_terms()` Sonnet retry when Haiku confidence < threshold.
3. `extract_card_terms()` writes an llm_extractions audit row with
   intent="parse_card_document".
4. `POST /accounts/parse-card-document` 415 on a non-PDF MIME type.
5. `POST /accounts/parse-card-document` 413 on an oversized payload.
6. Happy path → parsed terms, NOTHING created (the PUT is the only write path).
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.main import app
from api.models.credit_card_terms import CreditCardTerms
from api.models.llm_extraction import LLMExtraction
from api.models.user import User as UserModel
from api.services.auth.magic_link import generate_link
from api.services.llm_extractor import FixtureLLMClient, RecordedLLMResponse
from api.services.llm_extractor.document import (
    _CONFIDENCE_THRESHOLD,
    extract_card_terms,
)
from bot.app import set_llm_client


_TINY_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


_CARD_HIGH = RecordedLLMResponse(
    tool_input={
        "issuer": "BAC",
        "credit_limit": 2000000,
        "statement_balance": 350000,
        "annual_interest_rate": 0.45,
        "cash_advance_rate": 0.50,
        "minimum_payment_pct": 0.025,
        "minimum_payment_amount": 8750,
        "statement_day": 20,
        "payment_due_day": 10,
        "currency": "CRC",
        "confidence": 0.9,
    },
)

# Dual-currency CR card: the contract lists separate dollar terms — the
# *_usd fields are the "this card runs in ₡ AND $" signal the form uses to
# switch to Ambas and create one credit account per currency.
_CARD_DUAL = RecordedLLMResponse(
    tool_input={
        **_CARD_HIGH.tool_input,
        "annual_interest_rate_usd": 0.39,
        "credit_limit_usd": 4000,
        "statement_balance_usd": 250,
    },
)

_CARD_LOW = RecordedLLMResponse(
    tool_input={
        "issuer": None,
        "credit_limit": None,
        "statement_balance": None,
        "annual_interest_rate": None,
        "cash_advance_rate": None,
        "minimum_payment_pct": None,
        "minimum_payment_amount": None,
        "statement_day": None,
        "payment_due_day": None,
        "currency": None,
        "confidence": 0.30,
    },
)

_CARD_SONNET = RecordedLLMResponse(
    tool_input={**_CARD_HIGH.tool_input, "confidence": 0.82},
)


def _override_db(session):
    async def _yield():
        yield session

    app.dependency_overrides[get_db] = _yield


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


async def _get_user(session, user_id) -> UserModel:
    return (
        await session.execute(select(UserModel).where(UserModel.id == user_id))
    ).scalar_one()


async def _setup_token(session, user_id, fixture_response):
    set_llm_client(FixtureLLMClient(default=fixture_response))
    link = await generate_link(session, user_id=user_id, purpose="onboarding")
    _override_db(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        exchange = await ac.post(
            "/api/v1/auth/magic-link/exchange", json={"token": link.raw_token}
        )
    assert exchange.status_code == 200, exchange.text
    return exchange.json()["token"]


@pytest.mark.asyncio
async def test_extract_card_terms_single_pass(db_with_user):
    session, user_id = db_with_user
    user = await _get_user(session, user_id)

    call_count = 0

    class _CountingClient:
        async def extract(self, *, user_message, **kwargs) -> RecordedLLMResponse:
            nonlocal call_count
            call_count += 1
            return _CARD_HIGH

    result = await extract_card_terms(
        user=user,
        pdf_bytes=_TINY_PDF,
        client=_CountingClient(),
        haiku_model="claude-haiku-4-5",
        sonnet_model="claude-sonnet-4-5",
        db=session,
    )

    assert call_count == 1
    assert result.confidence == 0.9
    assert result.annual_interest_rate == 0.45  # 0–1 fraction
    assert result.minimum_payment_pct == 0.025
    assert result.statement_day == 20
    assert result.payment_due_day == 10
    assert result.issuer == "BAC"


@pytest.mark.asyncio
async def test_extract_card_terms_retries_on_low_confidence(db_with_user):
    session, user_id = db_with_user
    user = await _get_user(session, user_id)

    assert _CARD_LOW.tool_input["confidence"] < _CONFIDENCE_THRESHOLD
    responses = [_CARD_LOW, _CARD_SONNET]
    idx = 0

    class _StepClient:
        async def extract(self, *, user_message, model, **kwargs) -> RecordedLLMResponse:
            nonlocal idx
            r = responses[idx]
            idx += 1
            return r

    result = await extract_card_terms(
        user=user,
        pdf_bytes=_TINY_PDF,
        client=_StepClient(),
        haiku_model="claude-haiku-4-5",
        sonnet_model="claude-sonnet-4-5",
        db=session,
    )

    assert idx == 2
    assert result.confidence == 0.82


@pytest.mark.asyncio
async def test_extract_card_terms_logs_extraction(db_with_user):
    session, user_id = db_with_user
    user = await _get_user(session, user_id)

    class _Client:
        async def extract(self, **kwargs) -> RecordedLLMResponse:
            return _CARD_HIGH

    await extract_card_terms(
        user=user,
        pdf_bytes=_TINY_PDF,
        client=_Client(),
        haiku_model="claude-haiku-4-5",
        sonnet_model="claude-sonnet-4-5",
        db=session,
    )

    rows = (
        await session.execute(
            select(LLMExtraction).where(LLMExtraction.user_id == user_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.intent == "parse_card_document"
    assert row.extraction["document"] is True
    assert "pdf_b64" in row.extraction


@pytest.mark.asyncio
async def test_extract_card_terms_dual_currency_passthrough(db_with_user):
    """A dual-currency contract fills the *_usd fields; a single-currency one
    leaves them null (the prior tests' fixtures omit them → None)."""
    session, user_id = db_with_user
    user = await _get_user(session, user_id)

    class _Client:
        async def extract(self, **kwargs) -> RecordedLLMResponse:
            return _CARD_DUAL

    result = await extract_card_terms(
        user=user,
        pdf_bytes=_TINY_PDF,
        client=_Client(),
        haiku_model="claude-haiku-4-5",
        sonnet_model="claude-sonnet-4-5",
        db=session,
    )
    assert result.annual_interest_rate == 0.45  # colones
    assert result.annual_interest_rate_usd == 0.39  # dólares (lower, typical)
    assert result.credit_limit_usd == 4000
    assert result.statement_balance_usd == 250


@pytest.mark.asyncio
async def test_parse_card_document_rejects_bad_mime(db_with_user):
    session, user_id = db_with_user
    token = await _setup_token(session, user_id, _CARD_HIGH)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/accounts/parse-card-document",
                files={"file": ("photo.png", b"\x89PNG", "image/png")},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 415
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_parse_card_document_rejects_oversized(db_with_user):
    session, user_id = db_with_user
    token = await _setup_token(session, user_id, _CARD_HIGH)
    try:
        oversized = b"%PDF-" + b"0" * (4 * 1024 * 1024 + 1)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/accounts/parse-card-document",
                files={"file": ("big.pdf", oversized, "application/pdf")},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 413
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_parse_card_document_happy_path_creates_nothing(db_with_user):
    session, user_id = db_with_user
    token = await _setup_token(session, user_id, _CARD_HIGH)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/accounts/parse-card-document",
                files={"file": ("estado.pdf", _TINY_PDF, "application/pdf")},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["annual_interest_rate"] == 0.45
        assert body["minimum_payment_pct"] == 0.025
        assert body["statement_balance"] == 350000
        assert body["confidence"] == 0.9

        rows = (
            await session.execute(
                select(CreditCardTerms).where(
                    CreditCardTerms.user_id == user_id
                )
            )
        ).scalars().all()
        assert rows == []
    finally:
        _clear_db_override()

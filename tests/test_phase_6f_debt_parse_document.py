"""Phase 6f debt slice (D2) — PDF loan-term extraction + parse-document endpoint.

Tests cover:
1. `extract_debt_terms()` single Haiku pass when confidence >= threshold.
2. `extract_debt_terms()` Sonnet retry when Haiku confidence < threshold.
3. `extract_debt_terms()` writes an llm_extractions audit row.
4. `POST /debts/parse-document` 415 on a non-PDF MIME type.
5. `POST /debts/parse-document` 413 on an oversized payload.
6. `POST /debts/parse-document` happy path → parsed terms, no debt created.

FixtureLLMClient ignores the PDF bytes, so a minimal `%PDF-` header suffices.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.main import app
from api.models.debt import Debt
from api.models.llm_extraction import LLMExtraction
from api.models.user import User as UserModel
from api.services.auth.magic_link import generate_link
from api.services.llm_extractor import FixtureLLMClient, RecordedLLMResponse
from api.services.llm_extractor.document import (
    _CONFIDENCE_THRESHOLD,
    extract_debt_terms,
)
from bot.app import set_llm_client


_TINY_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


_TERMS_HIGH = RecordedLLMResponse(
    tool_input={
        "original_amount": 5000000,
        "interest_rate": 0.18,
        "term_months": 60,
        "minimum_payment": 126000,
        "lender": "BAC",
        "start_date": "2026-01-15",
        "rate_type": "fixed",
        "includes_insurance": False,
        "insurance_monthly": None,
        "currency": "CRC",
        "confidence": 0.9,
    },
)

_TERMS_LOW = RecordedLLMResponse(
    tool_input={
        "original_amount": None,
        "interest_rate": None,
        "term_months": None,
        "minimum_payment": None,
        "lender": None,
        "start_date": None,
        "rate_type": None,
        "includes_insurance": None,
        "insurance_monthly": None,
        "currency": None,
        "confidence": 0.30,
    },
)

_TERMS_SONNET = RecordedLLMResponse(
    tool_input={**_TERMS_HIGH.tool_input, "confidence": 0.82},
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


# ── 1. high confidence → single Haiku call ────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_debt_terms_single_pass(db_with_user):
    session, user_id = db_with_user
    user = await _get_user(session, user_id)

    call_count = 0

    class _CountingClient:
        async def extract(self, *, user_message, **kwargs) -> RecordedLLMResponse:
            nonlocal call_count
            call_count += 1
            return _TERMS_HIGH

    result = await extract_debt_terms(
        user=user,
        pdf_bytes=_TINY_PDF,
        client=_CountingClient(),
        haiku_model="claude-haiku-4-5",
        sonnet_model="claude-sonnet-4-5",
        db=session,
    )

    assert call_count == 1
    assert result.confidence == 0.9
    assert result.interest_rate == 0.18  # stored as a 0–1 fraction
    assert result.term_months == 60
    assert result.lender == "BAC"


# ── 2. low confidence → Haiku + Sonnet retry ──────────────────────────────────


@pytest.mark.asyncio
async def test_extract_debt_terms_retries_on_low_confidence(db_with_user):
    session, user_id = db_with_user
    user = await _get_user(session, user_id)

    assert _TERMS_LOW.tool_input["confidence"] < _CONFIDENCE_THRESHOLD
    responses = [_TERMS_LOW, _TERMS_SONNET]
    idx = 0

    class _StepClient:
        async def extract(self, *, user_message, model, **kwargs) -> RecordedLLMResponse:
            nonlocal idx
            r = responses[idx]
            idx += 1
            return r

    result = await extract_debt_terms(
        user=user,
        pdf_bytes=_TINY_PDF,
        client=_StepClient(),
        haiku_model="claude-haiku-4-5",
        sonnet_model="claude-sonnet-4-5",
        db=session,
    )

    assert idx == 2
    assert result.confidence == 0.82


# ── 3. an llm_extractions audit row is written ────────────────────────────────


@pytest.mark.asyncio
async def test_extract_debt_terms_logs_extraction(db_with_user):
    session, user_id = db_with_user
    user = await _get_user(session, user_id)

    class _Client:
        async def extract(self, **kwargs) -> RecordedLLMResponse:
            return _TERMS_HIGH

    await extract_debt_terms(
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
    assert row.intent == "parse_debt_document"
    assert row.extraction["document"] is True
    assert "pdf_b64" in row.extraction  # PDF stored inline for audit


# ── 4. endpoint 415 on non-PDF ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_document_rejects_bad_mime(db_with_user):
    session, user_id = db_with_user
    token = await _setup_token(session, user_id, _TERMS_HIGH)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/debts/parse-document",
                files={"file": ("photo.png", b"\x89PNG", "image/png")},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 415
    finally:
        _clear_db_override()


# ── 5. endpoint 413 on oversized payload ──────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_document_rejects_oversized(db_with_user):
    session, user_id = db_with_user
    token = await _setup_token(session, user_id, _TERMS_HIGH)
    try:
        oversized = b"%PDF-" + b"0" * (4 * 1024 * 1024 + 1)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/debts/parse-document",
                files={"file": ("big.pdf", oversized, "application/pdf")},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 413
    finally:
        _clear_db_override()


# ── 6. endpoint happy path → parsed terms, no debt created ────────────────────


@pytest.mark.asyncio
async def test_parse_document_happy_path(db_with_user):
    session, user_id = db_with_user
    token = await _setup_token(session, user_id, _TERMS_HIGH)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/debts/parse-document",
                files={"file": ("loan.pdf", _TINY_PDF, "application/pdf")},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["interest_rate"] == 0.18
        assert body["term_months"] == 60
        assert body["lender"] == "BAC"
        assert body["confidence"] == 0.9

        # parse-document never creates a debt — it only pre-fills the form.
        debts = (
            await session.execute(select(Debt).where(Debt.user_id == user_id))
        ).scalars().all()
        assert len(debts) == 0
    finally:
        _clear_db_override()

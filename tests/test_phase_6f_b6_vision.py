"""Phase 6f B6 — receipt photo upload: vision extraction + `/chat/image` endpoint.

Tests cover:
1. `extract_vision()` calls Haiku once when confidence >= threshold.
2. `extract_vision()` retries with Sonnet when Haiku confidence < threshold.
3. `/POST /chat/image` 415 on unsupported MIME type.
4. `/POST /chat/image` 413 on oversized payload.
5. `/POST /chat/image` happy-path: valid JPEG → proposal returned.
6. `process_message()` vision fast-path: image_bytes bypasses text pipeline.
"""
from __future__ import annotations

import io
import struct
import zlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from api.database import get_db
from api.main import app
from api.services.auth.magic_link import generate_link
from api.services.llm_extractor import FixtureLLMClient, RecordedLLMResponse
from api.services.llm_extractor.vision import (
    _CONFIDENCE_THRESHOLD,
    extract_vision,
)
from api.models.account import Account
from bot.app import set_llm_client
from tests.fixtures.extractor_responses import BASIC_EXPENSE_CRC


# ── shared fixture responses ──────────────────────────────────────────────────

_HIGH_CONFIDENCE = RecordedLLMResponse(
    tool_input={
        "intent": "log_expense",
        "dispatcher": "write",
        "amount": 3500,
        "currency": "CRC",
        "merchant": "farmacia",
        "category_hint": "salud",
        "account_hint": None,
        "occurred_at_hint": None,
        "query_window": None,
        "confidence": 0.88,
        "raw_notes": None,
    },
)

_LOW_CONFIDENCE = RecordedLLMResponse(
    tool_input={
        "intent": "unknown",
        "dispatcher": "control",
        "amount": None,
        "currency": None,
        "merchant": None,
        "category_hint": None,
        "account_hint": None,
        "occurred_at_hint": None,
        "query_window": None,
        "confidence": 0.30,
        "raw_notes": "imagen borrosa",
    },
)

_SONNET_RECOVERY = RecordedLLMResponse(
    tool_input={
        "intent": "log_expense",
        "dispatcher": "write",
        "amount": 3500,
        "currency": "CRC",
        "merchant": "farmacia",
        "category_hint": "salud",
        "account_hint": None,
        "occurred_at_hint": None,
        "query_window": None,
        "confidence": 0.82,
        "raw_notes": None,
    },
)


def _tiny_png() -> bytes:
    """Generate a minimal valid 1×1 white PNG in memory."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00\xff\xff\xff"
    compressed = zlib.compress(raw)
    idat = compressed
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


# ── helpers ───────────────────────────────────────────────────────────────────


def _override_db(session):
    async def _yield():
        yield session

    app.dependency_overrides[get_db] = _yield


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


async def _get_bearer(session, user_id) -> str:
    acct = Account(
        user_id=user_id,
        name="BAC",
        account_type="checking",
        currency="CRC",
        initial_balance=0,
        is_active=True,
    )
    session.add(acct)
    await session.commit()

    link = await generate_link(session, user_id=user_id, purpose="onboarding")
    _override_db(session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/auth/magic-link/exchange", json={"token": link.raw_token}
        )
    assert resp.status_code == 200
    return resp.json()["token"]


# ── 1. extract_vision: high confidence → single Haiku call ───────────────────


@pytest.mark.asyncio
async def test_extract_vision_single_pass(db_with_user):
    session, user_id = db_with_user
    from api.models.user import User as UserModel
    from sqlalchemy import select

    user = (
        await session.execute(select(UserModel).where(UserModel.id == user_id))
    ).scalar_one()

    call_count = 0

    class _CountingClient:
        async def extract(self, *, user_message, **kwargs) -> RecordedLLMResponse:
            nonlocal call_count
            call_count += 1
            return _HIGH_CONFIDENCE

    result = await extract_vision(
        user=user,
        image_bytes=_tiny_png(),
        image_media_type="image/png",
        client=_CountingClient(),
        haiku_model="claude-haiku-4-5",
        sonnet_model="claude-sonnet-4-5",
        db=session,
    )

    assert call_count == 1
    assert result.confidence == 0.88
    assert result.intent.value == "log_expense"


# ── 2. extract_vision: low confidence → Haiku + Sonnet retry ─────────────────


@pytest.mark.asyncio
async def test_extract_vision_retries_on_low_confidence(db_with_user):
    session, user_id = db_with_user
    from api.models.user import User as UserModel
    from sqlalchemy import select

    user = (
        await session.execute(select(UserModel).where(UserModel.id == user_id))
    ).scalar_one()

    responses = [_LOW_CONFIDENCE, _SONNET_RECOVERY]
    idx = 0

    class _StepClient:
        async def extract(self, *, user_message, model, **kwargs) -> RecordedLLMResponse:
            nonlocal idx
            r = responses[idx]
            idx += 1
            return r

    result = await extract_vision(
        user=user,
        image_bytes=_tiny_png(),
        image_media_type="image/png",
        client=_StepClient(),
        haiku_model="claude-haiku-4-5",
        sonnet_model="claude-sonnet-4-5",
        db=session,
    )

    assert idx == 2
    assert result.confidence == 0.82


# ── 3. /chat/image 415 on bad MIME ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_image_rejects_bad_mime(db_with_user):
    session, user_id = db_with_user
    set_llm_client(FixtureLLMClient(default=BASIC_EXPENSE_CRC))
    token = await _get_bearer(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/chat/image",
                files={"file": ("doc.pdf", b"%PDF", "application/pdf")},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 415
    finally:
        _clear_db_override()


# ── 4. /chat/image 413 on oversized payload ───────────────────────────────────


@pytest.mark.asyncio
async def test_chat_image_rejects_oversized(db_with_user):
    session, user_id = db_with_user
    set_llm_client(FixtureLLMClient(default=BASIC_EXPENSE_CRC))
    token = await _get_bearer(session, user_id)
    try:
        oversized = b"\xff\xd8\xff" + b"0" * (4 * 1024 * 1024 + 1)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/chat/image",
                files={"file": ("big.jpg", oversized, "image/jpeg")},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 413
    finally:
        _clear_db_override()


# ── 5. /chat/image happy path → proposal returned ────────────────────────────


@pytest.mark.asyncio
async def test_chat_image_happy_path_returns_proposal(db_with_user):
    """A valid receipt photo (mocked as tiny PNG) returns a proposal with
    confirm/cancel chips — same shape as a text-based write intent."""
    session, user_id = db_with_user
    set_llm_client(FixtureLLMClient(default=_HIGH_CONFIDENCE))
    token = await _get_bearer(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/chat/image",
                files={"file": ("receipt.png", _tiny_png(), "image/png")},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "reply_text" in body
        assert isinstance(body["buttons"], list)
        assert len(body["buttons"]) == 3
    finally:
        _clear_db_override()


# ── 6. process_message vision fast-path skips text branches ──────────────────


@pytest.mark.asyncio
async def test_process_message_image_skips_text_pipeline(db_with_user):
    """When image_bytes is provided, process_message bypasses commands,
    clarification, and pending-confirm checks and calls extract_vision."""
    session, user_id = db_with_user
    from api.models.user import User as UserModel
    from api.redis_client import get_redis
    from bot.pipeline import process_message
    from sqlalchemy import select

    user = (
        await session.execute(select(UserModel).where(UserModel.id == user_id))
    ).scalar_one()

    acct = Account(
        user_id=user_id,
        name="BAC",
        account_type="checking",
        currency="CRC",
        initial_balance=0,
        is_active=True,
    )
    session.add(acct)
    await session.commit()

    redis = get_redis()
    client = FixtureLLMClient(default=_HIGH_CONFIDENCE)

    with patch(
        "bot.pipeline.extract_vision",
        new=AsyncMock(
            return_value=(await _resolve_extraction(_HIGH_CONFIDENCE))
        ),
    ) as mock_vision:
        reply = await process_message(
            user=user,
            text="",
            db=session,
            redis=redis,
            llm_client=client,
            llm_model="claude-haiku-4-5",
            image_bytes=_tiny_png(),
            image_media_type="image/png",
            vision_model="claude-sonnet-4-5",
        )

    mock_vision.assert_awaited_once()
    assert reply.text != ""


async def _resolve_extraction(recorded: RecordedLLMResponse):
    """Convert a RecordedLLMResponse into an ExtractionResult."""
    from api.services.llm_extractor.schema import ExtractionResult

    return ExtractionResult.model_validate(recorded.tool_input)

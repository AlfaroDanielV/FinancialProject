"""Phase 6d B8 — lazy account detection in the write dispatcher."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from api.models.account import Account
from api.models.lazy_detection_event import LazyDetectionEvent
from api.models.user import User
from bot.pipeline import process_mock_extraction


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self.data[key] = value

    async def delete(self, key: str):
        self.data.pop(key, None)


def _raw(account_hint: str | None):
    return {
        "intent": "log_expense",
        "dispatcher": "write",
        "amount": 5000,
        "currency": None,
        "merchant": "super",
        "category_hint": "supermercado",
        "account_hint": account_hint,
        "occurred_at_hint": None,
        "query_window": None,
        "confidence": 0.95,
        "raw_notes": None,
    }


async def _user(session, user_id):
    user = await session.get(User, user_id)
    assert user is not None
    user.currency = "CRC"
    user.timezone = "America/Costa_Rica"
    return user


@pytest.mark.asyncio
async def test_unknown_bank_hint_prompts_create_or_link_and_records_pending_event(
    db_with_user,
):
    session, user_id = db_with_user
    session.add(
        Account(
            user_id=user_id,
            name="Efectivo",
            account_type="checking",
            initial_balance=Decimal("0"),
        )
    )
    await session.commit()

    reply = await process_mock_extraction(
        user=await _user(session, user_id),
        raw_extraction=_raw("BAC"),
        db=session,
        redis=_FakeRedis(),
    )

    assert "No tengo registrada una cuenta llamada BAC" in reply.text
    assert "crear" in reply.text
    # B16: the "link / SPA" option was dropped (no web). The prompt now offers
    # create-here, and the user can ignore it and continue (pipeline abandons
    # the soft awaiting_start state on unrelated input).
    assert "seguí" in reply.text

    rows = (
        await session.execute(
            select(LazyDetectionEvent).where(
                LazyDetectionEvent.user_id == user_id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].hint_type == "bank"
    assert rows[0].hint_text == "BAC"
    assert rows[0].resolution == "pending"
    assert rows[0].matched_account_id is None


@pytest.mark.asyncio
async def test_matching_bank_hint_links_existing_account_and_records_event(
    db_with_user,
):
    session, user_id = db_with_user
    account = Account(
        user_id=user_id,
        name="Cuenta BAC ahorros",
        account_type="checking",
        initial_balance=Decimal("0"),
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)

    reply = await process_mock_extraction(
        user=await _user(session, user_id),
        raw_extraction=_raw("BAC"),
        db=session,
        redis=_FakeRedis(),
    )

    assert "Cuenta BAC ahorros" in reply.text
    assert reply.buttons

    rows = (
        await session.execute(
            select(LazyDetectionEvent).where(
                LazyDetectionEvent.user_id == user_id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].hint_text == "BAC"
    assert rows[0].resolution == "linked_existing"
    assert rows[0].matched_account_id == account.id
    assert rows[0].fuzzy_match_score >= Decimal("0.85")

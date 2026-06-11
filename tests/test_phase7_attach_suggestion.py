"""B4 — deterministic, rate-limited "attach your fixed expenses" suggestion.

The query DISPATCHER (not the LLM) appends the nudge after a cashflow tool runs
and finds obligations with no envelope, once per conversation window.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from api.models.recurring_bill import RecurringBill
from api.models.user import User
from api.redis_client import get_redis
from app.queries.dispatcher import (
    _ATTACH_SUGGEST_KEY,
    _maybe_append_attach_suggestion,
)


def _bill(user_id):
    return RecurringBill(
        user_id=user_id, name="Internet", category="servicios",
        amount_expected=Decimal("30000"), currency="CRC", frequency="monthly",
        start_date=date.today(),
    )


@pytest.mark.asyncio
async def test_suggestion_appends_and_is_rate_limited(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    redis = get_redis()
    key = _ATTACH_SUGGEST_KEY.format(user_id=user_id)
    await redis.delete(key)
    session.add(_bill(user_id))  # unattached
    await session.commit()
    try:
        out = await _maybe_append_attach_suggestion(
            "Respuesta.", db=session, user=user, redis=redis,
            tools_used=[{"name": "assess_purchase"}],
        )
        assert out.startswith("Respuesta.")
        assert "Internet" in out  # names the unattached item
        assert "sin sobre" in out
        # Rate-limited: the second call (flag now set) does NOT append again.
        out2 = await _maybe_append_attach_suggestion(
            "Otra.", db=session, user=user, redis=redis,
            tools_used=[{"name": "assess_purchase"}],
        )
        assert out2 == "Otra."
    finally:
        await redis.delete(key)


@pytest.mark.asyncio
async def test_suggestion_skips_without_a_cashflow_tool(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    redis = get_redis()
    key = _ATTACH_SUGGEST_KEY.format(user_id=user_id)
    await redis.delete(key)
    session.add(_bill(user_id))
    await session.commit()
    out = await _maybe_append_attach_suggestion(
        "Resp.", db=session, user=user, redis=redis,
        tools_used=[{"name": "list_transactions"}],
    )
    assert out == "Resp."


@pytest.mark.asyncio
async def test_suggestion_skips_when_nothing_unattached(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    redis = get_redis()
    key = _ATTACH_SUGGEST_KEY.format(user_id=user_id)
    await redis.delete(key)
    out = await _maybe_append_attach_suggestion(
        "Resp.", db=session, user=user, redis=redis,
        tools_used=[{"name": "assess_purchase"}],
    )
    assert out == "Resp."

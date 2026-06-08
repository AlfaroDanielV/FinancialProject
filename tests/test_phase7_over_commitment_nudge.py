"""Phase 7 — proactive over-commitment nudge (surface 3).

The evaluator reuses the deterministic affordability engine: it fires when a
user's active fixed bills + debt minimums already consume >= OVER_COMMITMENT_RATIO
of monthly income. No income on file → no candidate (honesty rule). The engine
decides; the payload carries real numbers; the phrasing LLM only words them.

Prereqs: `docker compose up -d db && alembic upgrade head`.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from api.models.debt import Debt
from api.models.recurring_bill import RecurringBill
from api.models.recurring_income import RecurringIncome
from api.models.user_nudge import UserNudge, UserNudgeSilence
from api.services.nudges.delivery import buttons_for
from api.services.nudges.evaluators import OverCommitmentEvaluator
from api.services.nudges.orchestrator import evaluate_all
from api.services.nudges.phrasing import build_user_prompt
from api.services.nudges.policy import REASON_AUTO_DISMISSED_2X
from sqlalchemy import select


_NOW = datetime(2026, 6, 15, 15, 0, tzinfo=timezone.utc)
_MONTH_TAG = "2026-06"


async def _seed_income(session, user_id, *, amount="800000"):
    session.add(
        RecurringIncome(
            user_id=user_id,
            name="Salario",
            income_type="salary",
            amount=Decimal(amount),
            currency="CRC",
            frequency="monthly",
            next_payment_date=_NOW.date(),
        )
    )
    await session.commit()


async def _seed_fixed_bill(session, user_id, *, amount):
    session.add(
        RecurringBill(
            user_id=user_id,
            name="Alquiler",
            category="vivienda",
            amount_expected=Decimal(amount),
            currency="CRC",
            frequency="monthly",
            start_date=_NOW.date(),
        )
    )
    await session.commit()


async def _seed_debt(session, user_id, *, minimum):
    session.add(
        Debt(
            user_id=user_id,
            name="Préstamo",
            debt_type="loan",
            original_amount=Decimal("5000000"),
            current_balance=Decimal("4000000"),
            interest_rate=Decimal("0.20"),
            minimum_payment=Decimal(minimum),
            payment_due_day=1,
        )
    )
    await session.commit()


async def _nudges_for(session, user_id) -> list[UserNudge]:
    result = await session.execute(
        select(UserNudge).where(UserNudge.user_id == user_id)
    )
    return list(result.scalars().all())


# ── evaluator ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fires_when_commitments_eat_the_income(db_with_user):
    session, user_id = db_with_user
    # income 800k; fixed 400k + debt 300k = 700k committed → 87.5% ≥ 85% → fire.
    await _seed_income(session, user_id, amount="800000")
    await _seed_fixed_bill(session, user_id, amount="400000")
    await _seed_debt(session, user_id, minimum="300000")

    candidates = await OverCommitmentEvaluator().evaluate(
        session, _NOW, user_id=user_id
    )
    mine = [c for c in candidates if c.user_id == user_id]

    assert len(mine) == 1
    c = mine[0]
    assert c.dedup_key == f"over_commitment:{user_id}:{_MONTH_TAG}"
    assert c.priority == "normal"
    assert c.payload["currency"] == "CRC"
    assert c.payload["monthly_income"] == "800000.00"
    assert c.payload["monthly_fixed_expenses"] == "400000.00"
    assert c.payload["monthly_debt_payments"] == "300000.00"
    assert c.payload["monthly_disposable"] == "100000.00"
    assert c.payload["committed_ratio_pct"] >= 85
    assert c.payload["month_tag"] == _MONTH_TAG


@pytest.mark.asyncio
async def test_does_not_fire_when_healthy(db_with_user):
    session, user_id = db_with_user
    # income 800k; fixed 100k + debt 50k = 150k → 18.75% ≪ 85% → no fire.
    await _seed_income(session, user_id, amount="800000")
    await _seed_fixed_bill(session, user_id, amount="100000")
    await _seed_debt(session, user_id, minimum="50000")

    candidates = await OverCommitmentEvaluator().evaluate(
        session, _NOW, user_id=user_id
    )
    assert [c for c in candidates if c.user_id == user_id] == []


@pytest.mark.asyncio
async def test_does_not_fire_without_income(db_with_user):
    session, user_id = db_with_user
    # Heavy commitments but NO recurring income → honest no-verdict, no nudge.
    await _seed_fixed_bill(session, user_id, amount="400000")
    await _seed_debt(session, user_id, minimum="300000")

    candidates = await OverCommitmentEvaluator().evaluate(
        session, _NOW, user_id=user_id
    )
    assert [c for c in candidates if c.user_id == user_id] == []


@pytest.mark.asyncio
async def test_does_not_fire_with_income_and_no_commitments(db_with_user):
    session, user_id = db_with_user
    await _seed_income(session, user_id, amount="800000")

    candidates = await OverCommitmentEvaluator().evaluate(
        session, _NOW, user_id=user_id
    )
    assert [c for c in candidates if c.user_id == user_id] == []


@pytest.mark.asyncio
async def test_dedup_key_stable_within_month(db_with_user):
    session, user_id = db_with_user
    await _seed_income(session, user_id, amount="800000")
    await _seed_fixed_bill(session, user_id, amount="400000")
    await _seed_debt(session, user_id, minimum="300000")

    run1 = await OverCommitmentEvaluator().evaluate(session, _NOW, user_id=user_id)
    run2 = await OverCommitmentEvaluator().evaluate(
        session, _NOW + timedelta(hours=6), user_id=user_id
    )
    keys1 = {c.dedup_key for c in run1 if c.user_id == user_id}
    keys2 = {c.dedup_key for c in run2 if c.user_id == user_id}
    assert keys1 == keys2 == {f"over_commitment:{user_id}:{_MONTH_TAG}"}


# ── orchestrator ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_creates_and_dedups(db_with_user):
    session, user_id = db_with_user
    await _seed_income(session, user_id, amount="800000")
    await _seed_fixed_bill(session, user_id, amount="400000")
    await _seed_debt(session, user_id, minimum="300000")

    first = await evaluate_all(session, now=_NOW, user_id=user_id)
    await session.commit()
    second = await evaluate_all(
        session, now=_NOW + timedelta(hours=1), user_id=user_id
    )
    await session.commit()

    per_type = {c.nudge_type: c for c in first.per_type}
    assert per_type["over_commitment"].created == 1
    assert second.deduplicated >= 1
    nudges = await _nudges_for(session, user_id)
    over = [n for n in nudges if n.nudge_type == "over_commitment"]
    assert len(over) == 1  # no duplicate
    assert over[0].status == "pending"
    assert over[0].priority == "normal"


@pytest.mark.asyncio
async def test_orchestrator_respects_silence(db_with_user):
    session, user_id = db_with_user
    await _seed_income(session, user_id, amount="800000")
    await _seed_fixed_bill(session, user_id, amount="400000")
    await _seed_debt(session, user_id, minimum="300000")

    session.add(
        UserNudgeSilence(
            user_id=user_id,
            nudge_type="over_commitment",
            silenced_until=_NOW + timedelta(days=14),
            reason=REASON_AUTO_DISMISSED_2X,
        )
    )
    await session.commit()

    result = await evaluate_all(session, now=_NOW, user_id=user_id)
    await session.commit()

    per_type = {c.nudge_type: c for c in result.per_type}
    assert per_type["over_commitment"].created == 0
    assert per_type["over_commitment"].silenced == 1
    nudges = await _nudges_for(session, user_id)
    assert [n for n in nudges if n.nudge_type == "over_commitment"] == []


# ── phrasing + delivery wiring ───────────────────────────────────────────────


def test_phrasing_prompt_carries_real_numbers():
    payload = {
        "currency": "CRC",
        "monthly_income": "800000.00",
        "monthly_fixed_expenses": "400000.00",
        "monthly_debt_payments": "300000.00",
        "monthly_disposable": "100000.00",
        "committed_ratio_pct": 88,
        "threshold_pct": 85,
        "excluded_variable_bills": 0,
        "month_tag": _MONTH_TAG,
    }
    prompt = build_user_prompt("over_commitment", payload)
    # The LLM gets the exact figures so it never has to calculate or invent.
    assert "88%" in prompt
    assert "CRC 800,000" in prompt
    assert "CRC 400,000" in prompt
    assert "CRC 300,000" in prompt
    assert "CRC 100,000" in prompt
    # Instruction guards: no numeric advice, no invented amounts.
    assert "No des" in prompt or "no inventés" in prompt.lower()


def test_buttons_present_and_capped_at_three():
    buttons = buttons_for("over_commitment")
    assert len(buttons) == 3
    verbs = [b.verb for b in buttons]
    assert "act" in verbs and "dismiss" in verbs and "later" in verbs

"""Phase 7 — deterministic feasibility gate at conversational goal creation.

When a user proposes a goal with a deadline, the write dispatcher runs the
deterministic affordability engine and folds an honest, non-blocking verdict
into the proposal summary. The engine decides; the copy only words it. The goal
still proposes (and can be confirmed) either way — pushback informs, never vetoes.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from api.models.debt import Debt
from api.models.recurring_bill import RecurringBill
from api.models.recurring_income import RecurringIncome
from api.models.user import User
from api.services.llm_extractor import ExtractionResult, Intent
from api.services.telegram_dispatcher import ProposeAction, dispatch

_TODAY = date(2026, 5, 31)


def _goal_extraction(**overrides) -> ExtractionResult:
    base = dict(
        intent=Intent.CREATE_GOAL,
        dispatcher="write",
        goal_name="vacaciones",
        goal_target_amount=Decimal("1200000"),
        confidence=0.9,
    )
    base.update(overrides)
    return ExtractionResult(**base)


async def _seed_income(session, user_id, *, amount="800000"):
    session.add(
        RecurringIncome(
            user_id=user_id,
            name="Salario",
            income_type="salary",
            amount=Decimal(amount),
            currency="CRC",
            frequency="monthly",
            next_payment_date=_TODAY,
        )
    )
    await session.commit()


async def _seed_heavy_commitments(session, user_id):
    """Fixed bills + debt that swallow most of the income."""
    session.add_all(
        [
            RecurringBill(
                user_id=user_id,
                name="Alquiler",
                category="vivienda",
                amount_expected=Decimal("400000"),
                currency="CRC",
                frequency="monthly",
                start_date=_TODAY,
            ),
            Debt(
                user_id=user_id,
                name="Préstamo",
                debt_type="loan",
                original_amount=Decimal("5000000"),
                current_balance=Decimal("4000000"),
                interest_rate=Decimal("0.20"),
                minimum_payment=Decimal("300000"),
                payment_due_day=1,
            ),
        ]
    )
    await session.commit()


@pytest.mark.asyncio
async def test_feasible_goal_says_it_fits(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed_income(session, user_id, amount="800000")  # disposable 800k, safe 640k

    # 1,200,000 over 12 months → 100,000/mes ≤ 640,000 safe → feasible.
    decision = await dispatch(
        extraction=_goal_extraction(goal_target_date="en 12 meses"),
        user=user,
        today=_TODAY,
        db=session,
    )
    assert isinstance(decision, ProposeAction)
    assert "te alcanza" in decision.summary_es
    assert "/mes" in decision.summary_es


@pytest.mark.asyncio
async def test_infeasible_goal_pushes_back_with_alternative(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed_income(session, user_id, amount="800000")
    await _seed_heavy_commitments(session, user_id)
    # disposable = 800k − 400k − 300k = 100k; safe = 80k.

    # 1,200,000 over 2 months → 600,000/mes ≫ 80,000 safe → infeasible.
    decision = await dispatch(
        extraction=_goal_extraction(goal_target_date="en 2 meses"),
        user=user,
        today=_TODAY,
        db=session,
    )
    assert isinstance(decision, ProposeAction)
    # Honest pushback + a real alternative (extend / lower), never a veto.
    assert "te faltarían" in decision.summary_es
    assert "extender" in decision.summary_es
    # Still a proposal the user can confirm — pushback informs, doesn't block.
    assert decision.payload["action_type"] == "create_goal"


@pytest.mark.asyncio
async def test_over_committed_goal_explains_no_disposable(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed_income(session, user_id, amount="600000")
    await _seed_heavy_commitments(session, user_id)  # 400k + 300k = 700k > 600k income

    decision = await dispatch(
        extraction=_goal_extraction(goal_target_date="en 6 meses"),
        user=user,
        today=_TODAY,
        db=session,
    )
    assert isinstance(decision, ProposeAction)
    assert "consumen tu ingreso" in decision.summary_es


@pytest.mark.asyncio
async def test_no_income_does_not_fabricate_a_verdict(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    # No income seeded → engine returns feasible=None; we say so honestly.

    decision = await dispatch(
        extraction=_goal_extraction(goal_target_date="en 10 meses"),
        user=user,
        today=_TODAY,
        db=session,
    )
    assert isinstance(decision, ProposeAction)
    assert "/mes" in decision.summary_es
    assert "no puedo confirmar si te alcanza" in decision.summary_es


@pytest.mark.asyncio
async def test_no_deadline_skips_the_gate(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed_income(session, user_id)

    # No target_date → no horizon → no monthly line, no feasibility claim.
    decision = await dispatch(
        extraction=_goal_extraction(goal_target_date=None),
        user=user,
        today=_TODAY,
        db=session,
    )
    assert isinstance(decision, ProposeAction)
    assert "/mes" not in decision.summary_es
    assert "te alcanza" not in decision.summary_es

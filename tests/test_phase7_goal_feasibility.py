"""Phase 7 — deterministic feasibility gate at conversational goal creation.

When a user proposes a goal with a deadline, the write dispatcher runs the
deterministic affordability engine (now over the envelope-aware SURPLUS —
[[Decision - Unified Monthly Cashflow]]) and folds an honest, non-blocking
verdict into the proposal summary. The engine decides; the copy only words it.
The goal still proposes (and can be confirmed) either way — pushback informs,
never vetoes. When the cashflow is gated, the copy asks for the DISTINCT action
(register income / build budget / cover obligations) instead of a verdict.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from api.models.debt import Debt
from api.models.envelope import Envelope
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


async def _seed_envelopes(session, user_id, specs):
    """specs: list of (name, envelope_class, limit)."""
    session.add_all(
        [
            Envelope(
                user_id=user_id,
                name=name,
                envelope_class=klass,
                limit_amount=Decimal(limit),
                currency="CRC",
            )
            for name, klass, limit in specs
        ]
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


async def _propose(session, user, **extraction_overrides) -> ProposeAction:
    decision = await dispatch(
        extraction=_goal_extraction(**extraction_overrides),
        user=user,
        today=_TODAY,
        db=session,
    )
    assert isinstance(decision, ProposeAction)
    return decision


@pytest.mark.asyncio
async def test_feasible_goal_says_it_fits(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed_income(session, user_id, amount="800000")
    await _seed_envelopes(session, user_id, [("Gastos", "needs", "200000")])
    # committed 200k → surplus 600k, safe 480k. 1.2M over 12 → 100k ≤ 480k → fits.

    decision = await _propose(session, user, goal_target_date="en 12 meses")
    assert "te alcanza con tu sobrante" in decision.summary_es
    assert "/mes" in decision.summary_es


@pytest.mark.asyncio
async def test_infeasible_goal_pushes_back_with_alternative(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed_income(session, user_id, amount="800000")
    # committed 700k (incl. 200k savings) → surplus 100k, safe 80k.
    await _seed_envelopes(
        session, user_id, [("Gastos", "needs", "500000"), ("Ahorro", "savings", "200000")]
    )

    # 1,200,000 over 2 months → 600,000/mes ≫ 80,000 safe → infeasible.
    decision = await _propose(session, user, goal_target_date="en 2 meses")
    # Honest pushback + a real alternative (extend / lower), never a veto.
    assert "te faltarían" in decision.summary_es
    assert "extender" in decision.summary_es
    # Savings/investing reallocation offered as the user's option (sub-decision B).
    assert "podés reasignar" in decision.summary_es
    assert decision.payload["action_type"] == "create_goal"


@pytest.mark.asyncio
async def test_deficit_goal_explains_envelopes_consume_income(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed_income(session, user_id, amount="600000")
    await _seed_envelopes(session, user_id, [("Gastos", "needs", "700000")])
    # committed 700k > income 600k → surplus −100k (reliable deficit).

    decision = await _propose(session, user, goal_target_date="en 6 meses")
    assert "consumen tu ingreso" in decision.summary_es


@pytest.mark.asyncio
async def test_no_budget_gate_asks_to_build_one(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed_income(session, user_id, amount="800000")  # income but NO envelopes

    decision = await _propose(session, user, goal_target_date="en 12 meses")
    assert "Armá tus sobres" in decision.summary_es
    assert "te faltarían" not in decision.summary_es  # no verdict when gated


@pytest.mark.asyncio
async def test_under_coverage_gate_asks_to_cover_obligations(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed_income(session, user_id, amount="800000")
    await _seed_heavy_commitments(session, user_id)  # bills 400k + debt 300k = 700k
    await _seed_envelopes(session, user_id, [("Gastos", "needs", "100000")])  # < 700k

    decision = await _propose(session, user, goal_target_date="en 12 meses")
    assert "no cubren tus deudas" in decision.summary_es


@pytest.mark.asyncio
async def test_no_income_does_not_fabricate_a_verdict(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    # No income seeded → no_income gate wins; we say so honestly.

    decision = await _propose(session, user, goal_target_date="en 10 meses")
    assert "/mes" in decision.summary_es
    assert "no puedo confirmar si te alcanza" in decision.summary_es
    assert "Registralos" in decision.summary_es


@pytest.mark.asyncio
async def test_no_deadline_skips_the_gate(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed_income(session, user_id)
    await _seed_envelopes(session, user_id, [("Gastos", "needs", "200000")])

    # No target_date → no horizon → no monthly line, no feasibility claim.
    decision = await _propose(session, user, goal_target_date=None)
    assert "/mes" not in decision.summary_es
    assert "te alcanza" not in decision.summary_es

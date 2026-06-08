"""Phase 7a — context-aware pushback (envelope execution + upcoming obligations).

The affordability engine's HEADLINE verdict is unchanged (income − fixed − debt,
80% margin). These tests prove the new `gather_financial_context` surfaces the
envelope-execution + upcoming-bill/event signals, that the chat tool carries
them WITHOUT changing the verdict, and that the goal-creation gate appends a
non-blocking heads-up when there's something worth flagging.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from api.models.custom_event import CustomEvent
from api.models.envelope import Envelope
from api.models.recurring_income import RecurringIncome
from api.models.transaction import Transaction
from api.models.user import User
from api.services.finance.affordability import gather_financial_context
from api.services.llm_extractor import ExtractionResult, Intent
from api.services.telegram_dispatcher import ProposeAction, dispatch
from app.queries.tools.affordability import assess_purchase


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


async def _seed_over_limit_envelope(session, user_id):
    """A 'wants' envelope with a 20k cap and 25k spent → 5k over."""
    env = Envelope(
        user_id=user_id, name="Ocio", envelope_class="wants",
        limit_amount=Decimal("20000"), currency="CRC", depth=1,
    )
    session.add(env)
    await session.flush()
    session.add(
        Transaction(
            user_id=user_id, amount=-25000, currency="CRC",
            transaction_date=date.today(), source="manual",
            status="confirmed", envelope_id=env.id,
        )
    )


async def _seed_upcoming_event(session, user_id, *, days=10, amount="600000"):
    session.add(
        CustomEvent(
            user_id=user_id, title="Seguro del carro", event_type="reminder",
            event_date=date.today() + timedelta(days=days),
            amount=Decimal(amount), currency="CRC", is_active=True,
        )
    )


async def _seed_income(session, user_id, *, amount="800000"):
    session.add(
        RecurringIncome(
            user_id=user_id, name="Salario", income_type="salary",
            amount=Decimal(amount), currency="CRC", frequency="monthly",
            next_payment_date=date.today(),
        )
    )


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


# ── gather_financial_context ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gather_context_envelope_pressure_and_upcoming(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed_over_limit_envelope(session, user_id)
    await _seed_upcoming_event(session, user_id)
    await session.commit()

    ctx = await gather_financial_context(session, user=user)

    assert ctx.envelope_pressure is not None
    assert ctx.envelope_pressure.total_spent == Decimal("25000.00")
    over = ctx.envelope_pressure.over_limit
    assert [o.name for o in over] == ["Ocio"]
    assert over[0].overage == Decimal("5000.00")

    titles = [o.title for o in ctx.upcoming_obligations]
    assert "Seguro del carro" in titles
    seguro = next(o for o in ctx.upcoming_obligations if o.title == "Seguro del carro")
    assert seguro.amount == Decimal("600000.00")
    assert seguro.kind == "event"
    assert ctx.upcoming_total >= Decimal("600000.00")


@pytest.mark.asyncio
async def test_gather_context_empty_when_nothing_seeded(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    ctx = await gather_financial_context(session, user=user)
    assert ctx.envelope_pressure is None
    assert ctx.upcoming_obligations == ()
    assert ctx.upcoming_total == Decimal("0.00")


# ── assess_purchase tool: context present, verdict unchanged ─────────────────


@pytest.mark.asyncio
async def test_assess_purchase_carries_context_without_changing_verdict(
    db_with_user, monkeypatch
):
    session, user_id = db_with_user
    monkeypatch.setattr(
        "app.queries.tools.affordability.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )
    # income 800k, no bills/debt → disposable 800k, safe 640k.
    await _seed_income(session, user_id, amount="800000")
    # envelope + upcoming context that must NOT move the verdict.
    await _seed_over_limit_envelope(session, user_id)
    await _seed_upcoming_event(session, user_id)
    await session.commit()

    result = await assess_purchase(amount=300000, user_id=user_id)

    # Verdict is the pure income−fixed−debt math, untouched by context.
    assert result["feasible"] is True
    assert result["monthly_disposable"] == "800000.00"
    assert result["safe_monthly_disposable"] == "640000.00"

    # Context block is populated alongside the verdict.
    ctx = result["context"]
    assert ctx["envelope_pressure"] is not None
    assert any(o["name"] == "Ocio" for o in ctx["envelope_pressure"]["over_limit"])
    assert any(
        o["title"] == "Seguro del carro" for o in ctx["upcoming_obligations"]
    )


# ── goal-creation gate: non-blocking context note ────────────────────────────


@pytest.mark.asyncio
async def test_goal_gate_appends_over_limit_note(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed_income(session, user_id, amount="800000")
    await _seed_over_limit_envelope(session, user_id)
    await session.commit()

    decision = await dispatch(
        extraction=_goal_extraction(goal_target_date="en 12 meses"),
        user=user,
        today=date.today(),
        db=session,
    )
    assert isinstance(decision, ProposeAction)
    # Still feasible (100k/mes ≤ 640k safe) AND carries the heads-up.
    assert "te alcanza" in decision.summary_es
    assert "Ojo: ya te pasaste del sobre Ocio" in decision.summary_es
    # Pushback never vetoes — it's still a confirmable proposal.
    assert decision.payload["action_type"] == "create_goal"


@pytest.mark.asyncio
async def test_goal_gate_no_note_when_clean(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed_income(session, user_id, amount="800000")
    await session.commit()

    decision = await dispatch(
        extraction=_goal_extraction(goal_target_date="en 12 meses"),
        user=user,
        today=date.today(),
        db=session,
    )
    assert isinstance(decision, ProposeAction)
    assert "Ojo:" not in decision.summary_es

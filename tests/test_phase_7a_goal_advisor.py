"""Phase 7a advisor — goal query tools + cross-dispatcher context bridge.

Two gaps made the advisor give unrealistic savings plans ("ahorrá ₡1.000.000/
mes" ignoring fixed costs + debt) for a just-created goal:

1. The read-only dispatcher had no way to see goals, so "¿cómo alcanzo esa
   meta?" couldn't be grounded in the real target.
2. Goal creation runs through the write dispatcher, which never wrote to the
   query dispatcher's history — so "esa meta" had no referent.

These tests cover the fix: `list_goals` / `assess_goal` (the latter reuses the
deterministic affordability engine so the plan accounts for income − fixed −
debt) and the bridge that lands a created goal in `query_history`.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.main import app
from api.models.debt import Debt
from api.models.envelope import Envelope
from api.models.goal import Goal
from api.models.recurring_bill import RecurringBill
from api.models.recurring_income import RecurringIncome
from api.services.auth.magic_link import generate_link
from app.queries.history import load_history
from app.queries.tools.goals import assess_goal, list_goals


# --------------------------------------------------------------------------- #
# Direct-call tool tests (LLM routing is separate)                            #
# --------------------------------------------------------------------------- #


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _patch_session(monkeypatch, session):
    monkeypatch.setattr(
        "app.queries.tools.goals.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )


def _twelve_months_out() -> date:
    today = date.today()
    # First day of the same month next year → months_between == 12 exactly.
    return date(today.year + 1, today.month, 1)


async def _seed_goal(
    session,
    user_id,
    *,
    name: str,
    target: str,
    target_date: date | None = None,
    current: str = "0",
    status: str = "active",
) -> Goal:
    goal = Goal(
        user_id=user_id,
        name=name,
        target_amount=Decimal(target),
        target_currency="CRC",
        current_amount=Decimal(current),
        target_date=target_date,
        status=status,
    )
    session.add(goal)
    await session.commit()
    await session.refresh(goal)
    return goal


async def _seed_finances(session, user_id):
    """income 800,000; bills 150,000 + debt 100,000 = 250,000 obligations; a
    250,000 'needs' envelope covers them exactly → committed 250,000, surplus
    550,000, safe = 440,000. Mirrors tests/test_affordability.py exactly."""
    session.add_all(
        [
            RecurringIncome(
                user_id=user_id,
                name="Salario",
                income_type="salary",
                amount=Decimal("800000"),
                currency="CRC",
                frequency="monthly",
                next_payment_date=date.today(),
            ),
            RecurringBill(
                user_id=user_id,
                name="Internet",
                category="servicios",
                amount_expected=Decimal("150000"),
                currency="CRC",
                frequency="monthly",
                start_date=date.today(),
            ),
            Debt(
                user_id=user_id,
                name="Préstamo personal",
                debt_type="loan",
                original_amount=Decimal("3000000"),
                current_balance=Decimal("2000000"),
                interest_rate=Decimal("0.18"),
                minimum_payment=Decimal("100000"),
                payment_due_day=1,
            ),
            Envelope(
                user_id=user_id,
                name="Gastos",
                envelope_class="needs",
                limit_amount=Decimal("250000"),
                currency="CRC",
            ),
        ]
    )
    await session.commit()


@pytest.mark.asyncio
async def test_list_goals_returns_only_plannable(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_goal(session, user_id, name="iPhone", target="2000000")
    await _seed_goal(session, user_id, name="Viaje", target="1500000")
    await _seed_goal(
        session, user_id, name="Marchamo viejo", target="300000", status="achieved"
    )

    result = await list_goals(user_id=user_id)
    names = {g["name"] for g in result["goals"]}
    assert result["count"] == 2
    assert names == {"iPhone", "Viaje"}  # achieved goal is excluded


@pytest.mark.asyncio
async def test_assess_goal_grounds_plan_in_engine(db_with_user, monkeypatch):
    """The anti-"unreal ₡1M/mes" regression: the plan must subtract fixed
    expenses + debt, not propose a naive target/timeline figure as affordable."""
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_finances(session, user_id)
    # 2,640,000 over 12 months → needed 220,000 ≤ safe 440,000 → feasible.
    await _seed_goal(
        session,
        user_id,
        name="iPhone",
        target="2640000",
        target_date=_twelve_months_out(),
    )

    # Case-insensitive partial match resolves the goal from "el iphone".
    result = await assess_goal(goal_name="iphone", user_id=user_id)

    assert result["matched"] is True
    assert result["timeline_months"] == 12
    assert result["timeline_source"] == "deadline"
    assert result["has_deadline"] is True
    assert result["monthly_income"] == "800000.00"
    assert result["monthly_fixed_expenses"] == "150000.00"
    assert result["monthly_debt_payments"] == "100000.00"
    # surplus already nets out the committed budget — this is the whole point.
    assert result["committed_outflows"] == "250000.00"
    assert result["surplus"] == "550000.00"
    assert result["safe_surplus"] == "440000.00"
    assert result["gate_reason"] is None
    assert result["monthly_needed"] == "220000.00"
    assert result["feasible"] is True
    assert result["shortfall"] == "0.00"


@pytest.mark.asyncio
async def test_assess_goal_infeasible_offers_alternatives(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_finances(session, user_id)
    # 13,200,000 over 12 months → needed 1,100,000 > safe 440,000.
    await _seed_goal(
        session,
        user_id,
        name="Carro",
        target="13200000",
        target_date=_twelve_months_out(),
    )

    result = await assess_goal(goal_name="Carro", user_id=user_id)
    assert result["feasible"] is False
    assert result["monthly_needed"] == "1100000.00"
    assert result["shortfall"] == "660000.00"
    # ceil(13,200,000 / 440,000) = 30 months to make it fit at the safe rate.
    assert result["min_timeline_months_feasible"] == 30


@pytest.mark.asyncio
async def test_assess_goal_no_deadline_uses_safe_horizon(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_finances(session, user_id)
    # No target_date → modeled as immediate; min_timeline gives the real horizon.
    await _seed_goal(session, user_id, name="Fondo", target="4400000")

    result = await assess_goal(goal_name="Fondo", user_id=user_id)
    assert result["has_deadline"] is False
    assert result["timeline_source"] == "none"
    assert result["timeline_months"] == 1
    # ceil(4,400,000 / 440,000) = 10 months at the safe disposable rate.
    assert result["min_timeline_months_feasible"] == 10


@pytest.mark.asyncio
async def test_assess_goal_no_income_is_unknown(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    # Goal but no recurring income → engine returns feasible=None honestly.
    await _seed_goal(
        session, user_id, name="iPhone", target="2000000",
        target_date=_twelve_months_out(),
    )

    result = await assess_goal(goal_name="iPhone", user_id=user_id)
    assert result["matched"] is True
    assert result["feasible"] is None
    assert result["gate_reason"] == "no_income"
    assert result["surplus"] is None
    assert any("ingresos recurrentes" in n for n in result["notes"])


@pytest.mark.asyncio
async def test_assess_goal_already_funded(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_finances(session, user_id)
    await _seed_goal(
        session, user_id, name="Tele", target="500000", current="500000"
    )

    result = await assess_goal(goal_name="Tele", user_id=user_id)
    assert result["already_funded"] is True
    assert result["feasible"] is None
    assert result["monthly_needed"] == "0.00"
    assert any("alcanzaste" in n for n in result["notes"])


@pytest.mark.asyncio
async def test_assess_goal_unknown_name_lists_available(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_goal(session, user_id, name="iPhone", target="2000000")

    result = await assess_goal(goal_name="carro nuevo", user_id=user_id)
    assert result["matched"] is False
    assert result["available_goal_names"] == ["iPhone"]


@pytest.mark.asyncio
async def test_assess_goal_ambiguous_needs_disambiguation(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_goal(session, user_id, name="Viaje a Europa", target="3000000")
    await _seed_goal(session, user_id, name="Viaje a México", target="1000000")

    result = await assess_goal(goal_name="viaje", user_id=user_id)
    assert result["matched"] is False
    assert result["needs_disambiguation"] is True
    assert len(result["candidates"]) == 2


@pytest.mark.asyncio
async def test_assess_goal_timeline_override_wins(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_finances(session, user_id)
    await _seed_goal(
        session, user_id, name="iPhone", target="4400000",
        target_date=_twelve_months_out(),
    )

    # User proposes a different horizon than the deadline.
    result = await assess_goal(
        goal_name="iPhone", timeline_months_override=22, user_id=user_id
    )
    assert result["timeline_source"] == "override"
    assert result["timeline_months"] == 22
    assert result["monthly_needed"] == "200000.00"  # 4,400,000 / 22
    assert result["feasible"] is True


def test_bridged_actions_exclude_raw_logs():
    """Raw expense/income captures must NOT be bridged into advisor history —
    only conversational creation turns. Otherwise the 10-entry window floods
    with capture noise and crowds out real conversation."""
    from bot.pipeline import _BRIDGED_CREATION_ACTIONS

    assert "log_expense" not in _BRIDGED_CREATION_ACTIONS
    assert "log_income" not in _BRIDGED_CREATION_ACTIONS
    assert "create_goal" in _BRIDGED_CREATION_ACTIONS


# --------------------------------------------------------------------------- #
# Context bridge — integration through the chat pipeline                       #
# --------------------------------------------------------------------------- #


def _override_db(session):
    async def _yield():
        yield session

    app.dependency_overrides[get_db] = _yield


async def _chat(client: AsyncClient, text: str, bearer: str):
    return await client.post(
        "/api/v1/chat/message",
        json={"text": text},
        headers={"Authorization": f"Bearer {bearer}"},
    )


@pytest.mark.asyncio
async def test_goal_creation_bridges_into_query_history(db_with_user):
    """After creating a goal in chat, the proposal + confirmation land in the
    shared query_history so the advisor can resolve a follow-up "¿cómo alcanzo
    esa meta?"."""
    from api.redis_client import get_redis
    from api.services.llm_extractor import FixtureLLMClient
    from bot.app import set_llm_client
    from tests.fixtures.extractor_responses import CREATE_GOAL_NAMED

    session, user_id = db_with_user
    set_llm_client(FixtureLLMClient(default=CREATE_GOAL_NAMED))
    link = await generate_link(session, user_id=user_id, purpose="onboarding")
    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            exchange = await ac.post(
                "/api/v1/auth/magic-link/exchange", json={"token": link.raw_token}
            )
            assert exchange.status_code == 200, exchange.text
            token = exchange.json()["token"]

            propose = await _chat(
                ac, "creá una meta de fondo de emergencia de 500 mil", token
            )
            assert propose.status_code == 200, propose.text
            confirm = await _chat(ac, "sí", token)
            assert confirm.status_code == 200, confirm.text
            assert "Meta creada" in confirm.json()["reply_text"]

        # The goal narrative is now visible to the read-only advisor.
        turns = await load_history(user_id, redis=get_redis())
        assistant_blob = "\n".join(
            t.content for t in turns if t.role == "assistant"
        )
        assert "fondo de emergencia" in assistant_blob.lower()
        assert "Meta creada" in assistant_blob
    finally:
        app.dependency_overrides.pop(get_db, None)

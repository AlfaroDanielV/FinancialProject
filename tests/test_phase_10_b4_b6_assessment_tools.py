"""P10 B4–B6 — the advisory assessment tools.

Locks: `get_net_worth` delegates to the single net-worth owner (per-currency,
never ₡+$-summed) and is advice-traced; `assess_counterfactual` re-frames the
affordability engine's OWN alternatives as levers (never new math);
`build_multiyear_plan` composes saving-the-prima + the financing cuota and is
explicitly LINEAR; `project_retirement` returns the three SUPEN bands with the
regulator's disclaimer, asks for the gross when only a net is on file, and
never silently uses net as gross. All four ship ONLY via ADVISORY_TOOLSET.

Prereqs: `docker compose up -d db redis` + `alembic upgrade head`.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from api.models.advice_event import AdviceEvent
from api.models.envelope import Envelope
from api.models.recurring_income import RecurringIncome
from api.models.user import User
from app.queries.tools import ADVISORY_ONLY_TOOLS, BASE_TOOLSET
from app.queries.tools.advisory_planning import (
    assess_counterfactual,
    build_multiyear_plan,
)
from app.queries.tools.net_worth import get_net_worth
from app.queries.tools.retirement import project_retirement

_TODAY = date(2026, 7, 3)


def _income(user_id, **overrides) -> RecurringIncome:
    base = dict(
        user_id=user_id,
        name="Salario",
        income_type="salary",
        amount=Decimal("800000"),
        currency="CRC",
        frequency="monthly",
        next_payment_date=_TODAY,
    )
    base.update(overrides)
    return RecurringIncome(**base)


def _envelope(user_id, **overrides) -> Envelope:
    base = dict(
        user_id=user_id,
        name="Gastos",
        envelope_class="needs",
        limit_amount=Decimal("300000"),
        currency="CRC",
    )
    base.update(overrides)
    return Envelope(**base)


# ── toolset membership ────────────────────────────────────────────────────────


def test_assessment_tools_are_advisory_only():
    for name in (
        "get_net_worth",
        "assess_counterfactual",
        "build_multiyear_plan",
        "project_retirement",
    ):
        assert name in ADVISORY_ONLY_TOOLS
        assert name not in BASE_TOOLSET


# ── B4: get_net_worth ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_net_worth_per_currency_and_traced(db_with_user):
    from api.models.account import Account
    from api.models.debt import Debt

    session, user_id = db_with_user
    session.add_all(
        [
            Account(
                user_id=user_id, name="BAC", account_type="checking",
                currency="CRC", initial_balance=Decimal("500000"),
            ),
            Account(
                user_id=user_id, name="BAC USD", account_type="savings",
                currency="USD", initial_balance=Decimal("1000"),
            ),
        ]
    )
    session.add(
        Debt(
            user_id=user_id, name="Préstamo", debt_type="personal_loan",
            original_amount=Decimal("2000000"), current_balance=Decimal("800000"),
            interest_rate=Decimal("0.12"), minimum_payment=Decimal("60000"),
            payment_due_day=15,
        )
    )
    await session.commit()

    payload = await get_net_worth(user_id=user_id)
    by_ccy = {e["currency"]: e for e in payload["entries"]}
    assert by_ccy["CRC"]["assets"] == "500000.00"
    assert by_ccy["CRC"]["liabilities"] == "800000.00"
    assert by_ccy["CRC"]["net"] == "-300000.00"
    assert by_ccy["USD"]["net"] == "1000.00"
    # No single combined figure anywhere in the payload (D3).
    assert "total" not in payload
    # Advice-traced.
    rows = (
        await session.execute(
            select(AdviceEvent).where(
                AdviceEvent.user_id == user_id, AdviceEvent.kind == "net_worth"
            )
        )
    ).scalars().all()
    assert len(rows) == 1


# ── B5: assess_counterfactual ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_counterfactual_gated_without_income(db_with_user):
    session, user_id = db_with_user
    payload = await assess_counterfactual(amount=1_000_000, user_id=user_id)
    assert payload["feasible"] is None
    assert payload["gate_reason"] == "no_income"
    assert "gate_guidance" in payload


@pytest.mark.asyncio
async def test_counterfactual_returns_engine_levers(db_with_user):
    session, user_id = db_with_user
    session.add(_income(user_id))
    session.add(_envelope(user_id, name="Vida", limit_amount=Decimal("600000")))
    session.add(
        _envelope(user_id, name="Colchón", envelope_class="savings",
                  limit_amount=Decimal("100000"))
    )
    await session.commit()

    # surplus = 800k − 700k = 100k; safe = 80k. Wanting 1.2M in 6 months
    # (200k/mo) is NOT feasible → the engine's levers must surface.
    payload = await assess_counterfactual(
        amount=1_200_000, timeline_months=6, user_id=user_id
    )
    assert payload["feasible"] is False
    levers = payload["levers"]
    assert levers["wait_longer_months"] == 15  # ceil(1.2M / 80k)
    assert levers["buy_cheaper_max_amount"] == "480000.00"  # 80k × 6
    assert levers["monthly_shortfall"] == "120000.00"  # 200k − 80k
    names = [c["name"] for c in levers["reallocation_candidates"]]
    assert "Vida" in names or "Colchón" in names


# ── B5: build_multiyear_plan ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_multiyear_plan_is_linear_and_composes_financing(db_with_user):
    session, user_id = db_with_user
    session.add(_income(user_id))
    session.add(_envelope(user_id, limit_amount=Decimal("500000")))
    await session.commit()

    payload = await build_multiyear_plan(
        target_amount=50_000_000, timeline_years=5, down_payment_pct=20.0,
        annual_rate_pct=9.5, term_months=360, user_id=user_id,
    )
    assert payload["linear_no_inflation"] is True
    savings = payload["savings_phase"]
    assert savings["down_payment_needed"] == "10000000.00"
    # 10M over 60 months ≈ 166 666.67/mo vs safe 240k → feasible.
    assert savings["monthly_needed"] == "166666.67"
    assert savings["feasible"] is True
    fin = payload["financing_phase"]
    assert fin is not None
    assert fin["financed_principal"] == "40000000.00"
    assert Decimal(fin["monthly_payment"]) > 0


@pytest.mark.asyncio
async def test_multiyear_plan_without_rate_asks_never_invents(db_with_user):
    session, user_id = db_with_user
    payload = await build_multiyear_plan(
        target_amount=50_000_000, timeline_years=5, user_id=user_id
    )
    assert payload["financing_phase"] is None
    assert "tasa" in payload["financing_note"]


# ── B6: project_retirement ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retirement_needs_gross_never_uses_net(db_with_user):
    session, user_id = db_with_user
    # A NET-only salary on file must NOT be used as gross.
    session.add(_income(user_id, amount=Decimal("1271700")))
    await session.commit()
    payload = await project_retirement(
        edad_actual=40, cuotas_ivm=180, saldo_rop=5_000_000, user_id=user_id
    )
    assert payload["needs_input"] == "salario_bruto_mensual"


@pytest.mark.asyncio
async def test_retirement_uses_registered_gross(db_with_user):
    session, user_id = db_with_user
    session.add(
        _income(
            user_id,
            amount=Decimal("635850"),  # per-payment net (quincenal)
            frequency="biweekly",
            gross_monthly=Decimal("1500000"),
        )
    )
    await session.commit()
    payload = await project_retirement(
        edad_actual=40, cuotas_ivm=240, saldo_rop=8_000_000, user_id=user_id
    )
    assert "bandas" in payload
    assert payload["salario_base"] == 1_500_000
    assert payload["spr_es_aproximacion"] is True


@pytest.mark.asyncio
async def test_retirement_three_bands_with_disclaimer(db_with_user):
    from app.domain.retirement import SUPEN_DISCLAIMER_ES

    session, user_id = db_with_user
    payload = await project_retirement(
        edad_actual=45, cuotas_ivm=300, saldo_rop=12_000_000,
        salario_bruto_mensual=1_200_000, salario_referencia=1_000_000,
        sexo="m", user_id=user_id,
    )
    bandas = payload["bandas"]
    assert [b["escenario"] for b in bandas] == ["pesimista", "esperado", "optimista"]
    assert payload["disclaimer"] == SUPEN_DISCLAIMER_ES
    assert payload["spr_es_aproximacion"] is False
    # Every band totals IVM + ROP; pesimista never dropped.
    for b in bandas:
        assert b["pension_total"] == b["pension_ivm"] + b["pension_rop"]
    # Trace row written.
    rows = (
        await session.execute(
            select(AdviceEvent).where(
                AdviceEvent.user_id == user_id,
                AdviceEvent.kind == "retirement_projection",
            )
        )
    ).scalars().all()
    assert len(rows) == 1

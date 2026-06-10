"""Phase 7 — financing advisor (loan simulation) + advise-first debt handoff.

Two surfaces:
- ``assess_financing`` read-only query tool: the user is EXPLORING ("¿cuánto
  sería la cuota?", "si lo financio a N años al T%") → deterministic French
  amortization + affordability verdict, no registration.
- ``_dispatch_create_debt`` advise-first: when the register-debt extraction
  already carries principal + rate + term, the form handoff leads with the
  deterministic cost instead of opening blind.

Prereqs: `docker compose up -d db && alembic upgrade head`.
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
from api.services.telegram_dispatcher import (
    DEBT_FORM_HANDOFF,
    OpenScreenAction,
    dispatch,
)
from app.queries.tools.financing import assess_financing


_TODAY = date(2026, 6, 1)


# --------------------------------------------------------------------------- #
# assess_financing query tool                                                 #
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
        "app.queries.tools.financing.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )


async def _seed_finances(session, user_id):
    # income 800,000; a 250,000 'needs' envelope with the bill (150k) + debt
    # (100k) ATTACHED (per-item coverage, B3) → reliable, surplus 550,000, safe
    # = 80% = 440,000.
    env = Envelope(
        user_id=user_id, name="Gastos", envelope_class="needs",
        limit_amount=Decimal("250000"), currency="CRC",
    )
    session.add(env)
    await session.flush()
    session.add_all(
        [
            RecurringIncome(
                user_id=user_id,
                name="Salario",
                income_type="salary",
                amount=Decimal("800000"),
                currency="CRC",
                frequency="monthly",
                next_payment_date=_TODAY,
            ),
            RecurringBill(
                user_id=user_id,
                name="Internet",
                category="servicios",
                amount_expected=Decimal("150000"),
                currency="CRC",
                frequency="monthly",
                start_date=_TODAY,
                envelope_id=env.id,
            ),
            Debt(
                user_id=user_id,
                name="Préstamo",
                debt_type="loan",
                original_amount=Decimal("3000000"),
                current_balance=Decimal("2000000"),
                interest_rate=Decimal("0.18"),
                minimum_payment=Decimal("100000"),
                payment_due_day=1,
                envelope_id=env.id,
            ),
        ]
    )
    await session.commit()


@pytest.mark.asyncio
async def test_small_loan_fits_disposable(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_finances(session, user_id)

    # 2,000,000 at 12% over 60 months → cuota ≈ 44,500 ≤ safe 440,000.
    r = await assess_financing(
        price=2_000_000, annual_rate_pct=12, term_months=60, user_id=user_id
    )
    assert r["currency"] == "CRC"
    assert float(r["monthly_payment"]) > 0
    assert r["safe_surplus"] == "440000.00"
    assert r["cuota_fits_surplus"] is True
    assert r["gate_reason"] is None
    assert r["cuota_shortfall"] == "0.00"
    assert float(r["total_interest"]) > 0


@pytest.mark.asyncio
async def test_large_high_rate_loan_does_not_fit(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_finances(session, user_id)

    # 50,000,000 at 45% over 240 months → cuota ≈ 1,875,000 ≫ safe 440,000,
    # and total interest is several times the price.
    r = await assess_financing(
        price=50_000_000, annual_rate_pct=45, term_months=240, user_id=user_id
    )
    assert r["cuota_fits_surplus"] is False
    assert float(r["cuota_shortfall"]) > 0
    assert float(r["monthly_payment"]) > 440000
    # At 45% over 20 years you pay far more in interest than the price itself.
    assert r["interest_multiple_of_price"] > 1


@pytest.mark.asyncio
async def test_down_payment_reduces_financed_principal(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_finances(session, user_id)

    r = await assess_financing(
        price=10_000_000,
        annual_rate_pct=12,
        term_months=60,
        down_payment=3_000_000,
        user_id=user_id,
    )
    assert r["financed_principal"] == "7000000.00"
    assert r["down_payment"] == "3000000.00"


@pytest.mark.asyncio
async def test_no_income_is_unknown_not_fabricated(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    # No recurring income seeded → engine can't say if the cuota fits.

    r = await assess_financing(
        price=2_000_000, annual_rate_pct=12, term_months=60, user_id=user_id
    )
    assert r["cuota_fits_surplus"] is None
    assert r["safe_surplus"] is None
    assert r["gate_reason"] == "no_income"
    assert float(r["monthly_payment"]) > 0  # still simulates the loan
    assert any("ingresos recurrentes" in n for n in r["notes"])


# --------------------------------------------------------------------------- #
# advise-first debt handoff                                                   #
# --------------------------------------------------------------------------- #


def _debt_extraction(**overrides) -> ExtractionResult:
    base = dict(
        intent=Intent.CREATE_DEBT,
        dispatcher="write",
        debt_principal=Decimal("50000000"),
        debt_interest_rate=Decimal("45"),
        debt_term_months=240,
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


@pytest.mark.asyncio
async def test_debt_handoff_leads_with_cost_when_priceable(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed_income(session, user_id, amount="800000")
    # A budget so the cashflow is reliable: committed 200k → surplus 600k, safe 480k.
    session.add(
        Envelope(
            user_id=user_id, name="Gastos", envelope_class="needs",
            limit_amount=Decimal("200000"), currency="CRC",
        )
    )
    await session.commit()

    decision = await dispatch(
        extraction=_debt_extraction(), user=user, today=_TODAY, db=session
    )
    assert isinstance(decision, OpenScreenAction)
    assert decision.screen == "debt_create"  # still opens the form
    # Advice leads; the handoff copy follows.
    assert "cuota rondaría" in decision.message_es
    assert "intereses" in decision.message_es
    assert "Supera tu sobrante" in decision.message_es  # 1.875M ≫ 480k
    assert DEBT_FORM_HANDOFF in decision.message_es


@pytest.mark.asyncio
async def test_debt_handoff_plain_when_no_rate(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed_income(session, user_id)

    # No rate → can't price it → plain handoff, no advice prefix.
    decision = await dispatch(
        extraction=_debt_extraction(debt_interest_rate=None),
        user=user,
        today=_TODAY,
        db=session,
    )
    assert isinstance(decision, OpenScreenAction)
    assert decision.message_es == DEBT_FORM_HANDOFF
    assert "cuota rondaría" not in decision.message_es

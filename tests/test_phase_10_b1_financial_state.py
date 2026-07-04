"""P10 B1 — `financial_state` single owner + per-currency net worth.

Locks: the FROZEN enum, the worst-first precedence of the pure classifier,
that nothing recomputes surplus (fixture cashflows come from the pure
`build_monthly_cashflow`), and that `compute_net_worth` never double-counts a
card (the credit account's negative balance IS the liability; a superseded
`credit_card` Debt is excluded) and never FX-sums currencies (D3).

Prereqs (DB tests): `docker compose up -d db` + `alembic upgrade head`.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from api.services.finance.cashflow import build_monthly_cashflow
from api.services.finance.financial_state import (
    ALL_FINANCIAL_STATES,
    FinancialState,
    classify_financial_state,
    classify_for_user,
)
from api.services.finance.net_worth import compute_net_worth


def _cashflow(
    *,
    income="800000",
    allocations="500000",
    has_budget=True,
    savings_allocations="0",
):
    return build_monthly_cashflow(
        monthly_income=Decimal(income) if income is not None else None,
        debt_payments=Decimal("0"),
        recurring_bills=Decimal("0"),
        envelope_allocations=Decimal(allocations),
        has_budget=has_budget,
        savings_allocations=Decimal(savings_allocations),
    )


# ── the frozen enum ───────────────────────────────────────────────────────────


def test_enum_is_the_frozen_p10_set():
    assert ALL_FINANCIAL_STATES == {
        "negative_surplus",
        "high_interest_debt",
        "irregular_income_stress",
        "recent_overspend",
        "first_time_saving",
        "stable_building",
    }


# ── pure classifier, one test per label ───────────────────────────────────────


def test_negative_surplus_dominates_everything():
    cf = _cashflow(income="400000", allocations="500000")
    state = classify_financial_state(
        cf,
        max_debt_interest_rate=Decimal("0.45"),
        card_revolving=True,
        over_limit_envelopes=3,
    )
    assert state is FinancialState.NEGATIVE_SURPLUS


def test_irregular_income_stress_when_no_income():
    cf = _cashflow(income=None)
    state = classify_financial_state(cf, card_revolving=True)
    assert state is FinancialState.IRREGULAR_INCOME_STRESS


def test_high_interest_debt_by_rate():
    cf = _cashflow()
    assert (
        classify_financial_state(cf, max_debt_interest_rate=Decimal("0.20"))
        is FinancialState.HIGH_INTEREST_DEBT
    )
    # Below the threshold the rate alone does not flag.
    assert (
        classify_financial_state(cf, max_debt_interest_rate=Decimal("0.12"))
        is not FinancialState.HIGH_INTEREST_DEBT
    )


def test_high_interest_debt_by_card_balance():
    cf = _cashflow()
    assert (
        classify_financial_state(cf, card_revolving=True)
        is FinancialState.HIGH_INTEREST_DEBT
    )


def test_high_interest_debt_by_dti():
    cf = _cashflow()
    assert (
        classify_financial_state(cf, dti=Decimal("0.40"))
        is FinancialState.HIGH_INTEREST_DEBT
    )


def test_recent_overspend():
    cf = _cashflow()
    assert (
        classify_financial_state(cf, over_limit_envelopes=1)
        is FinancialState.RECENT_OVERSPEND
    )


def test_first_time_saving_when_nothing_set_aside_and_nothing_built():
    cf = _cashflow(savings_allocations="0")
    assert classify_financial_state(cf) is FinancialState.FIRST_TIME_SAVING
    assert (
        classify_financial_state(cf, net_worth=Decimal("0"))
        is FinancialState.FIRST_TIME_SAVING
    )


def test_stable_building_with_savings_allocations():
    cf = _cashflow(savings_allocations="100000")
    assert classify_financial_state(cf) is FinancialState.STABLE_BUILDING


def test_stable_building_with_positive_net_worth():
    cf = _cashflow(savings_allocations="0")
    assert (
        classify_financial_state(cf, net_worth=Decimal("1500000"))
        is FinancialState.STABLE_BUILDING
    )


def test_no_budget_user_with_income_lands_first_time_saving():
    # Gated no_budget (income known, zero envelopes): surplus isn't a deficit
    # signal, there's no expensive debt — the user simply hasn't built yet.
    cf = _cashflow(allocations="0", has_budget=False)
    assert classify_financial_state(cf) is FinancialState.FIRST_TIME_SAVING


# ── composed reading (DB) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_for_user_fresh_user_is_income_stress(db_with_user):
    session, user_id = db_with_user
    from api.models.user import User

    user = await session.get(User, user_id)
    reading = await classify_for_user(session, user=user)
    assert reading.state is FinancialState.IRREGULAR_INCOME_STRESS
    assert reading.signals["income_known"] is False
    assert reading.signals["gate_reason"] == "no_income"


@pytest.mark.asyncio
async def test_net_worth_no_card_double_count_and_no_fx_sum(db_with_user):
    """A credit account owing ₡200k + a superseded credit_card Debt of the
    same ₡200k must count the liability ONCE; a USD account stays in its own
    entry (never ₡+$-summed)."""
    from datetime import date

    from api.models.account import Account
    from api.models.credit_card_terms import CreditCardTerms
    from api.models.debt import Debt
    from api.models.transaction import Transaction
    from api.models.user import User

    session, user_id = db_with_user
    user = await session.get(User, user_id)

    checking = Account(
        user_id=user_id, name="BAC Corriente", account_type="checking",
        currency="CRC", initial_balance=Decimal("500000"),
    )
    credit = Account(
        user_id=user_id, name="Promerica ₡", account_type="credit",
        currency="CRC", initial_balance=Decimal("0"),
    )
    usd = Account(
        user_id=user_id, name="BAC Dólares", account_type="checking",
        currency="USD", initial_balance=Decimal("1000"),
    )
    session.add_all([checking, credit, usd])
    await session.flush()

    session.add(
        CreditCardTerms(
            user_id=user_id,
            account_id=credit.id,
            annual_interest_rate=Decimal("0.42"),
            minimum_payment_pct=Decimal("0.03"),
            minimum_payment_floor=Decimal("10000"),
            payment_due_day=28,
        )
    )
    # The card owes ₡200 000 (one confirmed charge).
    session.add(
        Transaction(
            user_id=user_id, account_id=credit.id, amount=Decimal("-200000"),
            currency="CRC", merchant="super", transaction_date=date.today(),
            source="manual", status="confirmed",
        )
    )
    # Legacy credit_card Debt for the SAME card → superseded, must not add.
    session.add(
        Debt(
            user_id=user_id, name="Tarjeta Promerica", debt_type="credit_card",
            account_id=credit.id, original_amount=Decimal("200000"),
            current_balance=Decimal("200000"), interest_rate=Decimal("0.42"),
            minimum_payment=Decimal("10000"), payment_due_day=28,
        )
    )
    # A real personal loan that DOES count.
    session.add(
        Debt(
            user_id=user_id, name="Préstamo carro", debt_type="auto_loan",
            original_amount=Decimal("3000000"), current_balance=Decimal("1000000"),
            interest_rate=Decimal("0.12"), minimum_payment=Decimal("90000"),
            payment_due_day=15,
        )
    )
    await session.commit()

    reading = await compute_net_worth(session, user=user)
    crc = reading.entry_for("CRC")
    usd_entry = reading.entry_for("USD")
    assert crc is not None and usd_entry is not None
    assert crc.assets == Decimal("500000.00")
    # 200k card owed (once!) + 1M loan — the superseded Debt adds nothing.
    assert crc.liabilities == Decimal("1200000.00")
    assert crc.net == Decimal("-700000.00")
    assert usd_entry.assets == Decimal("1000.00")
    assert usd_entry.net == Decimal("1000.00")
    # display currency leads
    assert reading.entries[0].currency == reading.currency


# ── adversarial-review fixes (2026-07-03) ─────────────────────────────────────


def _add_income_and_envelope(session, user_id):
    from datetime import date

    from api.models.envelope import Envelope
    from api.models.recurring_income import RecurringIncome

    session.add(
        RecurringIncome(
            user_id=user_id, name="Salario", income_type="salary",
            amount=Decimal("800000"), currency="CRC", frequency="monthly",
            next_payment_date=date(2026, 7, 15),
        )
    )
    session.add(
        Envelope(
            user_id=user_id, name="Vida", envelope_class="needs",
            limit_amount=Decimal("500000"), currency="CRC",
        )
    )


@pytest.mark.asyncio
async def test_superseded_card_debt_rate_does_not_flag(db_with_user):
    """A superseded credit_card Debt at 42% APR whose card is PAID OFF must
    not label the user high_interest_debt — the card's live terms carry that
    obligation (same supersession rule as net worth / affordability)."""
    from api.models.account import Account
    from api.models.credit_card_terms import CreditCardTerms
    from api.models.debt import Debt
    from api.models.user import User

    session, user_id = db_with_user
    _add_income_and_envelope(session, user_id)
    credit = Account(
        user_id=user_id, name="Promerica ₡", account_type="credit",
        currency="CRC", initial_balance=Decimal("0"),
    )
    session.add(credit)
    await session.flush()
    session.add(
        CreditCardTerms(
            user_id=user_id, account_id=credit.id,
            annual_interest_rate=Decimal("0.42"),
            minimum_payment_pct=Decimal("0.03"),
            minimum_payment_floor=Decimal("10000"), payment_due_day=28,
        )
    )
    session.add(
        Debt(
            user_id=user_id, name="Tarjeta Promerica", debt_type="credit_card",
            account_id=credit.id, original_amount=Decimal("200000"),
            current_balance=Decimal("200000"), interest_rate=Decimal("0.42"),
            minimum_payment=Decimal("10000"), payment_due_day=28,
        )
    )
    # A paid-off personal loan (balance 0) at a high rate must not flag either.
    session.add(
        Debt(
            user_id=user_id, name="Préstamo viejo", debt_type="personal_loan",
            original_amount=Decimal("1000000"), current_balance=Decimal("0"),
            interest_rate=Decimal("0.30"), minimum_payment=Decimal("50000"),
            payment_due_day=10,
        )
    )
    await session.commit()

    user = await session.get(User, user_id)
    reading = await classify_for_user(session, user=user)
    assert reading.state is not FinancialState.HIGH_INTEREST_DEBT
    assert reading.signals["max_debt_interest_rate"] is None


@pytest.mark.asyncio
async def test_intra_cycle_card_usage_is_not_high_interest_debt(db_with_user):
    """Normal card usage (charged, will pay at corte, zero interest) must not
    read as expensive debt — only an unpaid statement past due flags."""
    from datetime import date

    from api.models.account import Account
    from api.models.credit_card_terms import CreditCardTerms
    from api.models.transaction import Transaction
    from api.models.user import User

    session, user_id = db_with_user
    _add_income_and_envelope(session, user_id)
    credit = Account(
        user_id=user_id, name="BAC ₡", account_type="credit",
        currency="CRC", initial_balance=Decimal("0"),
    )
    session.add(credit)
    await session.flush()
    # No statement_day: the composer can't tell intra-cycle from revolving →
    # contributes nothing to the signal (documented).
    session.add(
        CreditCardTerms(
            user_id=user_id, account_id=credit.id,
            annual_interest_rate=Decimal("0.45"),
            minimum_payment_pct=Decimal("0.03"),
            minimum_payment_floor=Decimal("10000"), payment_due_day=28,
        )
    )
    session.add(
        Transaction(
            user_id=user_id, account_id=credit.id, amount=Decimal("-150000"),
            currency="CRC", merchant="super", transaction_date=date.today(),
            source="manual", status="confirmed",
        )
    )
    await session.commit()

    user = await session.get(User, user_id)
    reading = await classify_for_user(session, user=user)
    assert reading.state is not FinancialState.HIGH_INTEREST_DEBT
    assert reading.signals["card_revolving"] is False


@pytest.mark.asyncio
async def test_wealth_in_non_display_currency_counts_as_built(db_with_user):
    """A CRC-display user whose wealth sits in USD must not read as
    first_time_saving — the SIGN of any per-currency net counts (no FX)."""
    from api.models.account import Account
    from api.models.user import User

    session, user_id = db_with_user
    _add_income_and_envelope(session, user_id)
    session.add(
        Account(
            user_id=user_id, name="BAC Dólares", account_type="savings",
            currency="USD", initial_balance=Decimal("5000"),
        )
    )
    await session.commit()

    user = await session.get(User, user_id)
    reading = await classify_for_user(session, user=user)
    assert reading.state is FinancialState.STABLE_BUILDING
    assert reading.signals["net_worth_by_currency"]["USD"] == "5000.00"

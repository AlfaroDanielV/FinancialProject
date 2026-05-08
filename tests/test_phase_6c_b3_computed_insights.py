from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from api.models.account import Account
from api.models.bill_occurrence import BillOccurrence
from api.models.debt import Debt
from api.models.recurring_bill import RecurringBill
from api.models.transaction import Transaction
from api.models.user_insight import UserInsight, UserInsightAudit
from api.schemas.insights import SpendingPatternContent
from api.services.insights.computed import (
    compute_all,
    compute_cash_flow_stability,
    compute_cr_seasonal_patterns,
    compute_debt_load,
    compute_emergency_fund,
    compute_recurring_drift,
    compute_spending_patterns,
    computed_confidence,
)
from api.services.insights.persister import persist_insights


async def _seed_txn(
    session,
    user_id: uuid.UUID,
    *,
    amount: Decimal,
    transaction_date: date,
    category: str,
    merchant: str = "Merchant",
    description: str | None = None,
    account_id: uuid.UUID | None = None,
    currency: str = "CRC",
    status: str = "confirmed",
    is_duplicate: bool = False,
):
    session.add(
        Transaction(
            user_id=user_id,
            account_id=account_id,
            amount=amount,
            currency=currency,
            merchant=merchant,
            description=description,
            category=category,
            transaction_date=transaction_date,
            source="manual",
            status=status,
            is_duplicate=is_duplicate,
        )
    )


async def test_compute_spending_patterns_groups_confirmed_expenses(db_with_user):
    session, user_id = db_with_user
    as_of = date(2026, 5, 7)

    for amount, tx_date in [
        (Decimal("-100.00"), date(2026, 3, 5)),
        (Decimal("-50.00"), date(2026, 3, 8)),
        (Decimal("-200.00"), date(2026, 4, 5)),
        (Decimal("-300.00"), date(2026, 5, 5)),
    ]:
        await _seed_txn(
            session,
            user_id,
            amount=amount,
            transaction_date=tx_date,
            category="Supermercado",
        )
    await _seed_txn(
        session,
        user_id,
        amount=Decimal("-999.00"),
        transaction_date=date(2026, 5, 6),
        category="Supermercado",
        is_duplicate=True,
    )
    await _seed_txn(
        session,
        user_id,
        amount=Decimal("-999.00"),
        transaction_date=date(2026, 5, 6),
        category="Supermercado",
        status="shadow",
    )
    await session.commit()

    insights = await compute_spending_patterns(session, user_id, as_of=as_of)

    assert len(insights) == 1
    insight = insights[0]
    assert insight.category == "supermercado"
    assert insight.monthly_avg_crc == Decimal("216.67")
    assert insight.monthly_avg_usd is None
    assert insight.trend_pct_3mo == Decimal("100.00")
    assert Decimal("0.50") <= computed_confidence(insight) <= Decimal("1.00")


async def test_compute_recurring_drift_uses_bill_expected_baseline(db_with_user):
    session, user_id = db_with_user
    bill = RecurringBill(
        user_id=user_id,
        name="Internet",
        category="internet",
        amount_expected=Decimal("10000.00"),
        currency="CRC",
        frequency="monthly",
        start_date=date(2026, 1, 1),
        is_active=True,
    )
    session.add(bill)
    await session.flush()
    session.add_all(
        [
            BillOccurrence(
                user_id=user_id,
                recurring_bill_id=bill.id,
                due_date=date(2026, 3, 1),
                amount_expected=Decimal("10000.00"),
                amount_paid=Decimal("10000.00"),
                status="paid",
            ),
            BillOccurrence(
                user_id=user_id,
                recurring_bill_id=bill.id,
                due_date=date(2026, 4, 1),
                amount_expected=Decimal("10000.00"),
                amount_paid=Decimal("13000.00"),
                status="paid",
            ),
        ]
    )
    await session.commit()

    insights = await compute_recurring_drift(
        session, user_id, as_of=date(2026, 5, 7)
    )

    assert len(insights) == 1
    assert insights[0].bill_id == bill.id
    assert insights[0].baseline_amount == Decimal("10000.00")
    assert insights[0].current_amount == Decimal("13000.00")
    assert insights[0].drift_pct == Decimal("30.00")


async def test_cash_flow_debt_load_and_emergency_fund(db_with_user):
    session, user_id = db_with_user
    account = Account(
        user_id=user_id,
        name="BAC Checking",
        account_type="checking",
        is_active=True,
    )
    session.add(account)
    await session.flush()

    rows = [
        (Decimal("1000000.00"), date(2026, 3, 1), "income", "Employer A"),
        (Decimal("-700000.00"), date(2026, 3, 8), "rent", "Rent"),
        (Decimal("1000000.00"), date(2026, 4, 1), "income", "Employer A"),
        (Decimal("-800000.00"), date(2026, 4, 8), "supermercado", "Automercado"),
        (Decimal("1000000.00"), date(2026, 5, 1), "income", "Employer B"),
        (Decimal("-600000.00"), date(2026, 5, 5), "servicios", "Servicios"),
    ]
    for amount, tx_date, category, merchant in rows:
        await _seed_txn(
            session,
            user_id,
            amount=amount,
            transaction_date=tx_date,
            category=category,
            merchant=merchant,
            account_id=account.id,
        )

    session.add_all(
        [
            Debt(
                user_id=user_id,
                name="Carro",
                debt_type="auto_loan",
                original_amount=Decimal("10000000.00"),
                current_balance=Decimal("8000000.00"),
                interest_rate=Decimal("0.0800"),
                minimum_payment=Decimal("200000.00"),
                payment_due_day=15,
                is_active=True,
            ),
            Debt(
                user_id=user_id,
                name="Tarjeta",
                debt_type="credit_card",
                original_amount=Decimal("1000000.00"),
                current_balance=Decimal("500000.00"),
                interest_rate=Decimal("0.3000"),
                minimum_payment=Decimal("100000.00"),
                payment_due_day=20,
                is_active=True,
            ),
        ]
    )
    await session.commit()

    cash_flow = await compute_cash_flow_stability(
        session, user_id, lookback_months=3, as_of=date(2026, 5, 7)
    )
    debt_load = await compute_debt_load(
        session, user_id, income_lookback_months=3, as_of=date(2026, 5, 7)
    )
    emergency = await compute_emergency_fund(
        session, user_id, expense_lookback_months=3, as_of=date(2026, 5, 7)
    )

    assert cash_flow[0].income_sources_count == 2
    assert cash_flow[0].savings_rate_pct == Decimal("30.00")
    assert cash_flow[0].last_income_at == date(2026, 5, 1)
    assert 70 <= cash_flow[0].score_0_100 <= 75

    assert debt_load[0].total_monthly_debt_service_crc == Decimal("300000.00")
    assert debt_load[0].debt_to_income_ratio == Decimal("0.30")
    assert debt_load[0].num_active_debts == 2

    assert emergency[0].liquid_balance_crc == Decimal("900000.00")
    assert emergency[0].monthly_essential_expenses_crc == Decimal("700000.00")
    assert emergency[0].months_of_expenses_covered == Decimal("1.29")
    assert emergency[0].sufficiency == "borderline"


async def test_compute_cr_seasonal_patterns(db_with_user):
    session, user_id = db_with_user
    for amount, tx_date, description in [
        (Decimal("500000.00"), date(2025, 12, 15), "Pago aguinaldo"),
        (Decimal("300000.00"), date(2026, 1, 15), "Salario escolar"),
        (Decimal("-250000.00"), date(2025, 12, 1), "Pago marchamo"),
    ]:
        await _seed_txn(
            session,
            user_id,
            amount=amount,
            transaction_date=tx_date,
            category="seasonal",
            description=description,
        )
    await session.commit()

    insights = await compute_cr_seasonal_patterns(
        session, user_id, as_of=date(2026, 5, 7)
    )

    by_event = {insight.event: insight for insight in insights}
    assert set(by_event) == {"aguinaldo", "salario_escolar", "marchamo"}
    assert by_event["aguinaldo"].expected_month == 12
    assert by_event["aguinaldo"].historical_amount_crc == Decimal("500000.00")
    assert by_event["salario_escolar"].expected_month == 1
    assert by_event["marchamo"].historical_amount_crc == Decimal("250000.00")


async def test_compute_all_runs_b3_computed_registry(db_with_user):
    session, user_id = db_with_user
    account = Account(
        user_id=user_id,
        name="BAC Checking",
        account_type="checking",
        is_active=True,
    )
    session.add(account)
    await session.flush()
    await _seed_txn(
        session,
        user_id,
        amount=Decimal("1000000.00"),
        transaction_date=date(2026, 5, 1),
        category="income",
        account_id=account.id,
    )
    await _seed_txn(
        session,
        user_id,
        amount=Decimal("-500000.00"),
        transaction_date=date(2026, 5, 2),
        category="rent",
        account_id=account.id,
    )
    await session.commit()

    insights = await compute_all(session, user_id, as_of=date(2026, 5, 7))

    insight_types = {insight.type for insight in insights}
    assert {"cash_flow_stability", "debt_load", "emergency_fund"}.issubset(
        insight_types
    )


async def test_persister_creates_and_reinforces_computed_insight(db_with_user):
    session, user_id = db_with_user
    await _seed_txn(
        session,
        user_id,
        amount=Decimal("-100.00"),
        transaction_date=date(2026, 5, 1),
        category="Supermercado",
    )
    await _seed_txn(
        session,
        user_id,
        amount=Decimal("-200.00"),
        transaction_date=date(2026, 5, 2),
        category="Supermercado",
    )
    await _seed_txn(
        session,
        user_id,
        amount=Decimal("-300.00"),
        transaction_date=date(2026, 5, 3),
        category="Supermercado",
    )
    await session.commit()

    insights = await compute_spending_patterns(
        session, user_id, as_of=date(2026, 5, 7)
    )
    now = datetime(2026, 5, 7, tzinfo=timezone.utc)
    stats = await persist_insights(
        session,
        user_id=user_id,
        insights=insights,
        source="computed",
        now=now,
    )
    assert stats.created == 1

    stats = await persist_insights(
        session,
        user_id=user_id,
        insights=insights,
        source="computed",
        now=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )
    assert stats.reinforced == 1
    await session.commit()

    row = (
        await session.execute(
            select(UserInsight).where(UserInsight.user_id == user_id)
        )
    ).scalar_one()
    assert row.insight_type == "spending_pattern"
    assert row.dedup_key == "supermercado"
    assert row.reinforcement_count == 2
    assert row.content["type"] == "spending_pattern"

    audits = (
        (
            await session.execute(
                select(UserInsightAudit)
                .where(UserInsightAudit.user_id == user_id)
                .order_by(UserInsightAudit.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    assert [audit.action for audit in audits] == ["created", "reinforced"]


async def test_persister_skips_user_locked_rows(db_with_user):
    session, user_id = db_with_user
    locked = SpendingPatternContent(
        category="supermercado",
        monthly_avg_crc=Decimal("10.00"),
        trend_pct_3mo=Decimal("0.00"),
        volatility_score=Decimal("0.00"),
    )
    session.add(
        UserInsight(
            user_id=user_id,
            insight_type=locked.type,
            content=locked.model_dump(mode="json"),
            confidence=Decimal("1.00"),
            source="user_override",
            valid_until=datetime(2027, 1, 1, tzinfo=timezone.utc),
            user_locked=True,
            dedup_key=locked.dedup_key(),
        )
    )
    await session.commit()

    replacement = SpendingPatternContent(
        category="supermercado",
        monthly_avg_crc=Decimal("999.00"),
        trend_pct_3mo=Decimal("100.00"),
        volatility_score=Decimal("0.50"),
    )
    stats = await persist_insights(
        session,
        user_id=user_id,
        insights=[replacement],
        source="computed",
        now=datetime(2026, 5, 7, tzinfo=timezone.utc),
    )
    await session.commit()

    assert stats.skipped_locked == 1
    row = (
        await session.execute(
            select(UserInsight).where(UserInsight.user_id == user_id)
        )
    ).scalar_one()
    assert row.content["monthly_avg_crc"] == "10.00"

"""Deterministic computed insights for Phase 6c.

These functions are the non-LLM writer for `user_insights`. They read
confirmed ledger data, return validated `InsightContent` objects, and do
not write to the database. The persister is the only B3 component that
mutates `user_insights`.

Confidence formula:
    base 0.50 + 0.50 * (0.70 * sample_score + 0.30 * recency_score)

`sample_score` caps at 1.0 once the insight has enough observations for
that type. `recency_score` is 1.0 for <=30d, 0.85 for <=60d, 0.70 for
<=90d, and 0.50 after that. The result is quantized to NUMERIC(3,2).
Computed insights therefore stay inside the locked [0.50, 1.00] range.
"""
from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from statistics import median
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.account import Account
from api.models.bill_occurrence import BillOccurrence
from api.models.debt import Debt
from api.models.recurring_bill import RecurringBill
from api.models.transaction import Transaction
from api.services.clock import user_today
from api.schemas.insights import (
    CRSeasonalPatternContent,
    CashFlowStabilityContent,
    DebtLoadContent,
    EmergencyFundContent,
    InsightContent,
    MoneyPersonalityContent,
    RecurringDriftContent,
    SpendingPatternContent,
)

_DECIMAL_CENTS = Decimal("0.01")
_DECIMAL_PCT = Decimal("0.01")
_ESSENTIAL_CATEGORY_HINTS = (
    "super",
    "grocer",
    "food",
    "comida",
    "mercado",
    "rent",
    "alquiler",
    "mortgage",
    "hipoteca",
    "utility",
    "electric",
    "water",
    "internet",
    "mobile",
    "phone",
    "servicio",
    "agua",
    "luz",
    "salud",
    "medical",
    "medicine",
    "transporte",
    "gas",
    "fuel",
)


@dataclass(frozen=True)
class _TxnRow:
    amount: Decimal
    currency: str
    category: str | None
    merchant: str | None
    description: str | None
    transaction_date: date


def computed_confidence(content: InsightContent) -> Decimal:
    """Return the confidence attached by a compute function.

    This value is intentionally private to the Pydantic object so it never
    lands in `user_insights.content`.
    """
    confidence = getattr(content, "_computed_confidence", None)
    return confidence if isinstance(confidence, Decimal) else Decimal("0.50")


async def compute_spending_patterns(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    lookback_months: int = 3,
    as_of: date | None = None,
) -> list[SpendingPatternContent]:
    """Group confirmed expenses by category and compute avg/trend/volatility."""
    effective_as_of = as_of or date.today()
    months = _month_starts(effective_as_of, lookback_months)
    start = months[0]
    rows = await _fetch_transactions(
        session,
        user_id=user_id,
        start=start,
        end=effective_as_of,
        amount_side="expense",
    )

    buckets: dict[tuple[str, str], dict[date, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: Decimal("0"))
    )
    counts: dict[tuple[str, str], int] = defaultdict(int)
    latest_by_key: dict[tuple[str, str], date] = {}

    for row in rows:
        category = _canonical_category(row.category)
        currency = row.currency.upper()
        key = (category, currency)
        buckets[key][_month_start(row.transaction_date)] += row.amount.copy_abs()
        counts[key] += 1
        latest_by_key[key] = max(
            row.transaction_date, latest_by_key.get(key, row.transaction_date)
        )

    by_category: dict[str, dict[str, list[Decimal] | int | date]] = {}
    for (category, currency), monthly_totals in buckets.items():
        series = [monthly_totals.get(month, Decimal("0")) for month in months]
        if counts[(category, currency)] < 3 or sum(series, Decimal("0")) <= 0:
            continue
        target = by_category.setdefault(
            category,
            {"CRC": [], "USD": [], "count": 0, "latest": start},
        )
        target[currency] = series
        target["count"] = int(target["count"]) + counts[(category, currency)]
        target["latest"] = max(target["latest"], latest_by_key[(category, currency)])

    insights: list[SpendingPatternContent] = []
    for category, data in by_category.items():
        crc_series = data.get("CRC") or []
        usd_series = data.get("USD") or []
        primary_series = crc_series or usd_series
        if not primary_series:
            continue
        content = SpendingPatternContent(
            category=category,
            monthly_avg_crc=_average(crc_series) if crc_series else None,
            monthly_avg_usd=_average(usd_series) if usd_series else None,
            trend_pct_3mo=_trend_pct(primary_series),
            volatility_score=_volatility(primary_series),
        )
        _attach_confidence(
            content,
            _confidence(
                sample_count=int(data["count"]),
                full_sample=12,
                latest_date=data["latest"],
                as_of=effective_as_of,
            ),
        )
        insights.append(content)

    return sorted(insights, key=lambda item: item.category)


async def compute_recurring_drift(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    as_of: date | None = None,
    min_abs_drift_pct: Decimal = Decimal("5.00"),
) -> list[RecurringDriftContent]:
    """Compare the last paid occurrence against the bill baseline."""
    effective_as_of = as_of or date.today()
    bills = list(
        (
            await session.execute(
                select(RecurringBill)
                .where(
                    RecurringBill.user_id == user_id,
                    RecurringBill.is_active.is_(True),
                )
                .order_by(RecurringBill.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    insights: list[RecurringDriftContent] = []
    for bill in bills:
        occurrences = list(
            (
                await session.execute(
                    select(BillOccurrence)
                    .where(
                        BillOccurrence.user_id == user_id,
                        BillOccurrence.recurring_bill_id == bill.id,
                        BillOccurrence.amount_paid.is_not(None),
                        BillOccurrence.status.in_(["paid", "partially_paid"]),
                    )
                    .order_by(BillOccurrence.due_date.asc())
                )
            )
            .scalars()
            .all()
        )
        if not occurrences:
            continue

        latest = occurrences[-1]
        current = _as_decimal(latest.amount_paid)
        previous_amounts = [_as_decimal(o.amount_paid) for o in occurrences[:-1]]
        baseline = (
            _as_decimal(bill.amount_expected)
            if bill.amount_expected is not None
            else _median_decimal(previous_amounts)
        )
        if baseline is None or baseline <= 0:
            continue

        drift_pct = _pct((current - baseline) / baseline)
        if drift_pct.copy_abs() < min_abs_drift_pct:
            continue

        detected_at = datetime.combine(
            effective_as_of, time.min, tzinfo=timezone.utc
        )
        content = RecurringDriftContent(
            bill_id=bill.id,
            baseline_amount=_money(baseline),
            current_amount=_money(current),
            drift_pct=drift_pct,
            detected_at=detected_at,
        )
        _attach_confidence(
            content,
            _confidence(
                sample_count=len(occurrences),
                full_sample=6,
                latest_date=latest.due_date,
                as_of=effective_as_of,
            ),
        )
        insights.append(content)
    return insights


async def compute_cash_flow_stability(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    lookback_months: int = 6,
    as_of: date | None = None,
) -> list[CashFlowStabilityContent]:
    """Compute variance of monthly net cash flow from confirmed transactions."""
    effective_as_of = as_of or date.today()
    months = _month_starts(effective_as_of, lookback_months)
    rows = await _fetch_transactions(
        session,
        user_id=user_id,
        start=months[0],
        end=effective_as_of,
        currency="CRC",
    )
    if not rows:
        return []

    net_by_month = {month: Decimal("0") for month in months}
    income_total = Decimal("0")
    spending_total = Decimal("0")
    income_sources: set[str] = set()
    last_income_at: date | None = None

    for row in rows:
        net_by_month[_month_start(row.transaction_date)] += row.amount
        if row.amount > 0:
            income_total += row.amount
            income_sources.add(_norm(row.merchant or row.description or "unknown"))
            last_income_at = max(
                last_income_at or row.transaction_date,
                row.transaction_date,
            )
        elif row.amount < 0:
            spending_total += row.amount.copy_abs()

    net_series = list(net_by_month.values())
    variance_pct = _monthly_variance_pct(net_series)
    score = max(0, min(100, int(round(100 - min(100, float(variance_pct))))))
    savings_rate_pct = (
        _pct((income_total - spending_total) / income_total)
        if income_total > 0
        else Decimal("0.00")
    )
    content = CashFlowStabilityContent(
        score_0_100=score,
        monthly_variance_pct=_pct_value(variance_pct),
        income_sources_count=len(income_sources),
        savings_rate_pct=savings_rate_pct,
        last_income_at=last_income_at,
    )
    latest_date = max(row.transaction_date for row in rows)
    _attach_confidence(
        content,
        _confidence(
            sample_count=len(rows),
            full_sample=24,
            latest_date=latest_date,
            as_of=effective_as_of,
        ),
    )
    return [content]


async def compute_debt_load(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    as_of: date | None = None,
    income_lookback_months: int = 3,
) -> list[DebtLoadContent]:
    """Compute DTI from active debts and recent confirmed income transactions."""
    effective_as_of = as_of or date.today()
    active_debts = list(
        (
            await session.execute(
                select(Debt).where(Debt.user_id == user_id, Debt.is_active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    months = _month_starts(effective_as_of, income_lookback_months)
    income_rows = await _fetch_transactions(
        session,
        user_id=user_id,
        start=months[0],
        end=effective_as_of,
        amount_side="income",
        currency="CRC",
    )

    total_debt_service = sum(
        (_as_decimal(debt.minimum_payment) for debt in active_debts),
        Decimal("0"),
    )
    monthly_income = (
        sum((row.amount for row in income_rows), Decimal("0"))
        / Decimal(income_lookback_months)
        if income_lookback_months > 0
        else Decimal("0")
    )
    ratio = (
        (total_debt_service / monthly_income)
        if monthly_income > 0 and total_debt_service > 0
        else Decimal("0")
    )
    content = DebtLoadContent(
        debt_to_income_ratio=_ratio(ratio),
        total_monthly_debt_service_crc=_money(total_debt_service),
        num_active_debts=len(active_debts),
    )
    latest = max(
        (row.transaction_date for row in income_rows),
        default=effective_as_of,
    )
    _attach_confidence(
        content,
        _confidence(
            sample_count=len(active_debts) + len(income_rows),
            full_sample=6,
            latest_date=latest,
            as_of=effective_as_of,
        ),
    )
    return [content]


async def compute_cr_seasonal_patterns(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    as_of: date | None = None,
    lookback_months: int = 24,
) -> list[CRSeasonalPatternContent]:
    """Detect Costa Rican seasonal financial events by keyword and month."""
    effective_as_of = as_of or date.today()
    rows = await _fetch_transactions(
        session,
        user_id=user_id,
        start=_month_starts(effective_as_of, lookback_months)[0],
        end=effective_as_of,
        currency="CRC",
    )

    matches: dict[str, list[_TxnRow]] = {
        "aguinaldo": [],
        "salario_escolar": [],
        "marchamo": [],
    }
    for row in rows:
        haystack = _norm(
            " ".join(filter(None, [row.category, row.merchant, row.description]))
        )
        month = row.transaction_date.month
        if row.amount > 0 and month == 12 and "aguinaldo" in haystack:
            matches["aguinaldo"].append(row)
        if row.amount > 0 and month == 1 and (
            "salario escolar" in haystack or "escolar" in haystack
        ):
            matches["salario_escolar"].append(row)
        if row.amount < 0 and month in (11, 12) and "marchamo" in haystack:
            matches["marchamo"].append(row)

    expected_month = {"aguinaldo": 12, "salario_escolar": 1, "marchamo": 12}
    insights: list[CRSeasonalPatternContent] = []
    for event, event_rows in matches.items():
        if not event_rows:
            continue
        amounts = [row.amount.copy_abs() for row in event_rows]
        latest = max(row.transaction_date for row in event_rows)
        content = CRSeasonalPatternContent(
            event=event,  # type: ignore[arg-type]
            expected_month=expected_month[event],
            historical_amount_crc=_money(sum(amounts, Decimal("0")) / len(amounts)),
            last_observed_year=latest.year,
        )
        _attach_confidence(
            content,
            _confidence(
                sample_count=len({row.transaction_date.year for row in event_rows}),
                full_sample=2,
                latest_date=latest,
                as_of=effective_as_of,
            ),
        )
        insights.append(content)
    return insights


async def compute_emergency_fund(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    as_of: date | None = None,
    expense_lookback_months: int = 3,
) -> list[EmergencyFundContent]:
    """Estimate liquid reserve coverage from liquid accounts and essentials."""
    effective_as_of = as_of or date.today()
    months = _month_starts(effective_as_of, expense_lookback_months)

    liquid_account_ids = list(
        (
            await session.execute(
                select(Account.id).where(
                    Account.user_id == user_id,
                    Account.is_active.is_(True),
                    Account.account_type.in_(["checking", "savings"]),
                )
            )
        )
        .scalars()
        .all()
    )

    liquid_balance = Decimal("0")
    if liquid_account_ids:
        total = (
            await session.execute(
                select(func.coalesce(func.sum(Transaction.amount), Decimal("0")))
                .where(
                    *_base_transaction_filters(
                        user_id=user_id,
                        end=effective_as_of,
                        currency="CRC",
                        account_ids=liquid_account_ids,
                    )
                )
            )
        ).scalar_one()
        liquid_balance = max(_as_decimal(total), Decimal("0"))

    expense_rows = await _fetch_transactions(
        session,
        user_id=user_id,
        start=months[0],
        end=effective_as_of,
        amount_side="expense",
        currency="CRC",
    )
    essential_rows = [row for row in expense_rows if _is_essential(row.category)]
    if not essential_rows:
        essential_rows = expense_rows

    total_essential = sum(
        (row.amount.copy_abs() for row in essential_rows),
        Decimal("0"),
    )
    monthly_essential = (
        total_essential / Decimal(expense_lookback_months)
        if expense_lookback_months > 0
        else Decimal("0")
    )
    months_covered = (
        liquid_balance / monthly_essential if monthly_essential > 0 else Decimal("0")
    )
    content = EmergencyFundContent(
        months_of_expenses_covered=_ratio(months_covered),
        liquid_balance_crc=_money(liquid_balance),
        monthly_essential_expenses_crc=_money(monthly_essential),
        sufficiency=_sufficiency(months_covered),
    )
    latest = max(
        (row.transaction_date for row in expense_rows),
        default=effective_as_of,
    )
    _attach_confidence(
        content,
        _confidence(
            sample_count=len(essential_rows) + len(liquid_account_ids),
            full_sample=9,
            latest_date=latest,
            as_of=effective_as_of,
        ),
    )
    return [content]


async def compute_money_personality(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    as_of: date | None = None,
) -> list[MoneyPersonalityContent]:
    """Classify the user into a money archetype (spender/avoider/saver/investor)
    from their OWN ledger. Deterministic — delegates to the pure classifier in
    `api/services/finance/money_personality.py` (no LLM). Returns [] when there
    isn't enough signal to classify (the classifier yields None)."""
    from api.models.user import User
    from api.services.finance.money_personality import (
        classify_money_personality,
        describe_personality_evidence,
        gather_personality_inputs,
    )

    user = await session.get(User, user_id)
    if user is None:
        return []
    today = as_of or user_today(user)
    inputs = await gather_personality_inputs(session, user, today=today)
    result = classify_money_personality(inputs)
    if result.personality is None:
        return []
    content = MoneyPersonalityContent(
        personality=result.personality,
        scores=result.scores,
        evidence_summary=describe_personality_evidence(result, inputs),
    )
    # Behavior sample drives confidence via the shared evidence formula.
    days_ago = min(inputs.days_since_last_capture, 3650)
    _attach_confidence(
        content,
        _confidence(
            sample_count=inputs.confirmed_txn_count_90d,
            full_sample=50,
            latest_date=today - timedelta(days=days_ago),
            as_of=today,
        ),
    )
    return [content]


async def compute_all(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    as_of: date | None = None,
) -> list[InsightContent]:
    """Run every B3 computed insight function for one user."""
    insights: list[InsightContent] = []
    insights.extend(await compute_spending_patterns(session, user_id, as_of=as_of))
    insights.extend(await compute_recurring_drift(session, user_id, as_of=as_of))
    insights.extend(await compute_cash_flow_stability(session, user_id, as_of=as_of))
    insights.extend(await compute_debt_load(session, user_id, as_of=as_of))
    insights.extend(await compute_emergency_fund(session, user_id, as_of=as_of))
    insights.extend(await compute_cr_seasonal_patterns(session, user_id, as_of=as_of))
    insights.extend(await compute_money_personality(session, user_id, as_of=as_of))
    return insights


async def _fetch_transactions(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    start: date,
    end: date,
    amount_side: str = "all",
    currency: str | None = None,
) -> list[_TxnRow]:
    filters = _base_transaction_filters(
        user_id=user_id,
        start=start,
        end=end,
        currency=currency,
    )
    if amount_side == "expense":
        filters.append(Transaction.amount < 0)
    elif amount_side == "income":
        filters.append(Transaction.amount > 0)

    result = await session.execute(
        select(
            Transaction.amount,
            Transaction.currency,
            Transaction.category,
            Transaction.merchant,
            Transaction.description,
            Transaction.transaction_date,
        )
        .where(*filters)
        .order_by(Transaction.transaction_date.asc(), Transaction.created_at.asc())
    )
    return [
        _TxnRow(
            amount=_as_decimal(row.amount),
            currency=row.currency,
            category=row.category,
            merchant=row.merchant,
            description=row.description,
            transaction_date=row.transaction_date,
        )
        for row in result.all()
    ]


def _base_transaction_filters(
    *,
    user_id: uuid.UUID,
    start: date | None = None,
    end: date | None = None,
    currency: str | None = None,
    account_ids: Iterable[uuid.UUID] | None = None,
) -> list[object]:
    filters: list[object] = [
        Transaction.user_id == user_id,
        Transaction.status == "confirmed",
        Transaction.is_duplicate.is_(False),
    ]
    if start is not None:
        filters.append(Transaction.transaction_date >= start)
    if end is not None:
        filters.append(Transaction.transaction_date <= end)
    if currency is not None:
        filters.append(Transaction.currency == currency)
    if account_ids is not None:
        filters.append(Transaction.account_id.in_(list(account_ids)))
    return filters


def _confidence(
    *,
    sample_count: int,
    full_sample: int,
    latest_date: date,
    as_of: date,
) -> Decimal:
    sample_score = min(
        Decimal(sample_count) / Decimal(max(full_sample, 1)),
        Decimal("1"),
    )
    age_days = max((as_of - latest_date).days, 0)
    if age_days <= 30:
        recency_score = Decimal("1.00")
    elif age_days <= 60:
        recency_score = Decimal("0.85")
    elif age_days <= 90:
        recency_score = Decimal("0.70")
    else:
        recency_score = Decimal("0.50")
    weighted = Decimal("0.70") * sample_score + Decimal("0.30") * recency_score
    return _ratio(Decimal("0.50") + Decimal("0.50") * weighted)


def _attach_confidence(content: InsightContent, confidence: Decimal) -> None:
    content._computed_confidence = max(
        Decimal("0.50"),
        min(confidence, Decimal("1.00")),
    )


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _month_starts(as_of: date, count: int) -> list[date]:
    safe_count = max(1, count)
    start = _subtract_months(_month_start(as_of), safe_count - 1)
    months = []
    current = start
    for _ in range(safe_count):
        months.append(current)
        current = _add_months(current, 1)
    return months


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _subtract_months(value: date, months: int) -> date:
    return _add_months(value, -months)


def _average(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0.00")
    return _money(sum(values, Decimal("0")) / Decimal(len(values)))


def _trend_pct(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0.00")
    first = values[0]
    last = values[-1]
    if first > 0:
        return _pct((last - first) / first)
    if last > 0:
        return Decimal("100.00")
    return Decimal("0.00")


def _volatility(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0.00")
    mean = sum(values, Decimal("0")) / Decimal(len(values))
    if mean <= 0:
        return Decimal("0.00")
    variance = sum(
        ((value - mean) ** 2 for value in values),
        Decimal("0"),
    ) / Decimal(len(values))
    stddev = Decimal(str(math.sqrt(float(variance))))
    return min(_ratio(stddev / mean), Decimal("1.00"))


def _monthly_variance_pct(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0.00")
    mean_abs = sum((value.copy_abs() for value in values), Decimal("0")) / Decimal(len(values))
    if mean_abs <= 0:
        return Decimal("0.00")
    mean = sum(values, Decimal("0")) / Decimal(len(values))
    variance = sum(
        ((value - mean) ** 2 for value in values),
        Decimal("0"),
    ) / Decimal(len(values))
    return Decimal(str(math.sqrt(float(variance)))) / mean_abs * Decimal("100")


def _pct(ratio: Decimal) -> Decimal:
    return _pct_value(ratio * Decimal("100"))


def _pct_value(value: Decimal) -> Decimal:
    return value.quantize(_DECIMAL_PCT, rounding=ROUND_HALF_UP)


def _ratio(value: Decimal) -> Decimal:
    return value.quantize(_DECIMAL_PCT, rounding=ROUND_HALF_UP)


def _money(value: Decimal) -> Decimal:
    return value.quantize(_DECIMAL_CENTS, rounding=ROUND_HALF_UP)


def _as_decimal(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _median_decimal(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return _as_decimal(median(values))


def _canonical_category(category: str | None) -> str:
    value = _norm(category or "Sin categoria")
    return value or "sin categoria"


def _norm(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _is_essential(category: str | None) -> bool:
    normalized = _norm(category or "")
    return any(hint in normalized for hint in _ESSENTIAL_CATEGORY_HINTS)


def _sufficiency(months_covered: Decimal) -> str:
    if months_covered < Decimal("1"):
        return "insufficient"
    if months_covered < Decimal("3"):
        return "borderline"
    if months_covered < Decimal("6"):
        return "adequate"
    return "abundant"

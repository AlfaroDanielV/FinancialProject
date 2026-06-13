from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.account import Account
from ...models.goal import Goal
from ...models.transaction import Transaction
from ...models.user_category import UserCategory
from ...models.user import User
from ...schemas.dashboard import (
    CashFlowPoint,
    CashFlowResponse,
    CategoryBreakdownItem,
    DailyCashFlowPoint,
    DailyCashFlowResponse,
    DashboardPeriod,
    DashboardSummary,
)


@dataclass(frozen=True)
class DateWindow:
    start: date
    end_exclusive: date


def _next_month_start(value: date) -> date:
    year = value.year + 1 if value.month == 12 else value.year
    month = 1 if value.month == 12 else value.month + 1
    return date(year, month, 1)


def _period_window(period: DashboardPeriod, today: date | None = None) -> DateWindow:
    today = today or date.today()
    month_start = date(today.year, today.month, 1)
    if period == "month_current":
        return DateWindow(month_start, _next_month_start(month_start))
    if period == "month_prev":
        prev_month = date(
            month_start.year - 1 if month_start.month == 1 else month_start.year,
            12 if month_start.month == 1 else month_start.month - 1,
            1,
        )
        return DateWindow(prev_month, month_start)
    if period == "last_6_months":
        first = month_start
        for _ in range(5):
            first = date(
                first.year - 1 if first.month == 1 else first.year,
                12 if first.month == 1 else first.month - 1,
                1,
            )
        return DateWindow(first, _next_month_start(month_start))
    return DateWindow(date(today.year, 1, 1), _next_month_start(month_start))


def _zero_summary(
    *,
    period: DashboardPeriod,
    display_currency: str,
    accounts_count: int,
    active_goals_count: int,
) -> DashboardSummary:
    return DashboardSummary(
        period=period,
        display_currency=display_currency,
        income_total=Decimal("0"),
        expense_total=Decimal("0"),
        net_flow=Decimal("0"),
        savings_rate=None,
        balance_total=Decimal("0"),
        available_balance=Decimal("0"),
        savings_balance=Decimal("0"),
        transaction_count=0,
        transfer_rows_excluded=0,
        accounts_count=accounts_count,
        active_goals_count=active_goals_count,
        category_breakdown=[],
    )


async def _active_counts(db: AsyncSession, user_id: uuid.UUID) -> tuple[int, int]:
    accounts_result = await db.execute(
        select(func.count()).where(
            Account.user_id == user_id,
            Account.is_active.is_(True),
            Account.archived.is_(False),
        )
    )
    goals_result = await db.execute(
        select(func.count()).where(
            Goal.user_id == user_id,
            Goal.status == "active",
        )
    )
    return int(accounts_result.scalar_one()), int(goals_result.scalar_one())


async def _balance_total(db: AsyncSession, user_id: uuid.UUID) -> Decimal:
    initial_result = await db.execute(
        select(func.coalesce(func.sum(Account.initial_balance), 0)).where(
            Account.user_id == user_id,
            Account.is_active.is_(True),
            Account.archived.is_(False),
        )
    )
    movement_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.user_id == user_id,
            Transaction.status == "confirmed",
            Transaction.archived.is_(False),
        )
    )
    return Decimal(initial_result.scalar_one() or 0) + Decimal(
        movement_result.scalar_one() or 0
    )


async def _balance_split(
    db: AsyncSession, user_id: uuid.UUID
) -> tuple[Decimal, Decimal]:
    """Phase 7h — split account balances into (available, savings).

    Savings (`account_type='savings'`) is "plata apartada" and must NOT count
    toward the home-screen available total. Same `initial_balance + Σ confirmed
    transactions` convention as `_balance_total`, but the movement sum is JOINed
    to the account so it can be bucketed by type — so a checking→savings
    transfer correctly lowers `available` and raises `savings` (transfers still
    net across accounts). Only active, non-archived accounts count.
    """
    is_sav = Account.account_type == "savings"

    initial = await db.execute(
        select(
            func.coalesce(
                func.sum(case((~is_sav, Account.initial_balance), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((is_sav, Account.initial_balance), else_=0)), 0
            ),
        ).where(
            Account.user_id == user_id,
            Account.is_active.is_(True),
            Account.archived.is_(False),
        )
    )
    avail_init, sav_init = initial.one()

    movement = await db.execute(
        select(
            func.coalesce(
                func.sum(case((~is_sav, Transaction.amount), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((is_sav, Transaction.amount), else_=0)), 0
            ),
        )
        .select_from(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.user_id == user_id,
            Transaction.status == "confirmed",
            Transaction.archived.is_(False),
            Account.is_active.is_(True),
            Account.archived.is_(False),
        )
    )
    avail_mov, sav_mov = movement.one()

    available = Decimal(avail_init or 0) + Decimal(avail_mov or 0)
    savings = Decimal(sav_init or 0) + Decimal(sav_mov or 0)
    return available, savings


async def _category_breakdown(
    db: AsyncSession, *, user_id: uuid.UUID, window: DateWindow
) -> list[CategoryBreakdownItem]:
    category_label = func.coalesce(
        UserCategory.name, Transaction.category, "otros"
    )
    result = await db.execute(
        select(
            category_label.label("category"),
            func.coalesce(
                func.sum(
                    case(
                        ((Transaction.amount > 0), Transaction.amount),
                        else_=0,
                    )
                ),
                0,
            ).label("income_total"),
            func.coalesce(
                func.sum(
                    case(
                        ((Transaction.amount < 0), func.abs(Transaction.amount)),
                        else_=0,
                    )
                ),
                0,
            ).label("expense_total"),
        )
        .outerjoin(UserCategory, UserCategory.id == Transaction.category_id)
        .where(
            Transaction.user_id == user_id,
            Transaction.transaction_date >= window.start,
            Transaction.transaction_date < window.end_exclusive,
            Transaction.status == "confirmed",
            Transaction.transfer_id.is_(None),
            # Phase 7d: goal aportes/refunds are savings flows, not spending.
            Transaction.goal_id.is_(None),
            Transaction.archived.is_(False),
        )
        .group_by(category_label)
        .order_by(
            (
                func.coalesce(
                    func.sum(
                        case(
                            ((Transaction.amount > 0), Transaction.amount),
                            else_=0,
                        )
                    ),
                    0,
                )
                + func.coalesce(
                    func.sum(
                        case(
                            ((Transaction.amount < 0), func.abs(Transaction.amount)),
                            else_=0,
                        )
                    ),
                    0,
                )
            ).desc()
        )
        .limit(5)
    )
    items: list[CategoryBreakdownItem] = []
    for category, income, expense in result.fetchall():
        income_total = Decimal(income or 0)
        expense_total = Decimal(expense or 0)
        items.append(
            CategoryBreakdownItem(
                category=category,
                income_total=income_total,
                expense_total=expense_total,
                net_flow=income_total - expense_total,
            )
        )
    return items


async def get_dashboard_summary(
    db: AsyncSession, *, user: User, period: DashboardPeriod
) -> DashboardSummary:
    display_currency = user.display_currency or user.currency or "CRC"
    accounts_count, active_goals_count = await _active_counts(db, user.id)
    window = _period_window(period)
    balance_total = await _balance_total(db, user.id)
    available_balance, savings_balance = await _balance_split(db, user.id)

    # Phase 7d: goal aportes/refunds join transfer legs as "moves money but
    # is neither income nor expense" — _not_flow below excludes both.
    _is_flow = Transaction.transfer_id.is_(None) & Transaction.goal_id.is_(None)
    result = await db.execute(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (
                            (_is_flow & (Transaction.amount > 0)),
                            Transaction.amount,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (_is_flow & (Transaction.amount < 0)),
                            func.abs(Transaction.amount),
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.count(Transaction.id).filter(_is_flow),
            func.count(Transaction.id).filter(
                Transaction.transfer_id.is_not(None)
                | Transaction.goal_id.is_not(None)
            ),
        ).where(
            Transaction.user_id == user.id,
            Transaction.transaction_date >= window.start,
            Transaction.transaction_date < window.end_exclusive,
            Transaction.status == "confirmed",
            Transaction.archived.is_(False),
        )
    )
    row = result.one()
    if row is None:
        return _zero_summary(
            period=period,
            display_currency=display_currency,
            accounts_count=accounts_count,
            active_goals_count=active_goals_count,
        )

    income_total = Decimal(row[0] or 0)
    expense_total = Decimal(row[1] or 0)
    net_flow = income_total - expense_total
    savings_rate = (
        (net_flow / income_total).quantize(Decimal("0.0001"))
        if income_total > 0
        else None
    )
    return DashboardSummary(
        period=period,
        display_currency=display_currency,
        income_total=income_total,
        expense_total=expense_total,
        net_flow=net_flow,
        savings_rate=savings_rate,
        balance_total=balance_total,
        available_balance=available_balance,
        savings_balance=savings_balance,
        transaction_count=int(row[2] or 0),
        transfer_rows_excluded=int(row[3] or 0),
        accounts_count=accounts_count,
        active_goals_count=active_goals_count,
        category_breakdown=await _category_breakdown(
            db, user_id=user.id, window=window
        ),
    )


def _parse_year_month(value: str) -> date:
    try:
        year_raw, month_raw = value.split("-", 1)
        year = int(year_raw)
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError
        return date(year, month, 1)
    except ValueError as exc:
        raise ValueError("Formato esperado YYYY-MM.") from exc


def _month_range(start: date, end: date) -> list[date]:
    months: list[date] = []
    current = start
    while current <= end:
        months.append(current)
        current = _next_month_start(current)
    return months


async def get_cash_flow(
    db: AsyncSession, *, user: User, from_month: str, to_month: str
) -> CashFlowResponse:
    start = _parse_year_month(from_month)
    end = _parse_year_month(to_month)
    if end < start:
        raise ValueError("El mes final no puede ser anterior al inicial.")
    if len(_month_range(start, end)) > 36:
        raise ValueError("El rango máximo es de 36 meses.")

    end_exclusive = _next_month_start(end)
    month_expr = func.date_trunc("month", Transaction.transaction_date)
    result = await db.execute(
        select(
            month_expr.label("month"),
            func.coalesce(
                func.sum(
                    case(
                        ((Transaction.amount > 0), Transaction.amount),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        ((Transaction.amount < 0), func.abs(Transaction.amount)),
                        else_=0,
                    )
                ),
                0,
            ),
        )
        .where(
            Transaction.user_id == user.id,
            Transaction.transaction_date >= start,
            Transaction.transaction_date < end_exclusive,
            Transaction.status == "confirmed",
            Transaction.transfer_id.is_(None),
            # Phase 7d: goal flows are savings movements, not cashflow.
            Transaction.goal_id.is_(None),
        )
        .group_by(month_expr)
        .order_by(month_expr)
    )
    by_month: dict[str, tuple[Decimal, Decimal]] = {}
    for month_value, income, expense in result.fetchall():
        month_date = month_value.date() if hasattr(month_value, "date") else month_value
        by_month[month_date.strftime("%Y-%m")] = (
            Decimal(income or 0),
            Decimal(expense or 0),
        )

    points: list[CashFlowPoint] = []
    for month in _month_range(start, end):
        label = month.strftime("%Y-%m")
        income, expense = by_month.get(label, (Decimal("0"), Decimal("0")))
        points.append(
            CashFlowPoint(
                month=label,
                income_total=income,
                expense_total=expense,
                net_flow=income - expense,
            )
        )
    return CashFlowResponse(
        display_currency=user.display_currency or user.currency or "CRC",
        points=points,
    )


async def get_daily_cash_flow(
    db: AsyncSession, *, user: User, from_date: date, to_date: date
) -> DailyCashFlowResponse:
    if to_date < from_date:
        raise ValueError("La fecha final no puede ser anterior a la inicial.")
    if (to_date - from_date).days > 120:
        raise ValueError("El rango máximo es de 120 días.")

    result = await db.execute(
        select(
            Transaction.transaction_date,
            func.coalesce(
                func.sum(
                    case(
                        ((Transaction.amount > 0), Transaction.amount),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        ((Transaction.amount < 0), func.abs(Transaction.amount)),
                        else_=0,
                    )
                ),
                0,
            ),
        )
        .where(
            Transaction.user_id == user.id,
            Transaction.transaction_date >= from_date,
            Transaction.transaction_date <= to_date,
            Transaction.status == "confirmed",
            Transaction.transfer_id.is_(None),
            # Phase 7d: goal flows are savings movements, not cashflow.
            Transaction.goal_id.is_(None),
        )
        .group_by(Transaction.transaction_date)
        .order_by(Transaction.transaction_date)
    )
    by_day: dict[date, tuple[Decimal, Decimal]] = {
        day: (Decimal(income or 0), Decimal(expense or 0))
        for day, income, expense in result.fetchall()
    }
    points: list[DailyCashFlowPoint] = []
    current = from_date
    while current <= to_date:
        income, expense = by_day.get(current, (Decimal("0"), Decimal("0")))
        points.append(
            DailyCashFlowPoint(
                date=current.isoformat(),
                income_total=income,
                expense_total=expense,
                net_flow=income - expense,
            )
        )
        current = current + timedelta(days=1)

    return DailyCashFlowResponse(
        display_currency=user.display_currency or user.currency or "CRC",
        points=points,
    )

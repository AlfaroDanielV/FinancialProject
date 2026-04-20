import uuid
from datetime import date, timedelta
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.budget import Budget
from ..models.debt import Debt, DebtPayment
from ..models.goal import Goal
from ..models.transaction import Transaction
from ..models.user import User
from ..models.weekly_report import WeeklyReport
from ..schemas.reports import (
    BudgetSummary,
    CategorySpend,
    DebtProgressItem,
    DebtReportOverview,
    GenerateReportRequest,
    GoalSummary,
    UpcomingDueDate,
    WeeklyReportData,
    WeeklyReportResponse,
)
from ..services.amortization import generate_schedule

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


def _week_bounds(ref: date) -> tuple[date, date]:
    """Return Monday–Sunday window containing `ref`."""
    start = ref - timedelta(days=ref.weekday())
    end = start + timedelta(days=6)
    return start, end


@router.post("/generate", response_model=WeeklyReportResponse, status_code=201)
async def generate_weekly_report(
    payload: GenerateReportRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    user_id = user.id

    ref_date = (payload.week_start if payload and payload.week_start else date.today())
    week_start, week_end = _week_bounds(ref_date)

    # Check for existing report this week
    existing = await db.execute(
        select(WeeklyReport).where(
            WeeklyReport.user_id == user_id,
            WeeklyReport.week_start == week_start,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe un reporte para la semana del {week_start}.",
        )

    # Fetch transactions for the week
    txn_result = await db.execute(
        select(Transaction).where(
            Transaction.user_id == user_id,
            Transaction.transaction_date >= week_start,
            Transaction.transaction_date <= week_end,
        )
    )
    transactions = list(txn_result.scalars().all())

    total_income = sum(float(t.amount) for t in transactions if float(t.amount) > 0)
    total_expenses = sum(abs(float(t.amount)) for t in transactions if float(t.amount) < 0)
    net = total_income - total_expenses

    # Spend by category (expenses only)
    cat_spend: dict[str, dict] = defaultdict(lambda: {"amount": 0.0, "count": 0})
    merchant_spend: dict[str, float] = defaultdict(float)

    for t in transactions:
        amt = float(t.amount)
        if amt < 0:
            cat = t.category or "sin categoría"
            cat_spend[cat]["amount"] += abs(amt)
            cat_spend[cat]["count"] += 1
            if t.merchant:
                merchant_spend[t.merchant] += abs(amt)

    spend_by_category = sorted(
        [
            CategorySpend(category=cat, amount=round(d["amount"], 2), transaction_count=d["count"])
            for cat, d in cat_spend.items()
        ],
        key=lambda x: x.amount,
        reverse=True,
    )

    top_merchants = sorted(
        [{"merchant": m, "amount": round(a, 2)} for m, a in merchant_spend.items()],
        key=lambda x: x["amount"],
        reverse=True,
    )[:10]

    # Budget status
    budget_result = await db.execute(
        select(Budget).where(
            Budget.user_id == user_id, Budget.is_active == True  # noqa: E712
        )
    )
    budgets = list(budget_result.scalars().all())

    budget_status_list = []
    for b in budgets:
        if b.period == "monthly":
            month_start = week_end.replace(day=1)
            if week_end.month == 12:
                month_end = date(week_end.year + 1, 1, 1) - timedelta(days=1)
            else:
                month_end = date(week_end.year, week_end.month + 1, 1) - timedelta(days=1)

            month_spent_result = await db.execute(
                select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0)).where(
                    Transaction.user_id == user_id,
                    Transaction.category == b.category,
                    Transaction.transaction_date >= month_start,
                    Transaction.transaction_date <= month_end,
                    Transaction.amount < 0,
                )
            )
            spent = float(month_spent_result.scalar_one())
        else:
            spent = cat_spend.get(b.category, {}).get("amount", 0.0)

        limit = float(b.amount_limit)
        remaining = limit - spent
        percent_used = (spent / limit * 100) if limit > 0 else 0

        budget_status_list.append(BudgetSummary(
            category=b.category,
            amount_limit=limit,
            spent=round(spent, 2),
            remaining=round(remaining, 2),
            percent_used=round(percent_used, 2),
            is_over_budget=spent > limit,
        ))

    # Goal progress
    goal_result = await db.execute(
        select(Goal).where(Goal.user_id == user_id, Goal.status == "active")
    )
    goals = list(goal_result.scalars().all())

    goal_progress = [
        GoalSummary(
            name=g.name,
            target_amount=float(g.target_amount),
            current_amount=float(g.current_amount),
            progress_percent=round(
                float(g.current_amount) / float(g.target_amount) * 100, 2
            ) if float(g.target_amount) > 0 else 0,
        )
        for g in goals
    ]

    # Debt overview
    debt_result = await db.execute(
        select(Debt).where(Debt.user_id == user_id, Debt.is_active == True)  # noqa: E712
    )
    active_debts = list(debt_result.scalars().all())

    debt_overview = None
    if active_debts:
        total_owed = sum(float(d.current_balance) for d in active_debts)
        total_minimum = sum(float(d.minimum_payment) for d in active_debts)

        # Payments made this week
        payments_result = await db.execute(
            select(DebtPayment).where(
                DebtPayment.debt_id.in_([d.id for d in active_debts]),
                DebtPayment.payment_date >= week_start,
                DebtPayment.payment_date <= week_end,
            )
        )
        week_payments = list(payments_result.scalars().all())
        total_paid_this_week = sum(float(p.amount_paid) for p in week_payments)

        # Upcoming due dates (next 7 days from week_end)
        import calendar
        upcoming_cutoff = week_end + timedelta(days=7)
        upcoming_dues = []
        for d in active_debts:
            try:
                due_this_month = date(week_end.year, week_end.month, d.payment_due_day)
            except ValueError:
                last_day = calendar.monthrange(week_end.year, week_end.month)[1]
                due_this_month = date(week_end.year, week_end.month, min(d.payment_due_day, last_day))

            if due_this_month < week_end:
                if week_end.month == 12:
                    ny, nm = week_end.year + 1, 1
                else:
                    ny, nm = week_end.year, week_end.month + 1
                try:
                    due_this_month = date(ny, nm, d.payment_due_day)
                except ValueError:
                    last_day = calendar.monthrange(ny, nm)[1]
                    due_this_month = date(ny, nm, min(d.payment_due_day, last_day))

            if week_end <= due_this_month <= upcoming_cutoff:
                days_until = (due_this_month - week_end).days
                upcoming_dues.append(UpcomingDueDate(
                    name=d.name,
                    amount=float(d.minimum_payment),
                    due_date=due_this_month,
                    days_until_due=days_until,
                ))

        # Debt progress with estimated payoff
        debt_progress_items = []
        for d in active_debts:
            original = float(d.original_amount)
            current = float(d.current_balance)
            pct_paid = round((1 - current / original) * 100, 2) if original > 0 else 100

            est_payoff = None
            if current > 0 and float(d.minimum_payment) > 0:
                sched = generate_schedule(
                    balance=current,
                    annual_rate=float(d.interest_rate),
                    monthly_payment=float(d.minimum_payment),
                    due_day=d.payment_due_day,
                    start_date=date.today(),
                    includes_insurance=d.includes_insurance,
                    insurance_monthly=float(d.insurance_monthly) if d.insurance_monthly else 0.0,
                )
                est_payoff = sched.payoff_date

            debt_progress_items.append(DebtProgressItem(
                name=d.name,
                original_amount=original,
                current_balance=current,
                percent_paid=pct_paid,
                estimated_payoff_date=est_payoff,
            ))

        debt_overview = DebtReportOverview(
            total_owed=round(total_owed, 2),
            total_minimum_monthly=round(total_minimum, 2),
            payments_made_this_week=len(week_payments),
            total_paid_this_week=round(total_paid_this_week, 2),
            upcoming_due_dates=upcoming_dues,
            debt_progress=debt_progress_items,
        )

    # vs_last_week comparison
    vs_last_week = None
    prev_start = week_start - timedelta(days=7)
    prev_report_result = await db.execute(
        select(WeeklyReport).where(
            WeeklyReport.user_id == user_id,
            WeeklyReport.week_start == prev_start,
        )
    )
    prev_report = prev_report_result.scalar_one_or_none()
    if prev_report and prev_report.report_data:
        prev_expenses = prev_report.report_data.get("total_expenses", 0)
        if prev_expenses > 0:
            change_pct = round((total_expenses - prev_expenses) / prev_expenses * 100, 2)
            direction = "more" if change_pct > 0 else "less" if change_pct < 0 else "same"
            vs_last_week = {"spent_change_pct": change_pct, "direction": direction}

    report_data = WeeklyReportData(
        total_income=round(total_income, 2),
        total_expenses=round(total_expenses, 2),
        net=round(net, 2),
        transaction_count=len(transactions),
        spend_by_category=spend_by_category,
        top_merchants=top_merchants,
        budget_status=budget_status_list,
        goal_progress=goal_progress,
        debt_overview=debt_overview,
        vs_last_week=vs_last_week,
    )

    report = WeeklyReport(
        user_id=user_id,
        week_start=week_start,
        week_end=week_end,
        report_data=report_data.model_dump(mode="json"),
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


@router.get("", response_model=list[WeeklyReportResponse])
async def list_reports(
    limit: int = Query(default=10, ge=1, le=52),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(WeeklyReport)
        .where(WeeklyReport.user_id == user.id)
        .order_by(WeeklyReport.week_start.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


@router.get("/latest", response_model=WeeklyReportResponse)
async def latest_report(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(WeeklyReport)
        .where(WeeklyReport.user_id == user.id)
        .order_by(WeeklyReport.week_start.desc())
        .limit(1)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="No hay reportes generados.")
    return report


@router.get("/{report_id}", response_model=WeeklyReportResponse)
async def get_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(WeeklyReport).where(
            WeeklyReport.id == report_id, WeeklyReport.user_id == user.id
        )
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Reporte no encontrado.")
    return report

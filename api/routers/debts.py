import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import settings
from ..database import get_db
from ..models.debt import Debt, DebtPayment
from ..schemas.debts import (
    AmortizationRow,
    AmortizationSchedule,
    DebtCreate,
    DebtOverview,
    DebtPaymentCreate,
    DebtPaymentResponse,
    DebtPayoffEntry,
    DebtResponse,
    DebtSummary,
    DebtUpdate,
    EarlyPayoffRequest,
    EarlyPayoffResponse,
    PayoffStrategiesResponse,
    PayoffStrategyResult,
    SavingsSummary,
    ScheduleSummary,
    UpcomingPayment,
)
from ..services.amortization import (
    DebtInfo,
    compare_payoff_strategies,
    early_payoff_aguinaldo,
    early_payoff_increase_payment,
    early_payoff_lump_sum,
    early_payoff_reduce_payment,
    early_payoff_reduce_term,
    generate_schedule,
)

router = APIRouter(prefix="/api/v1/debts", tags=["debts"])

VARIABLE_RATE_NOTICE = (
    "Este cálculo usa la tasa actual. La tasa variable ({ref} + spread) "
    "puede cambiar semanalmente según el Banco Central."
)


def _get_default_user_id() -> uuid.UUID:
    if not settings.default_user_id:
        raise HTTPException(
            status_code=503,
            detail="DEFAULT_USER_ID not configured. Run scripts/create_user.py first.",
        )
    return uuid.UUID(settings.default_user_id)


@router.post("", response_model=DebtResponse, status_code=201)
async def create_debt(
    payload: DebtCreate,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    debt = Debt(
        user_id=user_id,
        account_id=payload.account_id,
        name=payload.name,
        debt_type=payload.debt_type,
        lender=payload.lender,
        original_amount=payload.original_amount,
        current_balance=payload.current_balance,
        interest_rate=payload.interest_rate,
        minimum_payment=payload.minimum_payment,
        payment_due_day=payload.payment_due_day,
        term_months=payload.term_months,
        start_date=payload.start_date,
        maturity_date=payload.maturity_date,
        currency=payload.currency,
        notes=payload.notes,
        rate_type=payload.rate_type,
        rate_reference=payload.rate_reference,
        rate_spread=payload.rate_spread,
        prepayment_penalty_pct=payload.prepayment_penalty_pct,
        payments_made=payload.payments_made,
        includes_insurance=payload.includes_insurance,
        insurance_monthly=payload.insurance_monthly,
    )
    db.add(debt)
    await db.commit()
    await db.refresh(debt)
    return debt


@router.get("", response_model=list[DebtSummary])
async def list_debts(
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Debt)
        .where(Debt.user_id == user_id, Debt.is_active == True)  # noqa: E712
        .order_by(Debt.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/overview", response_model=DebtOverview)
async def debt_overview(
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Debt).where(Debt.user_id == user_id, Debt.is_active == True)  # noqa: E712
    )
    debts = list(result.scalars().all())

    total_debt = sum(float(d.current_balance) for d in debts)
    total_minimum = sum(float(d.minimum_payment) for d in debts)

    by_type: dict[str, float] = {}
    for d in debts:
        by_type[d.debt_type] = by_type.get(d.debt_type, 0) + float(d.current_balance)

    today = date.today()
    cutoff = today + timedelta(days=30)
    upcoming = []
    for d in debts:
        import calendar

        try:
            due_this_month = date(today.year, today.month, d.payment_due_day)
        except ValueError:
            last_day = calendar.monthrange(today.year, today.month)[1]
            due_this_month = date(today.year, today.month, min(d.payment_due_day, last_day))

        if due_this_month < today:
            if today.month == 12:
                next_year, next_month = today.year + 1, 1
            else:
                next_year, next_month = today.year, today.month + 1
            try:
                due_this_month = date(next_year, next_month, d.payment_due_day)
            except ValueError:
                last_day = calendar.monthrange(next_year, next_month)[1]
                due_this_month = date(next_year, next_month, min(d.payment_due_day, last_day))

        if due_this_month <= cutoff:
            upcoming.append(UpcomingPayment(
                debt_id=d.id,
                name=d.name,
                debt_type=d.debt_type,
                minimum_payment=float(d.minimum_payment),
                payment_due_day=d.payment_due_day,
                current_balance=float(d.current_balance),
            ))

    return DebtOverview(
        total_debt=total_debt,
        total_minimum_monthly=total_minimum,
        debts_by_type=by_type,
        upcoming_payments=upcoming,
    )


@router.get("/payoff-strategies", response_model=PayoffStrategiesResponse)
async def payoff_strategies(
    extra_monthly: float = Query(default=0),
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Debt).where(Debt.user_id == user_id, Debt.is_active == True)  # noqa: E712
    )
    debts = list(result.scalars().all())

    if not debts:
        empty_strategy = PayoffStrategyResult(
            strategy_name="", order=[], total_months=0,
            total_interest=0, months_saved_vs_minimum=0, interest_saved_vs_minimum=0,
        )
        return PayoffStrategiesResponse(
            extra_monthly=extra_monthly,
            minimum_only=empty_strategy,
            snowball=empty_strategy,
            avalanche=empty_strategy,
            recommendation="No hay deudas activas.",
        )

    # Default extra to 10% of total minimums if not specified
    if extra_monthly <= 0:
        total_min = sum(float(d.minimum_payment) for d in debts)
        extra_monthly = round(total_min * 0.10, 2)

    debt_infos = [
        DebtInfo(
            debt_id=str(d.id),
            name=d.name,
            debt_type=d.debt_type,
            balance=float(d.current_balance),
            annual_rate=float(d.interest_rate),
            minimum_payment=float(d.minimum_payment),
            includes_insurance=d.includes_insurance,
            insurance_monthly=float(d.insurance_monthly) if d.insurance_monthly else 0.0,
        )
        for d in debts
    ]

    strategies = compare_payoff_strategies(debt_infos, extra_monthly)

    def _to_schema(s) -> PayoffStrategyResult:
        return PayoffStrategyResult(
            strategy_name=s.strategy_name,
            order=[
                DebtPayoffEntry(
                    debt_id=e.debt_id, name=e.name, debt_type=e.debt_type,
                    current_balance=e.current_balance, interest_rate=e.interest_rate,
                    minimum_payment=e.minimum_payment, payoff_month=e.payoff_month,
                    total_interest_paid=e.total_interest_paid,
                )
                for e in s.order
            ],
            total_months=s.total_months,
            total_interest=s.total_interest,
            months_saved_vs_minimum=s.months_saved_vs_minimum,
            interest_saved_vs_minimum=s.interest_saved_vs_minimum,
        )

    avalanche = strategies["avalanche"]
    snowball = strategies["snowball"]
    if avalanche.total_interest <= snowball.total_interest:
        rec = (
            f"Avalanche ahorra ₡{round(snowball.total_interest - avalanche.total_interest):,} "
            f"más en intereses que Snowball. Recomendado si la disciplina no es problema."
        )
    else:
        rec = (
            "Snowball paga menos intereses en este caso. "
            "Además ofrece victorias psicológicas tempranas."
        )

    return PayoffStrategiesResponse(
        extra_monthly=extra_monthly,
        minimum_only=_to_schema(strategies["minimum_only"]),
        snowball=_to_schema(strategies["snowball"]),
        avalanche=_to_schema(strategies["avalanche"]),
        recommendation=rec,
    )


@router.get("/{debt_id}", response_model=DebtResponse)
async def get_debt(
    debt_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Debt)
        .where(Debt.id == debt_id, Debt.user_id == user_id)
        .options(selectinload(Debt.payments))
    )
    debt = result.scalar_one_or_none()
    if not debt:
        raise HTTPException(status_code=404, detail="Deuda no encontrada.")
    return debt


@router.patch("/{debt_id}", response_model=DebtResponse)
async def update_debt(
    debt_id: uuid.UUID,
    payload: DebtUpdate,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Debt).where(Debt.id == debt_id, Debt.user_id == user_id)
    )
    debt = result.scalar_one_or_none()
    if not debt:
        raise HTTPException(status_code=404, detail="Deuda no encontrada.")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(debt, field, value)

    await db.commit()
    await db.refresh(debt)
    return debt


@router.delete("/{debt_id}", response_model=DebtResponse)
async def delete_debt(
    debt_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Debt).where(Debt.id == debt_id, Debt.user_id == user_id)
    )
    debt = result.scalar_one_or_none()
    if not debt:
        raise HTTPException(status_code=404, detail="Deuda no encontrada.")

    debt.is_active = False
    await db.commit()
    await db.refresh(debt)
    return debt


@router.post("/{debt_id}/payments", response_model=DebtPaymentResponse, status_code=201)
async def record_payment(
    debt_id: uuid.UUID,
    payload: DebtPaymentCreate,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Debt).where(Debt.id == debt_id, Debt.user_id == user_id)
    )
    debt = result.scalar_one_or_none()
    if not debt:
        raise HTTPException(status_code=404, detail="Deuda no encontrada.")

    remaining = payload.remaining_balance
    if remaining is None:
        remaining = float(debt.current_balance) - payload.amount_paid
        if remaining < 0:
            remaining = 0

    payment = DebtPayment(
        debt_id=debt_id,
        transaction_id=payload.transaction_id,
        payment_date=payload.payment_date,
        amount_paid=payload.amount_paid,
        principal_portion=payload.principal_portion,
        interest_portion=payload.interest_portion,
        extra_payment=payload.extra_payment,
        remaining_balance=remaining,
        notes=payload.notes,
    )
    db.add(payment)

    debt.current_balance = remaining
    debt.payments_made = (debt.payments_made or 0) + 1
    await db.commit()
    await db.refresh(payment)
    return payment


@router.get("/{debt_id}/payments", response_model=list[DebtPaymentResponse])
async def list_payments(
    debt_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    debt_result = await db.execute(
        select(Debt).where(Debt.id == debt_id, Debt.user_id == user_id)
    )
    if not debt_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Deuda no encontrada.")

    result = await db.execute(
        select(DebtPayment)
        .where(DebtPayment.debt_id == debt_id)
        .order_by(DebtPayment.payment_date.desc())
    )
    return list(result.scalars().all())


@router.get("/{debt_id}/amortization", response_model=AmortizationSchedule)
async def amortization_schedule(
    debt_id: uuid.UUID,
    projected_rate: float | None = Query(default=None, ge=0, le=1),
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Debt).where(Debt.id == debt_id, Debt.user_id == user_id)
    )
    debt = result.scalar_one_or_none()
    if not debt:
        raise HTTPException(status_code=404, detail="Deuda no encontrada.")

    balance = float(debt.current_balance)
    annual_rate = float(projected_rate if projected_rate is not None else debt.interest_rate)
    monthly_payment = float(debt.minimum_payment)
    insurance = float(debt.insurance_monthly) if debt.insurance_monthly else 0.0
    monthly_rate = annual_rate / 12

    if balance <= 0 or monthly_payment <= 0:
        return AmortizationSchedule(
            debt_id=debt.id, debt_name=debt.name, current_balance=balance,
            interest_rate=annual_rate, monthly_payment=monthly_payment,
            total_months=0, total_interest=0, total_principal=0,
            total_payments=0, payoff_date=None, schedule=[],
        )

    effective_payment = monthly_payment - (insurance if debt.includes_insurance else 0)
    if monthly_rate > 0 and effective_payment <= balance * monthly_rate:
        raise HTTPException(
            status_code=422,
            detail="El pago mínimo no cubre los intereses. La deuda no se puede amortizar con este pago.",
        )

    sched = generate_schedule(
        balance=balance,
        annual_rate=annual_rate,
        monthly_payment=monthly_payment,
        due_day=debt.payment_due_day,
        start_date=date.today(),
        includes_insurance=debt.includes_insurance,
        insurance_monthly=insurance,
    )

    rows = [
        AmortizationRow(
            month_number=r.month_number,
            payment_date=r.payment_date,
            payment_amount=r.payment_amount,
            principal_portion=r.principal_portion,
            interest_portion=r.interest_portion,
            insurance_portion=r.insurance_portion,
            extra_payment=r.extra_payment,
            remaining_balance=r.remaining_balance,
            cumulative_interest=r.cumulative_interest,
            cumulative_principal=r.cumulative_principal,
        )
        for r in sched.rows
    ]

    notice = None
    if debt.rate_type == "variable":
        ref = debt.rate_reference or "referencia"
        notice = VARIABLE_RATE_NOTICE.format(ref=ref)

    return AmortizationSchedule(
        debt_id=debt.id,
        debt_name=debt.name,
        current_balance=balance,
        interest_rate=annual_rate,
        monthly_payment=monthly_payment,
        total_months=sched.total_months,
        total_interest=sched.total_interest,
        total_principal=sched.total_principal,
        total_payments=sched.total_payments,
        payoff_date=sched.payoff_date,
        variable_rate_notice=notice,
        schedule=rows,
    )


@router.post("/{debt_id}/early-payoff", response_model=EarlyPayoffResponse)
async def early_payoff(
    debt_id: uuid.UUID,
    payload: EarlyPayoffRequest,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Debt).where(Debt.id == debt_id, Debt.user_id == user_id)
    )
    debt = result.scalar_one_or_none()
    if not debt:
        raise HTTPException(status_code=404, detail="Deuda no encontrada.")

    balance = float(debt.current_balance)
    annual_rate = float(
        payload.projected_rate if payload.projected_rate is not None else debt.interest_rate
    )
    monthly_payment = float(debt.minimum_payment)
    insurance = float(debt.insurance_monthly) if debt.insurance_monthly else 0.0

    common = dict(
        balance=balance,
        annual_rate=annual_rate,
        monthly_payment=monthly_payment,
        due_day=debt.payment_due_day,
        start_date=date.today(),
        includes_insurance=debt.includes_insurance,
        insurance_monthly=insurance,
        payments_made=debt.payments_made or 0,
        prepayment_penalty_pct=float(debt.prepayment_penalty_pct or 0),
    )

    if payload.strategy == "increase_payment":
        ep = early_payoff_increase_payment(**common, extra_monthly=payload.extra_monthly)
    elif payload.strategy == "lump_sum":
        ep = early_payoff_lump_sum(
            **common,
            lump_sum_amount=payload.lump_sum_amount,
            lump_sum_month=payload.lump_sum_month or 1,
        )
    elif payload.strategy == "aguinaldo":
        ep = early_payoff_aguinaldo(**common, aguinaldo_amount=payload.aguinaldo_amount)
    elif payload.strategy == "reduce_term":
        ep = early_payoff_reduce_term(**common, target_months=payload.target_months)
    elif payload.strategy == "reduce_payment":
        ep = early_payoff_reduce_payment(**common, target_payment=payload.target_payment)
    else:
        raise HTTPException(status_code=400, detail="Estrategia no válida.")

    notice = None
    if debt.rate_type == "variable":
        ref = debt.rate_reference or "referencia"
        notice = VARIABLE_RATE_NOTICE.format(ref=ref)

    return EarlyPayoffResponse(
        original_schedule=ScheduleSummary(
            monthly_payment=ep.original.monthly_payment,
            total_months=ep.original.total_months,
            total_interest=ep.original.total_interest,
            total_paid=ep.original.total_payments,
            payoff_date=ep.original.payoff_date,
        ),
        proposed_schedule=ScheduleSummary(
            monthly_payment=ep.proposed.monthly_payment,
            total_months=ep.proposed.total_months,
            total_interest=ep.proposed.total_interest,
            total_paid=ep.proposed.total_payments,
            payoff_date=ep.proposed.payoff_date,
        ),
        savings=SavingsSummary(
            months_saved=ep.months_saved,
            interest_saved=ep.interest_saved,
            total_saved=ep.total_saved,
            new_payoff_date=ep.new_payoff_date,
            prepayment_penalty_applies=ep.prepayment_penalty_applies,
            prepayment_penalty_amount=ep.prepayment_penalty_amount,
        ),
        monthly_impact=ep.monthly_impact,
        strategy=ep.strategy,
        currency=debt.currency,
        variable_rate_notice=notice,
    )

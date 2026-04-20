"""
French amortization engine with Costa Rica-specific features.

- Sistema Francés (fixed cuota, decreasing interest, increasing principal)
- 5 early payoff strategies: increase_payment, lump_sum, aguinaldo, reduce_term, reduce_payment
- Prepayment penalty logic per Ley 7472 (Art. 36 bis)
- Snowball vs Avalanche comparison
- Variable rate handling
- Insurance deduction
"""
from __future__ import annotations

import math
import calendar
from dataclasses import dataclass, field
from datetime import date


MAX_MONTHS = 600  # 50-year cap


@dataclass
class AmortRow:
    month_number: int
    payment_date: date
    payment_amount: float
    principal_portion: float
    interest_portion: float
    insurance_portion: float
    extra_payment: float
    remaining_balance: float
    cumulative_interest: float
    cumulative_principal: float


@dataclass
class ScheduleResult:
    rows: list[AmortRow]
    monthly_payment: float
    total_months: int
    total_interest: float
    total_principal: float
    total_payments: float
    payoff_date: date | None


@dataclass
class EarlyPayoffResult:
    original: ScheduleResult
    proposed: ScheduleResult
    months_saved: int
    interest_saved: float
    total_saved: float
    new_payoff_date: date | None
    prepayment_penalty_applies: bool
    prepayment_penalty_amount: float
    monthly_impact: float
    strategy: str


@dataclass
class DebtPayoffEntry:
    debt_id: str
    name: str
    debt_type: str
    current_balance: float
    interest_rate: float
    minimum_payment: float
    payoff_month: int
    total_interest_paid: float


@dataclass
class StrategyResult:
    strategy_name: str
    order: list[DebtPayoffEntry]
    total_months: int
    total_interest: float
    months_saved_vs_minimum: int
    interest_saved_vs_minimum: float


def _next_payment_date(start: date, due_day: int, month_offset: int) -> date:
    """Compute the payment date for month_offset months after start."""
    total_months = start.month - 1 + month_offset
    year = start.year + total_months // 12
    month = total_months % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(due_day, last_day)
    return date(year, month, day)


def compute_french_payment(principal: float, monthly_rate: float, n_months: int) -> float:
    """Standard French amortization monthly payment (PMT formula)."""
    if monthly_rate == 0:
        return principal / n_months if n_months > 0 else principal
    return principal * monthly_rate * (1 + monthly_rate) ** n_months / (
        (1 + monthly_rate) ** n_months - 1
    )


def months_to_payoff(principal: float, monthly_rate: float, payment: float) -> int:
    """Number of months to pay off at a given payment amount."""
    if monthly_rate == 0:
        return math.ceil(principal / payment) if payment > 0 else MAX_MONTHS
    if payment <= principal * monthly_rate:
        return MAX_MONTHS  # Can never pay off
    n = -math.log(1 - principal * monthly_rate / payment) / math.log(1 + monthly_rate)
    return math.ceil(n)


def generate_schedule(
    balance: float,
    annual_rate: float,
    monthly_payment: float,
    due_day: int,
    start_date: date | None = None,
    includes_insurance: bool = False,
    insurance_monthly: float = 0.0,
    extra_monthly: float = 0.0,
    lump_sum_amount: float = 0.0,
    lump_sum_month: int = 0,
    aguinaldo_amount: float = 0.0,
    term_months: int | None = None,
) -> ScheduleResult:
    """
    Generate a full amortization schedule.

    The monthly_payment is the total cuota (including insurance if bundled).
    Insurance is deducted before the principal/interest split.
    """
    if balance <= 0 or monthly_payment <= 0:
        return ScheduleResult(
            rows=[], monthly_payment=monthly_payment, total_months=0,
            total_interest=0, total_principal=0, total_payments=0, payoff_date=None,
        )

    monthly_rate = annual_rate / 12
    insurance = insurance_monthly if includes_insurance else 0.0

    # Effective payment toward debt (excluding insurance)
    base_debt_payment = monthly_payment - insurance

    if monthly_rate > 0 and base_debt_payment <= balance * monthly_rate and extra_monthly == 0:
        # Payment doesn't cover interest — will never pay off
        return ScheduleResult(
            rows=[], monthly_payment=monthly_payment, total_months=MAX_MONTHS,
            total_interest=0, total_principal=0, total_payments=0, payoff_date=None,
        )

    ref_date = start_date or date.today()
    rows: list[AmortRow] = []
    cum_interest = 0.0
    cum_principal = 0.0
    month = 0

    while balance > 0.01 and month < MAX_MONTHS:
        month += 1
        pay_date = _next_payment_date(ref_date, due_day, month)

        # Determine extra payments this month
        extra = extra_monthly

        # Lump sum in specific month
        if lump_sum_amount > 0 and month == lump_sum_month:
            extra += lump_sum_amount

        # Aguinaldo in December
        if aguinaldo_amount > 0 and pay_date.month == 12:
            extra += aguinaldo_amount

        interest = round(balance * monthly_rate, 2)

        # Total payment this month toward debt (base + extra)
        total_debt_payment = base_debt_payment + extra

        if balance + interest <= total_debt_payment:
            # Final payment
            principal = balance
            actual_interest = interest
            actual_payment = balance + interest + insurance
            actual_extra = max(0, total_debt_payment - base_debt_payment)
            # Adjust extra for final payment — can't pay more than owed
            if principal + actual_interest < total_debt_payment:
                actual_extra = 0
            balance = 0
        else:
            principal = total_debt_payment - interest
            if principal < 0:
                # Payment doesn't even cover interest this month
                principal = 0
                actual_interest = total_debt_payment
                actual_payment = total_debt_payment + insurance
                actual_extra = extra
                balance = balance + (interest - total_debt_payment)
            else:
                actual_interest = interest
                actual_payment = total_debt_payment + insurance
                actual_extra = extra
                balance -= principal

        cum_interest += actual_interest
        cum_principal += principal

        rows.append(AmortRow(
            month_number=month,
            payment_date=pay_date,
            payment_amount=round(actual_payment, 2),
            principal_portion=round(principal, 2),
            interest_portion=round(actual_interest, 2),
            insurance_portion=round(insurance, 2),
            extra_payment=round(actual_extra, 2),
            remaining_balance=round(max(balance, 0), 2),
            cumulative_interest=round(cum_interest, 2),
            cumulative_principal=round(cum_principal, 2),
        ))

    total_payments = sum(r.payment_amount for r in rows)
    payoff = rows[-1].payment_date if rows else None

    return ScheduleResult(
        rows=rows,
        monthly_payment=round(monthly_payment + extra_monthly, 2),
        total_months=month if balance <= 0.01 else MAX_MONTHS,
        total_interest=round(cum_interest, 2),
        total_principal=round(cum_principal, 2),
        total_payments=round(total_payments, 2),
        payoff_date=payoff,
    )


def calculate_prepayment_penalty(
    payments_made: int,
    prepayment_penalty_pct: float,
    prepayment_amount: float,
) -> tuple[float, bool]:
    """
    Ley 7472, Art. 36 bis: no prepayment commission after 2 payments.
    Returns (penalty_amount, penalty_applies).
    """
    if payments_made >= 2:
        return 0.0, False
    penalty_rate = prepayment_penalty_pct or 0.0
    penalty = round(prepayment_amount * penalty_rate, 2)
    return penalty, penalty_rate > 0


def early_payoff_increase_payment(
    balance: float,
    annual_rate: float,
    monthly_payment: float,
    due_day: int,
    extra_monthly: float,
    start_date: date | None = None,
    includes_insurance: bool = False,
    insurance_monthly: float = 0.0,
    payments_made: int = 0,
    prepayment_penalty_pct: float = 0.0,
) -> EarlyPayoffResult:
    original = generate_schedule(
        balance, annual_rate, monthly_payment, due_day,
        start_date=start_date, includes_insurance=includes_insurance,
        insurance_monthly=insurance_monthly,
    )
    proposed = generate_schedule(
        balance, annual_rate, monthly_payment, due_day,
        start_date=start_date, includes_insurance=includes_insurance,
        insurance_monthly=insurance_monthly, extra_monthly=extra_monthly,
    )
    penalty_amt, penalty_applies = calculate_prepayment_penalty(
        payments_made, prepayment_penalty_pct, balance,
    )
    interest_saved = original.total_interest - proposed.total_interest
    return EarlyPayoffResult(
        original=original, proposed=proposed,
        months_saved=original.total_months - proposed.total_months,
        interest_saved=round(interest_saved, 2),
        total_saved=round(interest_saved - penalty_amt, 2),
        new_payoff_date=proposed.payoff_date,
        prepayment_penalty_applies=penalty_applies,
        prepayment_penalty_amount=penalty_amt,
        monthly_impact=round(extra_monthly, 2),
        strategy="increase_payment",
    )


def early_payoff_lump_sum(
    balance: float,
    annual_rate: float,
    monthly_payment: float,
    due_day: int,
    lump_sum_amount: float,
    lump_sum_month: int = 1,
    start_date: date | None = None,
    includes_insurance: bool = False,
    insurance_monthly: float = 0.0,
    payments_made: int = 0,
    prepayment_penalty_pct: float = 0.0,
) -> EarlyPayoffResult:
    original = generate_schedule(
        balance, annual_rate, monthly_payment, due_day,
        start_date=start_date, includes_insurance=includes_insurance,
        insurance_monthly=insurance_monthly,
    )
    proposed = generate_schedule(
        balance, annual_rate, monthly_payment, due_day,
        start_date=start_date, includes_insurance=includes_insurance,
        insurance_monthly=insurance_monthly,
        lump_sum_amount=lump_sum_amount, lump_sum_month=lump_sum_month,
    )
    penalty_amt, penalty_applies = calculate_prepayment_penalty(
        payments_made, prepayment_penalty_pct, lump_sum_amount,
    )
    interest_saved = original.total_interest - proposed.total_interest
    return EarlyPayoffResult(
        original=original, proposed=proposed,
        months_saved=original.total_months - proposed.total_months,
        interest_saved=round(interest_saved, 2),
        total_saved=round(interest_saved - penalty_amt, 2),
        new_payoff_date=proposed.payoff_date,
        prepayment_penalty_applies=penalty_applies,
        prepayment_penalty_amount=penalty_amt,
        monthly_impact=0,
        strategy="lump_sum",
    )


def early_payoff_aguinaldo(
    balance: float,
    annual_rate: float,
    monthly_payment: float,
    due_day: int,
    aguinaldo_amount: float,
    start_date: date | None = None,
    includes_insurance: bool = False,
    insurance_monthly: float = 0.0,
    payments_made: int = 0,
    prepayment_penalty_pct: float = 0.0,
) -> EarlyPayoffResult:
    original = generate_schedule(
        balance, annual_rate, monthly_payment, due_day,
        start_date=start_date, includes_insurance=includes_insurance,
        insurance_monthly=insurance_monthly,
    )
    proposed = generate_schedule(
        balance, annual_rate, monthly_payment, due_day,
        start_date=start_date, includes_insurance=includes_insurance,
        insurance_monthly=insurance_monthly, aguinaldo_amount=aguinaldo_amount,
    )
    penalty_amt, penalty_applies = calculate_prepayment_penalty(
        payments_made, prepayment_penalty_pct, aguinaldo_amount,
    )
    interest_saved = original.total_interest - proposed.total_interest
    return EarlyPayoffResult(
        original=original, proposed=proposed,
        months_saved=original.total_months - proposed.total_months,
        interest_saved=round(interest_saved, 2),
        total_saved=round(interest_saved - penalty_amt, 2),
        new_payoff_date=proposed.payoff_date,
        prepayment_penalty_applies=penalty_applies,
        prepayment_penalty_amount=penalty_amt,
        monthly_impact=0,
        strategy="aguinaldo",
    )


def early_payoff_reduce_term(
    balance: float,
    annual_rate: float,
    monthly_payment: float,
    due_day: int,
    target_months: int,
    start_date: date | None = None,
    includes_insurance: bool = False,
    insurance_monthly: float = 0.0,
    payments_made: int = 0,
    prepayment_penalty_pct: float = 0.0,
) -> EarlyPayoffResult:
    """Calculate the required payment to pay off in target_months."""
    monthly_rate = annual_rate / 12
    insurance = insurance_monthly if includes_insurance else 0.0
    required_debt_payment = compute_french_payment(balance, monthly_rate, target_months)
    required_total = required_debt_payment + insurance
    extra = required_total - monthly_payment

    original = generate_schedule(
        balance, annual_rate, monthly_payment, due_day,
        start_date=start_date, includes_insurance=includes_insurance,
        insurance_monthly=insurance_monthly,
    )
    proposed = generate_schedule(
        balance, annual_rate, required_total, due_day,
        start_date=start_date, includes_insurance=includes_insurance,
        insurance_monthly=insurance_monthly,
    )
    penalty_amt, penalty_applies = calculate_prepayment_penalty(
        payments_made, prepayment_penalty_pct, balance,
    )
    interest_saved = original.total_interest - proposed.total_interest
    return EarlyPayoffResult(
        original=original, proposed=proposed,
        months_saved=original.total_months - proposed.total_months,
        interest_saved=round(interest_saved, 2),
        total_saved=round(interest_saved - penalty_amt, 2),
        new_payoff_date=proposed.payoff_date,
        prepayment_penalty_applies=penalty_applies,
        prepayment_penalty_amount=penalty_amt,
        monthly_impact=round(extra, 2),
        strategy="reduce_term",
    )


def early_payoff_reduce_payment(
    balance: float,
    annual_rate: float,
    monthly_payment: float,
    due_day: int,
    target_payment: float,
    start_date: date | None = None,
    includes_insurance: bool = False,
    insurance_monthly: float = 0.0,
    payments_made: int = 0,
    prepayment_penalty_pct: float = 0.0,
) -> EarlyPayoffResult:
    """Calculate the resulting term at a given target payment."""
    original = generate_schedule(
        balance, annual_rate, monthly_payment, due_day,
        start_date=start_date, includes_insurance=includes_insurance,
        insurance_monthly=insurance_monthly,
    )
    proposed = generate_schedule(
        balance, annual_rate, target_payment, due_day,
        start_date=start_date, includes_insurance=includes_insurance,
        insurance_monthly=insurance_monthly,
    )
    penalty_amt, penalty_applies = calculate_prepayment_penalty(
        payments_made, prepayment_penalty_pct, balance,
    )
    interest_saved = original.total_interest - proposed.total_interest
    monthly_diff = target_payment - monthly_payment
    return EarlyPayoffResult(
        original=original, proposed=proposed,
        months_saved=original.total_months - proposed.total_months,
        interest_saved=round(interest_saved, 2),
        total_saved=round(interest_saved - penalty_amt, 2),
        new_payoff_date=proposed.payoff_date,
        prepayment_penalty_applies=penalty_applies,
        prepayment_penalty_amount=penalty_amt,
        monthly_impact=round(monthly_diff, 2),
        strategy="reduce_payment",
    )


@dataclass
class DebtInfo:
    debt_id: str
    name: str
    debt_type: str
    balance: float
    annual_rate: float
    minimum_payment: float
    includes_insurance: bool = False
    insurance_monthly: float = 0.0


def _simulate_multi_debt_payoff(
    debts: list[DebtInfo],
    extra_monthly: float,
    order_key,
) -> StrategyResult:
    """
    Simulate paying off multiple debts using a given ordering strategy.
    order_key: function that returns the sort key for prioritization.
    """
    # Deep copy balances
    balances = {d.debt_id: d.balance for d in debts}
    minimums = {d.debt_id: d.minimum_payment for d in debts}
    rates = {d.debt_id: d.annual_rate for d in debts}
    insurances = {
        d.debt_id: d.insurance_monthly if d.includes_insurance else 0.0
        for d in debts
    }
    interest_paid = {d.debt_id: 0.0 for d in debts}
    payoff_months = {d.debt_id: 0 for d in debts}

    # Sort debts by the strategy's priority
    sorted_ids = [d.debt_id for d in sorted(debts, key=order_key)]

    month = 0
    while any(balances[did] > 0.01 for did in balances) and month < MAX_MONTHS:
        month += 1

        # Find which debts are still active
        active_ids = [did for did in sorted_ids if balances[did] > 0.01]
        if not active_ids:
            break

        # Pay minimum on all active debts
        freed_extra = 0.0
        for did in active_ids:
            monthly_rate = rates[did] / 12
            insurance = insurances[did]
            min_pay = minimums[did]
            debt_payment = min_pay - insurance

            interest = balances[did] * monthly_rate
            interest_paid[did] += interest

            principal = debt_payment - interest
            if principal < 0:
                principal = 0

            if balances[did] <= principal + 0.01:
                interest_paid[did] -= interest
                interest_paid[did] += balances[did] * monthly_rate
                balances[did] = 0
                payoff_months[did] = month
                # Freed-up minimum goes to extra pool
                freed_extra += min_pay
            else:
                balances[did] -= principal

        # Apply extra to the priority debt
        remaining_extra = extra_monthly + freed_extra
        # Only apply freed extra from debts paid off THIS month
        # Actually freed_extra already accounts for debts that hit zero above

        # Apply extra payment to highest-priority active debt
        for did in active_ids:
            if balances[did] <= 0.01:
                continue
            if remaining_extra <= 0:
                break

            monthly_rate = rates[did] / 12
            # Extra goes straight to principal
            if balances[did] <= remaining_extra:
                remaining_extra -= balances[did]
                balances[did] = 0
                payoff_months[did] = month
            else:
                balances[did] -= remaining_extra
                remaining_extra = 0

    # Build result
    debt_map = {d.debt_id: d for d in debts}
    order = []
    for did in sorted_ids:
        d = debt_map[did]
        order.append(DebtPayoffEntry(
            debt_id=did,
            name=d.name,
            debt_type=d.debt_type,
            current_balance=d.balance,
            interest_rate=d.annual_rate,
            minimum_payment=d.minimum_payment,
            payoff_month=payoff_months[did],
            total_interest_paid=round(interest_paid[did], 2),
        ))

    total_months = max(payoff_months.values()) if payoff_months else 0
    total_interest = round(sum(interest_paid.values()), 2)

    return StrategyResult(
        strategy_name="",
        order=order,
        total_months=total_months,
        total_interest=total_interest,
        months_saved_vs_minimum=0,
        interest_saved_vs_minimum=0,
    )


def compare_payoff_strategies(
    debts: list[DebtInfo],
    extra_monthly: float,
) -> dict[str, StrategyResult]:
    """Compare snowball (smallest balance first) vs avalanche (highest rate first)."""
    if not debts:
        empty = StrategyResult("", [], 0, 0, 0, 0)
        return {"snowball": empty, "avalanche": empty, "minimum_only": empty}

    # Minimum-only baseline (no extra)
    minimum_only = _simulate_multi_debt_payoff(
        debts, 0.0, order_key=lambda d: d.balance,
    )
    minimum_only.strategy_name = "minimum_only"

    # Snowball: smallest balance first
    snowball = _simulate_multi_debt_payoff(
        debts, extra_monthly, order_key=lambda d: d.balance,
    )
    snowball.strategy_name = "snowball"
    snowball.months_saved_vs_minimum = minimum_only.total_months - snowball.total_months
    snowball.interest_saved_vs_minimum = round(
        minimum_only.total_interest - snowball.total_interest, 2,
    )

    # Avalanche: highest rate first (negative for descending sort)
    avalanche = _simulate_multi_debt_payoff(
        debts, extra_monthly, order_key=lambda d: -d.annual_rate,
    )
    avalanche.strategy_name = "avalanche"
    avalanche.months_saved_vs_minimum = minimum_only.total_months - avalanche.total_months
    avalanche.interest_saved_vs_minimum = round(
        minimum_only.total_interest - avalanche.total_interest, 2,
    )

    return {
        "minimum_only": minimum_only,
        "snowball": snowball,
        "avalanche": avalanche,
    }

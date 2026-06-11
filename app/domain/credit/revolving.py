"""Revolving-credit projections — pure math, no LLM/DB/network (Phase 7b B4).

Model (simple revolving, monthly compounding, no new purchases):

    interest_m = balance × (annual_rate / 12)
    payment_m  = min(balance + interest_m, max(pct × balance, floor))
    balance'   = balance + interest_m − payment_m

CR cards commonly state the minimum as a percent of the balance with a fixed
floor ("2.5% del saldo, mínimo ₡5.000"). The floor is what makes a
minimum-only payoff terminate; without one — or when pct ≤ annual_rate/12 —
the balance never falls and the projection reports `never_pays_off=True`.
That honest verdict IS the feature ("pagando solo el mínimo NUNCA la terminás
de pagar").

All money is Decimal quantized to cents; components reconcile exactly:
total_paid == total_interest + initial_balance whenever the card pays off.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

_CENTS = Decimal("0.01")

# 50 years of monthly payments. Anything longer is "never" for a human.
MAX_MONTHS = 600


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENTS)


def compute_minimum(
    balance: Decimal,
    *,
    minimum_pct: Decimal,
    minimum_floor: Decimal | None = None,
) -> Decimal:
    """Minimum due on `balance`: max(pct × balance, floor), capped at the
    balance itself (you never owe a minimum above what you owe)."""
    if balance <= 0:
        return Decimal("0.00")
    if minimum_pct <= 0 or minimum_pct > 1:
        raise ValueError("minimum_pct must be a 0–1 fraction > 0")
    floor = minimum_floor if minimum_floor is not None else Decimal("0")
    if floor < 0:
        raise ValueError("minimum_floor must be >= 0")
    return _money(min(balance, max(balance * minimum_pct, floor)))


@dataclass(frozen=True)
class MonthRow:
    month: int
    payment: Decimal
    interest: Decimal
    principal: Decimal
    ending_balance: Decimal


@dataclass(frozen=True)
class PayoffProjection:
    """Outcome of paying a card month by month with no new purchases.

    `months is None` ⟺ `never_pays_off=True` — the payment plan doesn't
    retire the debt within MAX_MONTHS (or the balance grows). In that case
    total_paid/total_interest cover only the simulated window.
    """

    months: int | None
    total_paid: Decimal
    total_interest: Decimal
    never_pays_off: bool
    first_month_interest: Decimal
    rows: tuple[MonthRow, ...]


def _validate(balance: Decimal, annual_rate: Decimal) -> None:
    if balance < 0:
        raise ValueError("balance must be >= 0 (owed magnitude)")
    if annual_rate < 0 or annual_rate > 1:
        raise ValueError("annual_rate must be a 0–1 fraction")


def project_minimum_only(
    balance: Decimal,
    *,
    annual_rate: Decimal,
    minimum_pct: Decimal,
    minimum_floor: Decimal | None = None,
    max_months: int = MAX_MONTHS,
) -> PayoffProjection:
    """Pay exactly the minimum every month. The "minimum trap" projection."""
    _validate(balance, annual_rate)
    monthly_rate = annual_rate / 12

    if balance == 0:
        return PayoffProjection(
            months=0,
            total_paid=Decimal("0.00"),
            total_interest=Decimal("0.00"),
            never_pays_off=False,
            first_month_interest=Decimal("0.00"),
            rows=(),
        )

    rows: list[MonthRow] = []
    remaining = _money(balance)
    total_paid = Decimal("0")
    total_interest = Decimal("0")
    first_month_interest = _money(remaining * monthly_rate)

    for month in range(1, max_months + 1):
        interest = _money(remaining * monthly_rate)
        payment = compute_minimum(
            remaining + interest,
            minimum_pct=minimum_pct,
            minimum_floor=minimum_floor,
        )
        # The minimum doesn't even cover the month's interest → the balance
        # grows forever. Stop immediately; the verdict is the point.
        if payment <= interest and remaining + interest - payment >= remaining:
            return PayoffProjection(
                months=None,
                total_paid=_money(total_paid),
                total_interest=_money(total_interest),
                never_pays_off=True,
                first_month_interest=first_month_interest,
                rows=tuple(rows),
            )
        principal = _money(payment - interest)
        remaining = _money(remaining + interest - payment)
        total_paid += payment
        total_interest += interest
        rows.append(
            MonthRow(
                month=month,
                payment=payment,
                interest=interest,
                principal=principal,
                ending_balance=remaining,
            )
        )
        if remaining <= 0:
            return PayoffProjection(
                months=month,
                total_paid=_money(total_paid),
                total_interest=_money(total_interest),
                never_pays_off=False,
                first_month_interest=first_month_interest,
                rows=tuple(rows),
            )

    # Still owing after max_months → effectively never.
    return PayoffProjection(
        months=None,
        total_paid=_money(total_paid),
        total_interest=_money(total_interest),
        never_pays_off=True,
        first_month_interest=first_month_interest,
        rows=tuple(rows),
    )


def project_fixed_payment(
    balance: Decimal,
    *,
    annual_rate: Decimal,
    payment: Decimal,
    max_months: int = MAX_MONTHS,
) -> PayoffProjection:
    """Pay a fixed amount every month (last month pays the remainder)."""
    _validate(balance, annual_rate)
    if payment <= 0:
        raise ValueError("payment must be > 0")
    monthly_rate = annual_rate / 12

    if balance == 0:
        return PayoffProjection(
            months=0,
            total_paid=Decimal("0.00"),
            total_interest=Decimal("0.00"),
            never_pays_off=False,
            first_month_interest=Decimal("0.00"),
            rows=(),
        )

    rows: list[MonthRow] = []
    remaining = _money(balance)
    total_paid = Decimal("0")
    total_interest = Decimal("0")
    first_month_interest = _money(remaining * monthly_rate)

    if payment <= first_month_interest:
        return PayoffProjection(
            months=None,
            total_paid=Decimal("0.00"),
            total_interest=Decimal("0.00"),
            never_pays_off=True,
            first_month_interest=first_month_interest,
            rows=(),
        )

    for month in range(1, max_months + 1):
        interest = _money(remaining * monthly_rate)
        due = _money(min(payment, remaining + interest))
        # Per-month cent rounding can leave a sub-unit residue that would
        # produce a phantom extra month (e.g. a 13th month paying 0.03 on a
        # 12-month French payment). Absorb anything within one currency unit
        # into this month's payment — reconciliation stays exact because the
        # final payment is remaining + interest by construction.
        if remaining + interest - due <= Decimal("1.00"):
            due = _money(remaining + interest)
        principal = _money(due - interest)
        remaining = _money(remaining + interest - due)
        total_paid += due
        total_interest += interest
        rows.append(
            MonthRow(
                month=month,
                payment=due,
                interest=interest,
                principal=principal,
                ending_balance=remaining,
            )
        )
        if remaining <= 0:
            return PayoffProjection(
                months=month,
                total_paid=_money(total_paid),
                total_interest=_money(total_interest),
                never_pays_off=False,
                first_month_interest=first_month_interest,
                rows=tuple(rows),
            )

    return PayoffProjection(
        months=None,
        total_paid=_money(total_paid),
        total_interest=_money(total_interest),
        never_pays_off=True,
        first_month_interest=first_month_interest,
        rows=tuple(rows),
    )


def payment_for_months(
    balance: Decimal, *, annual_rate: Decimal, months: int
) -> Decimal:
    """Fixed monthly payment that retires `balance` in `months` (French
    formula). Used for "pagala en N meses" strategy rows."""
    _validate(balance, annual_rate)
    if months <= 0:
        raise ValueError("months must be > 0")
    if balance == 0:
        return Decimal("0.00")
    monthly_rate = annual_rate / 12
    if monthly_rate == 0:
        return _money(balance / months)
    factor = (1 + monthly_rate) ** months
    return _money(balance * monthly_rate * factor / (factor - 1))

"""Deterministic affordability assessment (Phase 7 — pushback engine).

The math is deterministic and lives here; the LLM only explains the result
(see ``app/queries/tools/affordability.py``). This preserves the project rule
"LLM extracts / explains; rules decide" — nothing in this module calls an LLM,
and nothing here invents a number.

Inputs are pulled from data the user already has on file:

- monthly income → active recurring incomes, normalized to a monthly figure
  (reuses ``api/services/envelopes.py::_monthly_income`` so the affordability
  answer can't drift from the envelope summary's income line).
- fixed expenses → active, fixed-amount recurring bills, normalized to monthly.
- commitments → active debts' monthly minimum payments.

All figures are converted to the user's currency via ``api/services/fx.py``
(the CRC/USD rate is the documented ₡500 placeholder until the BCCR worker
lands; bills/debt/income are overwhelmingly CRC, so this is immaterial today).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.debt import Debt
from ...models.recurring_bill import RecurringBill
from ...models.user import User
from ..envelopes import _monthly_income
from ..fx import convert

# 80% of disposable income is the safe ceiling — the margin the CLAUDE.md
# affordability spec mandates. A plan is "feasible" only if its monthly
# requirement fits inside this margin, leaving headroom for the unbudgeted.
SAFETY_MARGIN = Decimal("0.80")

_CENTS = Decimal("0.01")
_ZERO = Decimal("0")

# Normalize each recurring-bill cadence to a monthly figure. Mirrors the income
# normalization in api/services/envelopes.py but covers the full bill frequency
# set. 'custom' (RRULE) can't be reduced to a fixed monthly amount, so it is
# excluded and surfaced as a caveat instead of being guessed.
_BILL_FREQ_TO_MONTHLY = {
    "weekly": Decimal("52") / Decimal("12"),
    "biweekly": Decimal("26") / Decimal("12"),
    "monthly": Decimal("1"),
    "bimonthly": Decimal("1") / Decimal("2"),
    "quarterly": Decimal("1") / Decimal("3"),
    "semiannual": Decimal("1") / Decimal("6"),
    "annual": Decimal("1") / Decimal("12"),
}


def _q(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class AffordabilityInputs:
    currency: str
    monthly_income: Optional[Decimal]  # None when no recurring income on file
    monthly_fixed_expenses: Decimal
    monthly_debt_payments: Decimal
    excluded_variable_bills: int  # variable-amount bills we couldn't price
    excluded_custom_bills: int  # RRULE bills we couldn't normalize


@dataclass(frozen=True)
class AffordabilityResult:
    feasible: Optional[bool]  # None when income is unknown
    currency: str
    desired_amount: Decimal
    timeline_months: int
    monthly_income: Optional[Decimal]
    monthly_fixed_expenses: Decimal
    monthly_debt_payments: Decimal
    monthly_disposable: Optional[Decimal]
    safe_monthly_disposable: Optional[Decimal]
    monthly_needed: Decimal
    shortfall: Optional[Decimal]  # vs the safe ceiling; 0 when feasible
    min_timeline_months_feasible: Optional[int]
    max_amount_feasible_in_timeline: Optional[Decimal]
    notes: tuple[str, ...] = ()


def assess_affordability(
    *,
    monthly_income: Optional[Decimal],
    monthly_fixed_expenses: Decimal,
    monthly_debt_payments: Decimal,
    desired_amount: Decimal,
    timeline_months: int = 1,
    currency: str = "CRC",
    excluded_variable_bills: int = 0,
    excluded_custom_bills: int = 0,
) -> AffordabilityResult:
    """Pure deterministic affordability math. No DB, no LLM.

    ``timeline_months=1`` models an immediate purchase ("¿puedo con X?"); a
    larger horizon models saving toward a target ("¿me alcanza para Y en N
    meses?"). The single ``monthly_needed <= safe_disposable`` test covers both.
    """
    months = max(1, int(timeline_months))
    desired = _q(Decimal(desired_amount))
    monthly_needed = _q(desired / Decimal(months))

    notes: list[str] = []
    if excluded_variable_bills:
        notes.append(
            f"{excluded_variable_bills} gasto(s) recurrente(s) de monto variable "
            "no se incluyeron en los gastos fijos."
        )
    if excluded_custom_bills:
        notes.append(
            f"{excluded_custom_bills} gasto(s) recurrente(s) con regla "
            "personalizada no se pudieron convertir a un monto mensual."
        )

    fixed = _q(Decimal(monthly_fixed_expenses))
    commitments = _q(Decimal(monthly_debt_payments))

    # No income on file → refuse to fabricate a disposable figure. Honest
    # 'unknown' so the LLM asks the user to register income instead of guessing.
    if monthly_income is None:
        notes.append(
            "No hay ingresos recurrentes registrados; no puedo calcular el "
            "disponible."
        )
        return AffordabilityResult(
            feasible=None,
            currency=currency,
            desired_amount=desired,
            timeline_months=months,
            monthly_income=None,
            monthly_fixed_expenses=fixed,
            monthly_debt_payments=commitments,
            monthly_disposable=None,
            safe_monthly_disposable=None,
            monthly_needed=monthly_needed,
            shortfall=None,
            min_timeline_months_feasible=None,
            max_amount_feasible_in_timeline=None,
            notes=tuple(notes),
        )

    disposable = _q(Decimal(monthly_income) - fixed - commitments)
    safe = _q(disposable * SAFETY_MARGIN)
    feasible = monthly_needed <= safe
    shortfall = _q(max(_ZERO, monthly_needed - safe))

    # Deterministic alternatives so the LLM offers real numbers, never invented:
    #   - the shortest timeline whose monthly_needed fits the safe ceiling
    #   - the largest amount affordable within the requested timeline
    if safe > _ZERO:
        whole, remainder = divmod(desired, safe)
        min_timeline: Optional[int] = max(1, int(whole) + (1 if remainder > _ZERO else 0))
        max_amount: Optional[Decimal] = _q(safe * Decimal(months))
    else:
        # Already committed at/above income — no positive disposable to save
        # from, so no finite timeline makes it feasible.
        min_timeline = None
        max_amount = _ZERO

    return AffordabilityResult(
        feasible=feasible,
        currency=currency,
        desired_amount=desired,
        timeline_months=months,
        monthly_income=_q(Decimal(monthly_income)),
        monthly_fixed_expenses=fixed,
        monthly_debt_payments=commitments,
        monthly_disposable=disposable,
        safe_monthly_disposable=safe,
        monthly_needed=monthly_needed,
        shortfall=shortfall,
        min_timeline_months_feasible=min_timeline,
        max_amount_feasible_in_timeline=max_amount,
        notes=tuple(notes),
    )


async def gather_affordability_inputs(
    db: AsyncSession, *, user: User
) -> AffordabilityInputs:
    """Pull the real income / fixed-expense / debt-commitment figures for a
    user, each normalized to a monthly amount in the user's currency."""
    currency = user.currency or "CRC"
    income = await _monthly_income(db, user=user)

    bill_rows = await db.execute(
        select(
            RecurringBill.amount_expected,
            RecurringBill.currency,
            RecurringBill.frequency,
            RecurringBill.is_variable_amount,
        ).where(
            RecurringBill.user_id == user.id,
            RecurringBill.is_active.is_(True),
        )
    )
    fixed = _ZERO
    excluded_variable = 0
    excluded_custom = 0
    for amount, bill_currency, frequency, is_variable in bill_rows.all():
        if is_variable or amount is None:
            excluded_variable += 1
            continue
        factor = _BILL_FREQ_TO_MONTHLY.get(frequency)
        if factor is None:  # 'custom'/RRULE — can't reduce to a monthly figure
            excluded_custom += 1
            continue
        fixed += convert(Decimal(amount) * factor, bill_currency, currency)

    debt_rows = await db.execute(
        select(Debt.minimum_payment, Debt.currency).where(
            Debt.user_id == user.id,
            Debt.is_active.is_(True),
        )
    )
    commitments = _ZERO
    for payment, debt_currency in debt_rows.all():
        if payment is None:
            continue
        amount = Decimal(payment)
        if amount > _ZERO:
            commitments += convert(amount, debt_currency, currency)

    return AffordabilityInputs(
        currency=currency,
        monthly_income=income,
        monthly_fixed_expenses=_q(fixed),
        monthly_debt_payments=_q(commitments),
        excluded_variable_bills=excluded_variable,
        excluded_custom_bills=excluded_custom,
    )


async def assess_for_user(
    db: AsyncSession,
    *,
    user: User,
    desired_amount: Decimal,
    timeline_months: int = 1,
) -> AffordabilityResult:
    """Convenience: gather the user's real inputs, then run the pure engine."""
    inputs = await gather_affordability_inputs(db, user=user)
    return assess_affordability(
        monthly_income=inputs.monthly_income,
        monthly_fixed_expenses=inputs.monthly_fixed_expenses,
        monthly_debt_payments=inputs.monthly_debt_payments,
        desired_amount=desired_amount,
        timeline_months=timeline_months,
        currency=inputs.currency,
        excluded_variable_bills=inputs.excluded_variable_bills,
        excluded_custom_bills=inputs.excluded_custom_bills,
    )

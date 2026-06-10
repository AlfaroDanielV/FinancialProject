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
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.debt import Debt
from ...models.recurring_bill import RecurringBill
from ...models.user import User
from ..envelopes import _monthly_income, compute_envelope_summary
from ..fx import convert
from ..recurrence import get_upcoming_feed

if TYPE_CHECKING:  # avoid a circular import — cashflow.py imports from this module
    from .cashflow import MonthlyCashflow

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
    feasible: Optional[bool]  # None when GATED (no_income / no_budget / under_coverage)
    gate_reason: Optional[str]  # which gate suppressed the verdict, else None
    currency: str
    desired_amount: Decimal
    timeline_months: int
    monthly_needed: Decimal
    # Cashflow snapshot (the single source of truth) — transparency.
    monthly_income: Optional[Decimal]  # None when no income on file
    monthly_fixed_expenses: Decimal
    monthly_debt_payments: Decimal
    envelope_allocations: Decimal
    committed_outflows: Decimal  # Model A: == envelope_allocations
    savings_allocations: Decimal  # redirectable accumulation (messaging nuance)
    surplus: Optional[Decimal]  # income − committed; None when income unknown
    safe_surplus: Optional[Decimal]  # 0.80 × surplus; None when gated
    # Verdict extras — None when gated.
    shortfall: Optional[Decimal]  # vs the safe ceiling; 0 when feasible
    min_timeline_months_feasible: Optional[int]
    max_amount_feasible_in_timeline: Optional[Decimal]
    notes: tuple[str, ...] = ()


# Canned voseo guidance per gate — each maps to a DISTINCT user action and the
# copy must keep them distinct (register income vs build budget vs cover
# obligations). The full surfacing rules live in the tool descriptions + the
# write-path messaging; these are the engine-level notes.
_GATE_NOTES = {
    "no_income": (
        "No hay ingresos recurrentes registrados; no puedo estimar cuánto te "
        "sobra. Registrá tu ingreso y te doy números reales."
    ),
    "no_budget": (
        "Todavía no tenés sobres (presupuesto) configurados, así que no puedo "
        "estimar con confianza cuánto te queda libre. Armá tus sobres y con eso "
        "te doy un número real."
    ),
    "under_coverage": (
        "Tus sobres todavía no cubren tus deudas y gastos fijos registrados, así "
        "que el sobrante real puede ser menor de lo que parece. Ajustá tus sobres "
        "para cubrirlos y el cálculo será confiable."
    ),
}


def gate_guidance(gate_reason: Optional[str]) -> str:
    """Canned voseo guidance for a cashflow gate (empty string when ungated).
    Shared by every surface so the three gates keep their distinct, deterministic
    copy ([[Decision - Unified Monthly Cashflow]])."""
    return _GATE_NOTES.get(gate_reason or "", "")


def assess_affordability(
    cashflow: "MonthlyCashflow",
    *,
    desired_amount: Decimal,
    timeline_months: int = 1,
) -> AffordabilityResult:
    """Pure deterministic affordability math over the unified cashflow. No DB,
    no LLM.

    The verdict is judged against the envelope-aware ``surplus`` (income −
    committed envelopes) with the 80% safety margin — NOT the old
    income−fixed−debt disposable ([[Decision - Unified Monthly Cashflow]]). When
    the cashflow is gated (no income / no budget / budget under-covers
    obligations) the surplus isn't trustworthy, so ``feasible`` is ``None`` with
    ``gate_reason`` driving the canned guidance — honest, never fabricated.

    ``timeline_months=1`` models an immediate purchase; a larger horizon models
    saving toward a target. The single ``monthly_needed ≤ safe_surplus`` test
    covers both.
    """
    months = max(1, int(timeline_months))
    desired = _q(Decimal(desired_amount))
    monthly_needed = _q(desired / Decimal(months))

    notes: list[str] = []
    if cashflow.excluded_variable_bills:
        notes.append(
            f"{cashflow.excluded_variable_bills} gasto(s) recurrente(s) de monto "
            "variable no se incluyeron en los gastos fijos."
        )
    if cashflow.excluded_custom_bills:
        notes.append(
            f"{cashflow.excluded_custom_bills} gasto(s) recurrente(s) con regla "
            "personalizada no se pudieron convertir a un monto mensual."
        )

    common = dict(
        gate_reason=cashflow.gate_reason,
        currency=cashflow.currency,
        desired_amount=desired,
        timeline_months=months,
        monthly_needed=monthly_needed,
        monthly_income=(cashflow.monthly_income if cashflow.income_known else None),
        monthly_fixed_expenses=cashflow.recurring_bills,
        monthly_debt_payments=cashflow.debt_payments,
        envelope_allocations=cashflow.envelope_allocations,
        committed_outflows=cashflow.committed_outflows,
        savings_allocations=cashflow.savings_allocations,
        surplus=(cashflow.surplus if cashflow.income_known else None),
    )

    # Gated → no trustworthy surplus → withhold the verdict. The gate_reason
    # carries the canned guidance; the prose layer shows the transparency
    # breakdown but never a confident "te sobra / sí podés" claim.
    if not cashflow.reliable:
        notes.append(_GATE_NOTES.get(cashflow.gate_reason or "", ""))
        return AffordabilityResult(
            feasible=None,
            safe_surplus=None,
            shortfall=None,
            min_timeline_months_feasible=None,
            max_amount_feasible_in_timeline=None,
            notes=tuple(n for n in notes if n),
            **common,
        )

    # Reliable → surplus is a real Decimal. 80% safety margin retained (sub-
    # decision A: KEEP, now applied to surplus instead of income−fixed−debt).
    safe = _q(cashflow.surplus * SAFETY_MARGIN)
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
        # Everything committed at/above income — no positive surplus to save
        # from, so no finite timeline makes it feasible.
        min_timeline = None
        max_amount = _ZERO

    return AffordabilityResult(
        feasible=feasible,
        safe_surplus=safe,
        shortfall=shortfall,
        min_timeline_months_feasible=min_timeline,
        max_amount_feasible_in_timeline=max_amount,
        notes=tuple(n for n in notes if n),
        **common,
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
    """Convenience: gather the user's unified cashflow, then run the pure engine
    against the envelope-aware surplus."""
    # Lazy import: cashflow.py imports gather_affordability_inputs from here.
    from .cashflow import compute_monthly_cashflow

    cashflow = await compute_monthly_cashflow(db, user=user)
    return assess_affordability(
        cashflow,
        desired_amount=desired_amount,
        timeline_months=timeline_months,
    )


# ── Phase 7a: context signals (envelope execution + upcoming obligations) ──────
#
# These DO NOT change the affordability verdict. The headline `feasible` stays
# `monthly_needed <= 0.80 * (income - fixed - debt)` — auditable and stable. The
# signals below are extra deterministic context the LLM weaves into its honest
# explanation ("estructuralmente te alcanza, pero ya gastaste 90% de Gustos y
# viene el seguro el 15/jul"). They are NEVER folded into the disposable math:
# the monthly-fixed figure already amortizes recurring bills, so subtracting the
# upcoming lumps too would double-count.


@dataclass(frozen=True)
class OverLimitEnvelope:
    name: str
    overage: Decimal  # spent − limit, in the envelope's currency
    currency: str


@dataclass(frozen=True)
class EnvelopePressure:
    currency: str  # the user's summary currency
    total_limit: Decimal
    total_spent: Decimal
    total_remaining: Decimal
    pct_consumed: Optional[int]  # spent/limit %, None when no caps set
    over_limit: tuple[OverLimitEnvelope, ...]


@dataclass(frozen=True)
class UpcomingObligation:
    title: str
    due_date: str  # ISO date
    amount: Optional[Decimal]  # converted to the user currency; None if variable
    currency: str  # the user currency the amount is expressed in
    is_overdue: bool
    kind: str  # "bill" | "event"


@dataclass(frozen=True)
class FinancialContext:
    currency: str
    envelope_pressure: Optional[EnvelopePressure]
    upcoming_obligations: tuple[UpcomingObligation, ...]
    upcoming_total: Decimal  # Σ of the known amounts, user currency


# Cap the obligations list so the tool payload (and the LLM's attention) stays
# focused on the soonest, most material items.
_MAX_UPCOMING = 8


async def gather_financial_context(
    db: AsyncSession,
    *,
    user: User,
    today: Optional[date] = None,
    horizon_days: int = 60,
) -> FinancialContext:
    """Deterministic context for the pushback surfaces: how the envelopes are
    executing this month + which recurring bills / events are coming up. Reuses
    the live envelope summary (so it can't drift from the home-tab bars) and the
    Phase 4 upcoming feed."""
    currency = user.currency or "CRC"
    anchor = today or date.today()

    # ── envelope execution (reuses compute_envelope_summary — no drift) ──────
    summary = await compute_envelope_summary(db, user=user)
    pressure: Optional[EnvelopePressure] = None
    if summary.envelopes:
        total_limit = Decimal(str(summary.total_limit))
        total_spent = sum(
            (Decimal(str(c.spent_total)) for c in summary.by_class), Decimal("0")
        )
        over = tuple(
            OverLimitEnvelope(
                name=item.name,
                overage=_q(Decimal(str(item.spent)) - Decimal(str(item.limit_amount))),
                currency=item.currency,
            )
            for item in summary.envelopes
            if item.over_limit
        )
        pct = (
            int((total_spent / total_limit * Decimal("100")).to_integral_value())
            if total_limit > _ZERO
            else None
        )
        pressure = EnvelopePressure(
            currency=currency,
            total_limit=_q(total_limit),
            total_spent=_q(total_spent),
            total_remaining=_q(total_limit - total_spent),
            pct_consumed=pct,
            over_limit=over,
        )

    # ── upcoming bills + events (Phase 4 feed) ───────────────────────────────
    entries = await get_upcoming_feed(
        db,
        user.id,
        from_date=anchor,
        to_date=anchor + timedelta(days=horizon_days),
        include_overdue=True,
    )
    entries.sort(key=lambda e: e.date)
    obligations: list[UpcomingObligation] = []
    upcoming_total = _ZERO
    for entry in entries[:_MAX_UPCOMING]:
        amount: Optional[Decimal] = None
        if entry.amount is not None:
            amount = _q(
                convert(Decimal(str(entry.amount)), entry.currency or currency, currency)
            )
            upcoming_total += amount
        obligations.append(
            UpcomingObligation(
                title=entry.title,
                due_date=entry.date.isoformat(),
                amount=amount,
                currency=currency,
                is_overdue=entry.is_overdue,
                kind=entry.item_type,
            )
        )

    return FinancialContext(
        currency=currency,
        envelope_pressure=pressure,
        upcoming_obligations=tuple(obligations),
        upcoming_total=_q(upcoming_total),
    )

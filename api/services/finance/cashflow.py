"""Unified monthly cashflow — THE single source of truth (Phase 7).

Every "cuánto me sobra / puedo comprar / cuánto ahorro para X" answer must flow
through this module. No other code path may compute surplus, affordability, or
savings independently (Decision - Unified Monthly Cashflow). The LLM never
computes these figures — it calls a tool that calls this and narrates the
result.

**Model A — envelopes are the complete budget.**
``committed_outflows = envelope_allocations``. Debt payments and recurring bills
are computed for transparency but NOT re-added: a fixed obligation is expected to
live inside an envelope (attached via ``RecurringBill/Debt.envelope_id``), and the
per-item ``under_coverage`` gate flags any obligation with no envelope. Envelope
allocations are roots-only, so nested children aren't double-counted.

**Gate conditions** withhold the trustworthy surplus/savings claims. When one
holds, the prose layer shows the transparency breakdown but NOT "te sobran ₡X" /
"ahorrás en N meses", because the underlying number isn't one we trust:

  - ``no_income``      — no recurring income on file; without income there is no
                         surplus to compute (mirrors the engine's ``feasible=None``).
  - ``no_budget``      — no active envelopes at all; committed would be 0 and
                         surplus would collapse to the old inflated income figure.
  - ``under_coverage`` — a registered debt/bill isn't covered by the budget, so
                         committed (= just the envelopes) understates reality and
                         surplus is inflated. (Aggregate baseline here; upgraded to
                         per-item by the attachment feature — Block 3.)

Currency is the user's (CRC default). All math is Decimal — never float.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User
from ..envelopes import compute_envelope_summary
from .affordability import gather_affordability_inputs

_CENTS = Decimal("0.01")
_ZERO = Decimal("0")


def _q(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class MonthlyCashflow:
    """The single, deterministic monthly cashflow picture for one user.

    All components are always populated for transparency, even when a gate is
    active — the gate only governs whether the prose layer surfaces a confident
    surplus/savings claim, never whether the numbers exist.
    """

    monthly_income: Decimal  # CRC; aguinaldo + salario escolar EXCLUDED
    debt_payments: Decimal  # active cuotas / month (transparency)
    recurring_bills: Decimal  # active fixed bills / month (transparency)
    envelope_allocations: Decimal  # active root envelope allocations / month
    committed_outflows: Decimal  # Model A: == envelope_allocations
    surplus: Decimal  # income − committed; negative = deficit
    has_budget: bool  # at least one active envelope exists
    covers_obligations: bool  # allocations ≥ debt_payments + recurring_bills
    income_known: bool  # a recurring income is on file
    # Transparency only (sub-decision B): Σ allocations of savings + investing
    # roots. Subtracts NOTHING from committed/surplus — the engine never decides
    # this money is grabbable. It powers the "podés reasignar" messaging nuance
    # when a verdict is `no` but the user has savings/investing envelopes.
    savings_allocations: Decimal = Decimal("0")
    # Recurring bills we couldn't price into recurring_bills, surfaced as notes.
    excluded_variable_bills: int = 0
    excluded_custom_bills: int = 0
    currency: str = "CRC"

    @property
    def reliable(self) -> bool:
        """True only when a confident surplus/savings claim may be surfaced.
        Every gate must pass: income is known, a budget exists, and it covers
        the registered fixed obligations."""
        return self.income_known and self.has_budget and self.covers_obligations

    @property
    def gate_reason(self) -> Optional[str]:
        """Which gate (if any) suppresses the confident claim. Precedence runs
        most-fundamental first: without income nothing downstream is trustworthy,
        then no budget, then a budget that under-covers obligations."""
        if not self.income_known:
            return "no_income"
        if not self.has_budget:
            return "no_budget"
        if not self.covers_obligations:
            return "under_coverage"
        return None

    @property
    def is_deficit(self) -> bool:
        """A reliable, negative surplus — honest pushback, not an error."""
        return self.reliable and self.surplus < _ZERO

    def months_to_goal(self, goal_amount: Decimal) -> Optional[int]:
        """Whole months to save ``goal_amount`` at this surplus.

        Pure math on ``surplus``: ``ceil(goal / surplus)`` when surplus > 0,
        else ``None`` (a deficit or zero margin never reaches the goal). An
        already-met goal (amount ≤ 0) returns 0.

        NOTE: the prose layer must still check ``reliable`` before surfacing
        this — a months figure off an inflated surplus is exactly the
        misleading claim the gate exists to suppress.
        """
        amount = Decimal(goal_amount)
        if amount <= _ZERO:
            return 0
        if self.surplus <= _ZERO:
            return None
        return int((amount / self.surplus).to_integral_value(rounding=ROUND_CEILING))


def build_monthly_cashflow(
    *,
    monthly_income: Optional[Decimal],
    debt_payments: Decimal,
    recurring_bills: Decimal,
    envelope_allocations: Decimal,
    has_budget: bool,
    savings_allocations: Decimal = _ZERO,
    excluded_variable_bills: int = 0,
    excluded_custom_bills: int = 0,
    currency: str = "CRC",
) -> MonthlyCashflow:
    """Pure deterministic assembly — no DB, no LLM. Applies the Model A
    summation and the gate evaluation. ``monthly_income=None`` means no income
    on file (honest unknown → ``income_known=False``, surfaced as the
    ``no_income`` gate)."""
    income_known = monthly_income is not None
    income = _q(Decimal(monthly_income)) if income_known else _ZERO
    debt = _q(Decimal(debt_payments))
    bills = _q(Decimal(recurring_bills))
    allocations = _q(Decimal(envelope_allocations))

    # Model A: committed = envelopes only. Bills + debt stay transparency-only;
    # re-adding them would double-count obligations attached inside an envelope.
    # The double-count guard is structural — there is no `+ debt + bills` here.
    committed = allocations
    surplus = _q(income - committed)

    # Aggregate under_coverage gate: a budget that doesn't cover the registered
    # fixed obligations understates committed → surplus inflated → untrusted.
    # (Block 3 upgrades this to a per-item check off the attachment FK.)
    covers_obligations = allocations >= (debt + bills)

    return MonthlyCashflow(
        monthly_income=income,
        debt_payments=debt,
        recurring_bills=bills,
        envelope_allocations=allocations,
        committed_outflows=committed,
        surplus=surplus,
        has_budget=has_budget,
        covers_obligations=covers_obligations,
        income_known=income_known,
        savings_allocations=_q(Decimal(savings_allocations)),
        excluded_variable_bills=excluded_variable_bills,
        excluded_custom_bills=excluded_custom_bills,
        currency=currency,
    )


async def compute_monthly_cashflow(db: AsyncSession, *, user: User) -> MonthlyCashflow:
    """THE single source of truth. Gathers the four components, then runs the
    pure builder.

    Income / debt / bills reuse ``gather_affordability_inputs`` (so income
    already excludes aguinaldo + salario escolar and the affordability answer
    can't drift from this one). Envelope allocations reuse the live envelope
    summary's ``total_limit`` — roots-only and FX-converted to the user
    currency, so nested children aren't double-counted and the figure can't
    drift from the home-tab bars.
    """
    inputs = await gather_affordability_inputs(db, user=user)
    summary = await compute_envelope_summary(db, user=user)
    envelope_allocations = Decimal(str(summary.total_limit))
    # Roots-only, FX-converted per-class limits (by_class is already roots-only).
    # savings + investing are the "redirectable accumulation" classes — carried
    # for the messaging nuance, never subtracted from committed (sub-decision B).
    savings_allocations = sum(
        (
            Decimal(str(sub.limit_total))
            for sub in summary.by_class
            if sub.envelope_class in ("savings", "investing")
        ),
        Decimal("0"),
    )
    has_budget = bool(summary.envelopes)
    return build_monthly_cashflow(
        monthly_income=inputs.monthly_income,
        debt_payments=inputs.monthly_debt_payments,
        recurring_bills=inputs.monthly_fixed_expenses,
        envelope_allocations=envelope_allocations,
        has_budget=has_budget,
        savings_allocations=savings_allocations,
        excluded_variable_bills=inputs.excluded_variable_bills,
        excluded_custom_bills=inputs.excluded_custom_bills,
        currency=inputs.currency,
    )

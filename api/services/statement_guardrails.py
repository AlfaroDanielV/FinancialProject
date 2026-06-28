"""Deterministic guardrails for a reconciled LOAN balance.

A bank statement prints several numbers for a loan — the original/financed
amount, the current outstanding principal, and a "total a pagar" that bakes in
future interest. The extractor tags them with roles, but a mistag (or a missing
`principal_outstanding` tag) can anchor the wrong, much larger number onto
`Debt.current_balance` (the operator's "massive amount of debt" bug). These pure
checks catch an implausible loan balance by comparing it against ground truth we
already hold on the registered `Debt`: its original amount, its last known
balance, and its amortization schedule.

The checks FLAG (never hard-block) — the user always confirms with the evidence
(confirm-with-evidence, operator decision). The SAME function runs twice: in
`build_reconcile_plan` to default the row OFF + explain in the UI, and again in
`reconcile_products` as the server-side gate (so a direct/garbage write can't
bypass the client). Numbers only here; the verification-pass cross-check
(role-vs-judgment) lives in `statement_normalize`.

"LLM extracts; rules decide": the LLM never decides a loan balance is wrong —
this deterministic code does.

Pure: no LLM, no DB, no network (the Debt's fields are passed in).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from .amortization import generate_schedule

_CENT = Decimal("0.01")
# A loan can't owe more PRINCIPAL than was borrowed (capitalization aside); the
# small margin tolerates rounding / a capitalized-interest edge case.
_ORIGINAL_MARGIN = Decimal("1.02")
# A loan amortizes DOWN — a reconciled balance shouldn't jump materially above
# the last known balance (a re-draw is possible → flag, don't block).
_JUMP_MARGIN = Decimal("1.25")
# How far above the amortization-expected outstanding before we flag. WIDE on
# purpose: start_date / the cuota may be imprecise, so this only catches
# egregious (multiples-of) errors, not small drift. Only the HIGH side is
# flagged — a balance BELOW schedule just means the user paid extra.
_AMORT_HIGH_MARGIN = Decimal("1.40")


@dataclass(frozen=True)
class LoanBalanceVerdict:
    flagged: bool
    # loan_exceeds_original | loan_amortization_high | loan_balance_jumped
    reason: Optional[str] = None
    expected_outstanding: Optional[Decimal] = None


def _expected_outstanding_at(
    *,
    original_amount: Optional[Decimal],
    annual_rate: Optional[Decimal],
    cuota: Optional[Decimal],
    due_day: int,
    start_date: Optional[date],
    corte_date: Optional[date],
) -> Optional[Decimal]:
    """Amortization-expected remaining principal at the corte date, or None when
    the inputs are missing or the schedule can't be built (never-payoff, etc.).
    Reconstructs the schedule from origination (original_amount at the cuota) and
    reads the remaining balance after the elapsed number of payments."""
    if (
        original_amount is None
        or original_amount <= 0
        or annual_rate is None
        or cuota is None
        or cuota <= 0
        or start_date is None
        or corte_date is None
        or corte_date < start_date
    ):
        return None
    sched = generate_schedule(
        float(original_amount),
        float(annual_rate),
        float(cuota),
        due_day or 1,
        start_date=start_date,
    )
    if not sched.rows:
        return None
    months_elapsed = (corte_date.year - start_date.year) * 12 + (
        corte_date.month - start_date.month
    )
    if months_elapsed <= 0:
        return original_amount.quantize(_CENT)
    idx = min(months_elapsed, len(sched.rows))
    return Decimal(str(sched.rows[idx - 1].remaining_balance)).quantize(_CENT)


def evaluate_loan_balance(
    *,
    new_balance: Decimal,
    original_amount: Optional[Decimal],
    prior_balance: Optional[Decimal],
    annual_rate: Optional[Decimal] = None,
    cuota: Optional[Decimal] = None,
    due_day: int = 1,
    start_date: Optional[date] = None,
    corte_date: Optional[date] = None,
) -> LoanBalanceVerdict:
    """Flag (never hard-block) a loan balance that looks like the wrong, larger
    printed figure. Returns the first matching reason + the amortization-expected
    outstanding (for the UI), or `flagged=False` when the balance is plausible."""
    expected = _expected_outstanding_at(
        original_amount=original_amount,
        annual_rate=annual_rate,
        cuota=cuota,
        due_day=due_day,
        start_date=start_date,
        corte_date=corte_date,
    )

    # 1) Above the original/financed amount — the clearest "anchored the
    #    original/total" tell.
    if (
        original_amount is not None
        and original_amount > 0
        and new_balance > original_amount * _ORIGINAL_MARGIN
    ):
        return LoanBalanceVerdict(True, "loan_exceeds_original", expected)
    # 2) Far above the amortization schedule (catches a "total a pagar" still
    #    below the original on a long loan).
    if expected is not None and expected > 0 and new_balance > expected * _AMORT_HIGH_MARGIN:
        return LoanBalanceVerdict(True, "loan_amortization_high", expected)
    # 3) Jumped up vs the last known balance (loans amortize down).
    if (
        prior_balance is not None
        and prior_balance > 0
        and new_balance > prior_balance * _JUMP_MARGIN
    ):
        return LoanBalanceVerdict(True, "loan_balance_jumped", expected)
    return LoanBalanceVerdict(False, None, expected)


def verdict_for_debt(
    *, new_balance: Decimal, debt, corte_date: Optional[date]
) -> LoanBalanceVerdict:
    """Convenience over `evaluate_loan_balance` that reads the fields off a
    `Debt` ORM row (used by both the plan builder and the writer so the gate is
    identical on both sides)."""

    def _dec(v) -> Optional[Decimal]:
        return None if v is None else Decimal(str(v))

    return evaluate_loan_balance(
        new_balance=new_balance,
        original_amount=_dec(getattr(debt, "original_amount", None)),
        prior_balance=_dec(getattr(debt, "current_balance", None)),
        annual_rate=_dec(getattr(debt, "interest_rate", None)),
        cuota=_dec(getattr(debt, "minimum_payment", None)),
        due_day=getattr(debt, "payment_due_day", 1) or 1,
        start_date=getattr(debt, "start_date", None),
        corte_date=corte_date,
    )

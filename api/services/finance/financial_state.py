"""`financial_state` — the deterministic behavioral-finance label. ONE owner.

P10 B1 (prerequisite for the advisory layer + principle library). The label
lives HERE, in the P7 finance layer, next to ``cashflow.gate_reason`` — the
existing deterministic-label precedent. The principle matcher (P10 B8)
*consumes* these labels and never computes them (D8); the money archetype only
re-ranks already-matched principles and never selects (D9).

**The enum is FROZEN** — principles are tagged against these exact strings.
Adding a member is additive (new principles may target it); renaming or
removing one breaks every tagged principle and requires a decision note.

Classification composes EXISTING engine outputs (``compute_monthly_cashflow``,
``compute_net_worth``, ``compute_envelope_summary``, stored debt/card fields).
It re-derives nothing — surplus, DTI inputs, balances all come from their
single owners.

Precedence (worst-first, deterministic — the matcher gets the DOMINANT state):

1. ``negative_surplus``       — income + budget known, and committed outflows
                                exceed income. A deficit dominates everything.
2. ``irregular_income_stress`` — no registered income: nothing downstream is
                                trustworthy (mirrors the ``no_income`` gate).
3. ``high_interest_debt``     — expensive debt: any active debt at APR ≥ 20%,
                                a credit card carrying a balance, or DTI ≥ 0.40
                                (the existing insights-lifecycle threshold).
4. ``recent_overspend``       — at least one of the user's own envelopes is
                                over its limit this month (live, no snapshot).
5. ``first_time_saving``      — nothing set aside yet: zero savings/investing
                                allocations AND no positive net worth built.
6. ``stable_building``        — everything else: the healthy default.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.debt import Debt
from ...models.user import User
from ..credit_cards import list_active_cards_with_terms
from ..envelopes import compute_envelope_summary
from .cashflow import MonthlyCashflow, compute_monthly_cashflow
from .net_worth import compute_net_worth

_ZERO = Decimal("0")

# Any active debt at or above this annual rate (0–1 fraction) is "expensive"
# in the CR context (cards run 0.30–0.50; personal loans 0.15–0.25).
HIGH_INTEREST_APR = Decimal("0.20")

# Debt-service load that reads as over-indebted regardless of rate. Mirrors
# the existing threshold in api/services/insights/lifecycle.py (DTI 0.40).
HIGH_DTI = Decimal("0.40")


class FinancialState(str, Enum):
    """FROZEN label set — see module docstring before touching."""

    NEGATIVE_SURPLUS = "negative_surplus"
    HIGH_INTEREST_DEBT = "high_interest_debt"
    IRREGULAR_INCOME_STRESS = "irregular_income_stress"
    RECENT_OVERSPEND = "recent_overspend"
    FIRST_TIME_SAVING = "first_time_saving"
    STABLE_BUILDING = "stable_building"


# The values principles may be tagged against (import-stable convenience).
ALL_FINANCIAL_STATES: frozenset[str] = frozenset(s.value for s in FinancialState)


def classify_financial_state(
    cashflow: MonthlyCashflow,
    *,
    net_worth: Optional[Decimal] = None,
    dti: Optional[Decimal] = None,
    max_debt_interest_rate: Optional[Decimal] = None,
    card_carries_balance: bool = False,
    over_limit_envelopes: int = 0,
) -> FinancialState:
    """Pure classifier — no LLM, no DB, no network. See module precedence.

    ``net_worth``/``dti`` are the user-display-currency figures from their
    single owners (``compute_net_worth`` / income+debt from the cashflow);
    None means unknown and degrades gracefully (plan §2 / O1 spirit).
    """
    # 1 — deficit dominates. Deliberately income_known+has_budget (NOT the full
    # `reliable`): an unattached obligation only understates committed, so a
    # deficit that already shows through is still a deficit.
    if cashflow.income_known and cashflow.has_budget and cashflow.surplus < _ZERO:
        return FinancialState.NEGATIVE_SURPLUS

    # 2 — no registered income (the no_income gate, worn as a behavioral state).
    if not cashflow.income_known:
        return FinancialState.IRREGULAR_INCOME_STRESS

    # 3 — expensive debt by rate, revolving balance, or load.
    if (
        (max_debt_interest_rate is not None and max_debt_interest_rate >= HIGH_INTEREST_APR)
        or card_carries_balance
        or (dti is not None and dti >= HIGH_DTI)
    ):
        return FinancialState.HIGH_INTEREST_DEBT

    # 4 — an own envelope blew its cap this month.
    if over_limit_envelopes > 0:
        return FinancialState.RECENT_OVERSPEND

    # 5 — nothing set aside yet and nothing built yet.
    if cashflow.savings_allocations == _ZERO and (
        net_worth is None or net_worth <= _ZERO
    ):
        return FinancialState.FIRST_TIME_SAVING

    # 6 — the healthy default.
    return FinancialState.STABLE_BUILDING


@dataclass(frozen=True)
class FinancialStateReading:
    """The label plus the JSON-serializable signals that produced it — the
    advice-trace payload (D11: every framing traces to a deterministic
    finding)."""

    state: FinancialState
    signals: dict[str, Any]


async def classify_for_user(db: AsyncSession, *, user: User) -> FinancialStateReading:
    """Compose the existing engines into one labeled reading.

    Reuses ``compute_monthly_cashflow`` (surplus/gates), ``compute_net_worth``
    (per-currency; the display-currency entry feeds the classifier),
    ``compute_envelope_summary`` (own-envelope over-limit count) and stored
    debt/card fields. Recomputes no financial figure.
    """
    cashflow = await compute_monthly_cashflow(db, user=user)

    net_reading = await compute_net_worth(db, user=user)
    primary = net_reading.primary
    net_worth = primary.net if primary is not None else None

    dti: Optional[Decimal] = None
    if cashflow.income_known and cashflow.monthly_income > _ZERO:
        dti = (cashflow.debt_payments / cashflow.monthly_income).quantize(
            Decimal("0.001")
        )

    max_rate_row = await db.execute(
        select(func.max(Debt.interest_rate)).where(
            Debt.user_id == user.id,
            Debt.is_active.is_(True),
            Debt.archived.is_(False),
        )
    )
    max_rate_val = max_rate_row.scalar()
    max_rate = Decimal(max_rate_val) if max_rate_val is not None else None

    cards = await list_active_cards_with_terms(db, user_id=user.id)
    card_carries_balance = any(card.balance_owed > _ZERO for card in cards)

    summary = await compute_envelope_summary(db, user=user)
    over_limit = sum(
        1 for e in summary.envelopes if not e.is_shared and e.over_limit
    )

    state = classify_financial_state(
        cashflow,
        net_worth=net_worth,
        dti=dti,
        max_debt_interest_rate=max_rate,
        card_carries_balance=card_carries_balance,
        over_limit_envelopes=over_limit,
    )
    signals: dict[str, Any] = {
        "surplus": str(cashflow.surplus),
        "gate_reason": cashflow.gate_reason,
        "income_known": cashflow.income_known,
        "has_budget": cashflow.has_budget,
        "savings_allocations": str(cashflow.savings_allocations),
        "net_worth": str(net_worth) if net_worth is not None else None,
        "net_worth_currency": net_reading.currency,
        "dti": str(dti) if dti is not None else None,
        "max_debt_interest_rate": str(max_rate) if max_rate is not None else None,
        "card_carries_balance": card_carries_balance,
        "over_limit_envelopes": over_limit,
    }
    return FinancialStateReading(state=state, signals=signals)

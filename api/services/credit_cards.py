"""Phase 7b — credit-card helpers (B4 analysis, B5 obligation integration).

The card's balance is ONLY ever read live from `compute_account_balances`;
this module derives everything else (minimum due, projections) from the pure
`app/domain/credit` engine. No stored balance, no LLM.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.credit import (
    compute_minimum,
    payment_for_months,
    project_fixed_payment,
    project_minimum_only,
)

from ..models.account import Account
from ..models.credit_card_terms import CreditCardTerms
from ..models.debt import Debt
from ..schemas.card_terms import CardAnalysisResponse, CardPaymentStrategy
from .accounts import compute_account_balances


@dataclass(frozen=True)
class CardWithTerms:
    account: Account
    terms: CreditCardTerms
    balance_owed: Decimal  # positive magnitude; 0 when nothing is owed
    minimum_due: Decimal

    @property
    def recurring_payment_due(self) -> Decimal:
        """The monthly card payment the upcoming-payment projection surfaces:
        the full live balance when the card is paid 'de contado'
        (`payment_mode == 'full'`), else the minimum. The minimum stays the
        must-pay floor that budget / affordability / envelope reservation use
        — `payment_mode` only changes what the reminder + agent report."""
        if self.terms.payment_mode == "full":
            return self.balance_owed
        return self.minimum_due


async def list_active_cards_with_terms(
    db: AsyncSession, *, user_id: uuid.UUID
) -> list[CardWithTerms]:
    """Every non-archived credit account that has terms, with its LIVE owed
    balance and the minimum the terms formula yields today. A paid-off card
    (owed = 0) carries minimum 0 and produces no obligation anywhere."""
    rows = (
        await db.execute(
            select(Account, CreditCardTerms)
            .join(CreditCardTerms, CreditCardTerms.account_id == Account.id)
            .where(
                Account.user_id == user_id,
                Account.account_type == "credit",
                Account.archived.is_(False),
            )
        )
    ).all()
    if not rows:
        return []

    balances = await compute_account_balances(
        db, user_id=user_id, account_ids=[acc.id for acc, _terms in rows]
    )
    out: list[CardWithTerms] = []
    for account, terms in rows:
        current = balances.get(account.id)
        owed = max(
            Decimal("0"), -(current.current if current else Decimal("0"))
        )
        minimum = compute_minimum(
            owed,
            minimum_pct=Decimal(str(terms.minimum_payment_pct)),
            minimum_floor=(
                Decimal(str(terms.minimum_payment_floor))
                if terms.minimum_payment_floor is not None
                else None
            ),
        )
        out.append(
            CardWithTerms(
                account=account,
                terms=terms,
                balance_owed=owed,
                minimum_due=minimum,
            )
        )
    return out


async def superseded_credit_card_debt_ids(
    db: AsyncSession, *, user_id: uuid.UUID
) -> set[uuid.UUID]:
    """Coexistence rule: a `Debt(debt_type='credit_card')` whose account_id
    points at an account WITH card terms is excluded from feed/gate/
    affordability — the account+terms representation wins (no double count).
    Unlinked legacy card-debts keep working unchanged."""
    rows = (
        await db.execute(
            select(Debt.id)
            .join(CreditCardTerms, CreditCardTerms.account_id == Debt.account_id)
            .where(
                Debt.user_id == user_id,
                Debt.debt_type == "credit_card",
                Debt.account_id.is_not(None),
            )
        )
    ).scalars()
    return set(rows)


def build_card_analysis(
    *, card: CardWithTerms, today: Optional[date] = None
) -> CardAnalysisResponse:
    """Deterministic minimum-payment analysis over the live balance."""
    terms = card.terms
    annual_rate = Decimal(str(terms.annual_interest_rate))
    minimum_pct = Decimal(str(terms.minimum_payment_pct))
    minimum_floor = (
        Decimal(str(terms.minimum_payment_floor))
        if terms.minimum_payment_floor is not None
        else None
    )
    owed = card.balance_owed
    limit = (
        Decimal(str(terms.credit_limit))
        if terms.credit_limit is not None
        else None
    )

    minimum_projection = project_minimum_only(
        owed,
        annual_rate=annual_rate,
        minimum_pct=minimum_pct,
        minimum_floor=minimum_floor,
    )

    strategies: list[CardPaymentStrategy] = []
    if owed > 0:
        baseline_interest = (
            minimum_projection.total_interest
            if not minimum_projection.never_pays_off
            else None
        )

        def _strategy(label: str, payment: Decimal) -> None:
            projection = project_fixed_payment(
                owed, annual_rate=annual_rate, payment=payment
            )
            saved = (
                baseline_interest - projection.total_interest
                if baseline_interest is not None
                and not projection.never_pays_off
                else None
            )
            strategies.append(
                CardPaymentStrategy(
                    label=label,
                    monthly_payment=payment,
                    months=projection.months,
                    total_interest=projection.total_interest,
                    interest_saved_vs_minimum=saved,
                )
            )

        if card.minimum_due > 0:
            _strategy(
                "Pagando el doble del mínimo", card.minimum_due * 2
            )
        _strategy(
            "Para pagarla en 12 meses",
            payment_for_months(owed, annual_rate=annual_rate, months=12),
        )

    return CardAnalysisResponse(
        account_id=card.account.id,
        currency=card.account.currency,
        as_of=today or date.today(),
        balance_owed=owed,
        credit_limit=limit,
        available_credit=(
            max(Decimal("0"), limit - owed) if limit is not None else None
        ),
        minimum_payment=card.minimum_due,
        monthly_interest_cost=(owed * annual_rate / 12).quantize(
            Decimal("0.01")
        ),
        months_to_payoff_minimum=minimum_projection.months,
        total_interest_minimum=(
            minimum_projection.total_interest
            if not minimum_projection.never_pays_off
            else None
        ),
        total_paid_minimum=(
            minimum_projection.total_paid
            if not minimum_projection.never_pays_off
            else None
        ),
        never_pays_off=minimum_projection.never_pays_off,
        strategies=strategies,
    )

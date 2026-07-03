"""Deterministic per-currency net worth — P10 B1/B4 single owner.

``net = assets − liabilities`` where:

- **assets** = balances of active, non-archived FUND accounts (checking /
  savings / investment), from the single balance invariant
  ``compute_account_balances`` (anchor-aware). A credit account that is
  PREPAID (positive balance) also counts as an asset.
- **liabilities** = what's owed on credit accounts (the positive magnitude of
  a negative credit balance — mirroring ``credit_cards.py``'s owed
  convention) + ``Debt.current_balance`` of active, non-archived debts.
  A legacy ``credit_card`` Debt linked to an account with terms is
  **superseded** (the card's live balance already carries it — never counted
  twice, same rule as affordability). A loan on cargo automático
  (``charge_to_account_id`` set) IS included — the cargo mechanism moves the
  cuota, not the balance.

**Per-currency, USD apart (D3):** entries are never FX-summed into a single
₡+$ figure on the fixed ₡500 placeholder. The user's display currency leads;
other currencies are reported apart.

This module composes existing engines and stored fields only — it computes no
new financial figure ("no recomputation", plan §2). The advisory layer and the
``get_net_worth`` query tool both consume it; neither re-derives.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.account import Account
from ...models.debt import Debt
from ...models.user import User
from ..accounts import compute_account_balances
from ..credit_cards import superseded_credit_card_debt_ids

_CENTS = Decimal("0.01")
_ZERO = Decimal("0")


def _q(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class CurrencyNetWorth:
    """Net worth reconciled within ONE currency."""

    currency: str
    assets: Decimal
    liabilities: Decimal
    net: Decimal
    accounts_counted: int
    debts_counted: int


@dataclass(frozen=True)
class NetWorthReading:
    """Per-currency net worth entries, display currency first."""

    currency: str  # the user's display currency (leads the narration)
    entries: tuple[CurrencyNetWorth, ...]

    def entry_for(self, currency: str) -> Optional[CurrencyNetWorth]:
        for entry in self.entries:
            if entry.currency == currency:
                return entry
        return None

    @property
    def primary(self) -> Optional[CurrencyNetWorth]:
        return self.entry_for(self.currency)


async def compute_net_worth(db: AsyncSession, *, user: User) -> NetWorthReading:
    display_currency = (
        getattr(user, "display_currency", None) or user.currency or "CRC"
    )

    rows = (
        await db.execute(
            select(Account.id, Account.currency, Account.account_type).where(
                Account.user_id == user.id,
                Account.is_active.is_(True),
                Account.archived.is_(False),
            )
        )
    ).all()

    assets: dict[str, Decimal] = {}
    liabilities: dict[str, Decimal] = {}
    accounts_counted: dict[str, int] = {}
    debts_counted: dict[str, int] = {}

    if rows:
        balances = await compute_account_balances(
            db, user_id=user.id, account_ids=[r.id for r in rows]
        )
        for r in rows:
            ccy = r.currency or "CRC"
            bal = balances[r.id].current if r.id in balances else _ZERO
            accounts_counted[ccy] = accounts_counted.get(ccy, 0) + 1
            if r.account_type == "credit":
                # Owed = the positive magnitude of a negative balance; a
                # prepaid card (positive balance) is an asset.
                if bal < _ZERO:
                    liabilities[ccy] = liabilities.get(ccy, _ZERO) + (-bal)
                elif bal > _ZERO:
                    assets[ccy] = assets.get(ccy, _ZERO) + bal
            else:
                assets[ccy] = assets.get(ccy, _ZERO) + bal

    superseded = await superseded_credit_card_debt_ids(db, user_id=user.id)
    debt_rows = (
        await db.execute(
            select(Debt.id, Debt.currency, Debt.current_balance).where(
                Debt.user_id == user.id,
                Debt.is_active.is_(True),
                Debt.archived.is_(False),
            )
        )
    ).all()
    for d in debt_rows:
        if d.id in superseded:
            continue  # the card's live balance already carries this debt
        ccy = d.currency or "CRC"
        liabilities[ccy] = liabilities.get(ccy, _ZERO) + Decimal(d.current_balance)
        debts_counted[ccy] = debts_counted.get(ccy, 0) + 1

    currencies = sorted(
        set(assets) | set(liabilities),
        key=lambda c: (c != display_currency, c),  # display currency first
    )
    entries = tuple(
        CurrencyNetWorth(
            currency=ccy,
            assets=_q(assets.get(ccy, _ZERO)),
            liabilities=_q(liabilities.get(ccy, _ZERO)),
            net=_q(assets.get(ccy, _ZERO) - liabilities.get(ccy, _ZERO)),
            accounts_counted=accounts_counted.get(ccy, 0),
            debts_counted=debts_counted.get(ccy, 0),
        )
        for ccy in currencies
    )
    return NetWorthReading(currency=display_currency, entries=entries)

"""Bank-reconciliation anchor operations (onboarding + re-anchor / heal-drift).

The real bank balance is the source of truth; transactions are explanatory.
Setting/correcting a balance APPENDS an `account_anchors` row (never mutates) and
— for a correction — records a labeled, INFORMATIONAL "ajuste de reconciliación"
transaction for the delta.

The ajuste is dated ON the anchor's `effective_date`, so the strict-`>` balance
formula in `compute_account_balances` EXCLUDES it from the balance (decision B1) —
the new anchor value is what drives the balance; the ajuste only explains the gap
in the ledger. It is ALSO excluded from income/expense reports (it is a balance
reconciliation, not P&L) via the `AJUSTE_CATEGORY` marker — see
`api/services/dashboard/summary.py`. No `is_summed` flag is needed; pure date
arithmetic + the category marker keep it visible-but-uncounted.

See docs/balance-anchor-reconciliation-decisions.md (Gate B / Block 6).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.account import Account
from ..models.account_anchor import AccountAnchor
from ..models.transaction import Transaction
from ..models.user import User
from .accounts import compute_account_balances

# Reserved category marker for the reconciliation adjustment row. Analytics
# (dashboard/category/cash-flow) filter it out via `category IS DISTINCT FROM`
# this value. NOT user-selectable copy — it is a sentinel. (A dedicated
# `transactions.is_adjustment` column is logged as future tech-debt.)
AJUSTE_CATEGORY = "ajuste de reconciliación"
AJUSTE_DESCRIPTION = "Ajuste de reconciliación de saldo"

# Anchor provenance. `migrated` = the 0035 backfill (unconfirmed real balance →
# the "confirmá tu saldo" nudge fires); `onboarding` = set at account creation;
# `reanchor` = a later correction; `statement` = reconciled from an uploaded bank
# statement PDF at the fecha de corte.
ANCHOR_SOURCES = ("onboarding", "reanchor", "migrated", "statement")


@dataclass(frozen=True)
class AnchorResult:
    anchor_id: uuid.UUID
    value: Decimal
    effective_date: date
    delta: Decimal
    ajuste_transaction_id: Optional[uuid.UUID]


async def latest_anchor_sources(
    db: AsyncSession, *, account_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Latest-anchor `source` per account (DISTINCT ON). Accounts with no anchor
    are absent. Drives the "confirmá tu saldo real" nudge: a `migrated` source
    (or no anchor) means the real balance is unconfirmed."""
    ids = list(account_ids)
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(AccountAnchor.account_id, AccountAnchor.source)
            .where(AccountAnchor.account_id.in_(ids))
            .order_by(
                AccountAnchor.account_id,
                AccountAnchor.effective_date.desc(),
                AccountAnchor.created_at.desc(),
            )
            .distinct(AccountAnchor.account_id)
        )
    ).all()
    return {account_id: source for account_id, source in rows}


def needs_balance_confirmation(latest_source: Optional[str]) -> bool:
    """True when the account carries the 0035 `migrated` backfill anchor and its
    real balance hasn't been confirmed yet → the "confirmá tu saldo real" nudge
    fires. A brand-new account (no anchor) is NOT nudged — entering the balance
    at creation IS the confirmation; it reconstructs from `initial_balance`
    until the user re-anchors. Once corrected (`reanchor`), the nudge stops."""
    return latest_source == "migrated"


async def apply_anchor(
    db: AsyncSession,
    *,
    user: User,
    account: Account,
    value: Decimal,
    source: str,
    note: Optional[str] = None,
    write_ajuste: bool = True,
    today: date | None = None,
) -> AnchorResult:
    """Append a reconciliation anchor stating `value` as the account's real
    balance as of `today`, and (when `write_ajuste`) record the labeled,
    informational ajuste for the delta vs the currently-projected balance.

    `write_ajuste=False` is for a brand-new account's first anchor (a starting
    point, not a correction). Append-only — never mutates an existing anchor.
    The caller commits.
    """
    today = today or date.today()
    value = Decimal(value)

    projected = Decimal("0")
    if write_ajuste:
        balances = await compute_account_balances(
            db, user_id=user.id, account_ids=[account.id]
        )
        bal = balances.get(account.id)
        projected = bal.current if bal else Decimal("0")
    delta = (value - projected).quantize(Decimal("0.01"))

    anchor = AccountAnchor(
        user_id=user.id,
        account_id=account.id,
        value=value,
        currency=account.currency,
        effective_date=today,
        source=source,
        note=note,
    )
    db.add(anchor)

    ajuste_id: Optional[uuid.UUID] = None
    if write_ajuste and delta != 0:
        ajuste = Transaction(
            user_id=user.id,
            account_id=account.id,
            amount=delta,
            currency=account.currency,
            merchant=None,
            description=AJUSTE_DESCRIPTION,
            category=AJUSTE_CATEGORY,
            # == effective_date → excluded from the balance by the strict `>`
            # in compute_account_balances, and from income/expense by the
            # AJUSTE_CATEGORY marker. Visible in Movimientos (styled distinctly).
            transaction_date=today,
            source="manual",
            status="confirmed",
        )
        db.add(ajuste)
        await db.flush()
        ajuste_id = ajuste.id
    else:
        await db.flush()

    return AnchorResult(
        anchor_id=anchor.id,
        value=value,
        effective_date=today,
        delta=delta,
        ajuste_transaction_id=ajuste_id,
    )

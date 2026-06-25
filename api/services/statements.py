"""Bank-statement reconciliation — apply a confirmed plan → set the real balance.

A statement states each product's balance at the fecha de corte. We trust it as
ground truth (same philosophy as the balance-anchor engine, 2026-06-19). The
extraction + the per-(account×leg) plan are built upstream by
`statement_normalize.build_reconcile_plan` (semantic primitives + policy table +
conservation); this module is the deterministic WRITER the plan collapses to:

- **deposit / investment** account → append a `source="statement"` anchor at the
  corte date with `value = closing_balance`. Every txn dated ≤ corte is absorbed
  into the stated balance (excluded by the strict `>` in
  `compute_account_balances`); post-corte activity rides on top. `write_ajuste=
  False` — the corte balance IS the truth.
- **credit** account → same anchor, but the card balance is stored NEGATIVE when
  owed (`owed = -balance`, see `credit_cards.py`), so the anchor value is
  `-closing_balance`.
- **loan / line_of_credit** → set `Debt.current_balance := closing_balance`. The
  ONE sanctioned write to an otherwise-immutable debt field (the PATCH whitelist
  forbids it); an audit note is appended.

Two cross-cutting guards (defense in depth — a stale chat payload or a native
override can hand a bad item straight to this writer, bypassing the plan builder):
- **currency guard:** reject a leg whose `currency` ≠ the target's.
- **identity self-stamp:** fill the target's NULL identity columns (IBAN /
  account_number / last4) from the item so the NEXT statement matches
  deterministically (fill-if-null, never clobber a user-set value).

"LLM extracts; rules decide": this deterministic path is the only writer. The
caller commits.
"""
from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.account import Account
from ..models.debt import Debt
from ..models.user import User
from ..schemas.statements import (
    StatementReconcileItem,
    StatementReconcileResultItem,
)
from .accounts import compute_account_balances
from .anchors import apply_anchor

_CENT = Decimal("0.01")


class ReconcileError(ValueError):
    """A reconcile item couldn't be applied (bad/foreign/archived target, or a
    currency mismatch). The router maps this to a 400 with the Spanish message."""


def _is_credit(account: Account) -> bool:
    return account.account_type == "credit"


def _guard_currency(item: StatementReconcileItem, target: Any, label: str) -> None:
    """Reject a leg whose currency disagrees with the target's (the silent
    USD-onto-CRC bug). No-op when the item omits currency (legacy payloads)."""
    if not item.currency:
        return
    target_cur = (getattr(target, "currency", None) or "CRC").upper()
    if item.currency.upper() != target_cur:
        raise ReconcileError(
            f"La moneda del producto ({item.currency.upper()}) no coincide con "
            f"«{label}» ({target_cur})."
        )


def _stamp_identity(item: StatementReconcileItem, target: Any) -> None:
    """Fill the target's NULL identity columns from the item (fill-if-null)."""
    for attr in ("iban", "account_number", "last4"):
        val = getattr(item, attr, None)
        if val and not getattr(target, attr, None):
            setattr(target, attr, re.sub(r"\s+", "", val) or None)


async def reconcile_products(
    db: AsyncSession,
    *,
    user: User,
    corte_date: date,
    items: list[StatementReconcileItem],
) -> list[StatementReconcileResultItem]:
    """Apply every reconcile item. Raises `ReconcileError` on a bad target. The
    caller commits."""
    account_items = [it for it in items if it.kind in ("deposit", "credit")]
    loan_items = [it for it in items if it.kind == "loan"]

    results: list[StatementReconcileResultItem] = []

    # ── deposit + credit accounts → anchor at corte ──────────────────────────
    acct_ids = [it.target_id for it in account_items]
    accounts: dict[uuid.UUID, Account] = {}
    old_balances = {}
    if acct_ids:
        rows = (
            await db.execute(
                select(Account).where(
                    Account.user_id == user.id, Account.id.in_(acct_ids)
                )
            )
        ).scalars()
        accounts = {a.id: a for a in rows}
        # Pre-reconcile snapshot for the user-facing antes→después delta.
        old_balances = await compute_account_balances(
            db, user_id=user.id, account_ids=acct_ids
        )

    for it in account_items:
        acct = accounts.get(it.target_id)
        if acct is None:
            raise ReconcileError("Una de las cuentas no es válida.")
        if acct.archived or not acct.is_active:
            raise ReconcileError(
                f"La cuenta «{acct.name}» está archivada; no se puede reconciliar."
            )
        if (it.kind == "credit") != _is_credit(acct):
            raise ReconcileError(
                f"El tipo del producto no coincide con la cuenta «{acct.name}»."
            )
        _guard_currency(it, acct, acct.name)
        _stamp_identity(it, acct)
        # Credit balances are stored NEGATIVE when owed (see credit_cards.py).
        value = -it.closing_balance if it.kind == "credit" else it.closing_balance
        res = await apply_anchor(
            db,
            user=user,
            account=acct,
            value=value,
            source="statement",
            note=f"Reconciliado con estado de cuenta al {corte_date.isoformat()}.",
            write_ajuste=False,
            today=corte_date,
        )
        old = old_balances.get(acct.id)
        old_current = old.current if old else Decimal("0")
        results.append(
            StatementReconcileResultItem(
                target_id=acct.id,
                kind=it.kind,
                name=acct.name,
                delta=(value - old_current).quantize(_CENT),
                anchor_id=res.anchor_id,
                new_balance=value,
                conservation_ok=it.conservation_ok,
                needs_review=it.needs_review,
            )
        )

    # ── loans → set Debt.current_balance (sanctioned immutability exception) ──
    if loan_items:
        debt_ids = [it.target_id for it in loan_items]
        rows = (
            await db.execute(
                select(Debt).where(
                    Debt.user_id == user.id, Debt.id.in_(debt_ids)
                )
            )
        ).scalars()
        debts = {d.id: d for d in rows}
        for it in loan_items:
            debt = debts.get(it.target_id)
            if debt is None:
                raise ReconcileError("Uno de los préstamos no es válido.")
            if debt.archived:
                raise ReconcileError(
                    f"El préstamo «{debt.name}» está archivado; no se puede "
                    "reconciliar."
                )
            _guard_currency(it, debt, debt.name)
            _stamp_identity(it, debt)
            old_bal = Decimal(debt.current_balance or 0)
            new_bal = it.closing_balance
            debt.current_balance = new_bal
            note = (
                f"Saldo reconciliado con estado de cuenta al "
                f"{corte_date.isoformat()}: ₡{old_bal} → ₡{new_bal}."
            )
            debt.notes = f"{debt.notes} {note}".strip() if debt.notes else note
            results.append(
                StatementReconcileResultItem(
                    target_id=debt.id,
                    kind="loan",
                    name=debt.name,
                    delta=(new_bal - old_bal).quantize(_CENT),
                    anchor_id=None,
                    new_balance=new_bal,
                    conservation_ok=it.conservation_ok,
                    needs_review=it.needs_review,
                )
            )

    return results

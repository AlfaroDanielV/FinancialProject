"""Bank-statement reconciliation — match products → set the real balance.

A statement states each product's balance at the fecha de corte. We trust it as
ground truth (same philosophy as the balance-anchor engine, 2026-06-19):

- **deposit** account → append a `source="statement"` anchor at the corte date
  with `value = closing_balance`. Every txn dated ≤ corte is absorbed into the
  stated balance (excluded by the strict `>` in `compute_account_balances`);
  post-corte activity rides on top. `write_ajuste=False` — the corte balance IS
  the truth, so there is no drift row to write at corte (a `S − balance_now`
  ajuste dated at corte would be a confusing wrong-looking line).
- **credit** account → same anchor, but the card balance is stored NEGATIVE when
  owed (`owed = -balance`, see `credit_cards.py`), so the anchor value is
  `-closing_balance`.
- **loan** → set `Debt.current_balance := closing_balance`. This is the ONE
  sanctioned write to an otherwise-immutable debt financial field (the PATCH
  whitelist forbids it); justified because the statement is authoritative. An
  audit note is appended; the amortization schedule stays representational.

"LLM extracts; rules decide": the LLM proposed the figures; this deterministic
path is the only writer. The caller commits.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.account import Account
from ..models.debt import Debt
from ..models.user import User
from ..schemas.statements import (
    StatementExtraction,
    StatementReconcileItem,
    StatementReconcileResultItem,
)
from .accounts import compute_account_balances
from .anchors import apply_anchor
from .dispatch.lazy_detection import match_account_hint

_CENT = Decimal("0.01")


class ReconcileError(ValueError):
    """A reconcile item couldn't be applied (bad/foreign/archived target). The
    router maps this to a 400 with the Spanish message."""


def _is_credit(account: Account) -> bool:
    return account.account_type == "credit"


async def suggest_targets(
    db: AsyncSession,
    *,
    user: User,
    extraction: StatementExtraction,
) -> StatementExtraction:
    """Fill each product's `suggested_account_id` / `suggested_debt_id` with a
    best-guess match against the user's records (the form/chat lets the user
    override). Deterministic — never an LLM decision. Mutates + returns the
    extraction."""
    accounts = list(
        (
            await db.execute(
                select(Account).where(
                    Account.user_id == user.id,
                    Account.is_active.is_(True),
                    Account.archived.is_(False),
                )
            )
        ).scalars()
    )
    debts = list(
        (
            await db.execute(
                select(Debt).where(
                    Debt.user_id == user.id,
                    Debt.is_active.is_(True),
                    Debt.archived.is_(False),
                )
            )
        ).scalars()
    )

    for product in extraction.products:
        if product.kind == "loan":
            cands = [d for d in debts if d.currency == product.currency]
            if len(cands) == 1:
                product.suggested_debt_id = cands[0].id
            else:
                hint = " ".join(
                    p for p in (extraction.bank, product.label) if p
                )
                match = _match_debt(hint, cands)
                if match is not None:
                    product.suggested_debt_id = match.id
            continue

        want_credit = product.kind == "credit"
        cands = [
            a
            for a in accounts
            if _is_credit(a) == want_credit and a.currency == product.currency
        ]
        if len(cands) == 1:
            product.suggested_account_id = cands[0].id
            continue
        hint = " ".join(p for p in (extraction.bank, product.label) if p)
        result = match_account_hint(hint or None, cands)
        if result.status == "matched" and result.account is not None:
            product.suggested_account_id = result.account.id

    return extraction


def _match_debt(hint: str, debts: list[Debt]) -> Debt | None:
    """Light fuzzy match of a statement hint to a Debt by name/lender. Reuses the
    account matcher by treating the debt name as the candidate name."""
    if not hint.strip() or not debts:
        return None
    # match_account_hint matches on `.name`; both Debt and Account expose it.
    result = match_account_hint(hint, debts)  # type: ignore[arg-type]
    if result.status == "matched":
        return result.account  # type: ignore[return-value]
    return None


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
                )
            )

    return results

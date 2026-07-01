"""Envelope budgeting spend computation.

Spend is computed live from transactions — there is no stored running balance,
so the bars can never drift from the ledger. One grouped query per summary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.bill_occurrence import BillOccurrence
from ..models.debt import Debt, DebtPayment
from ..models.envelope import Envelope
from ..models.recurring_bill import RecurringBill
from ..models.recurring_income import RecurringIncome
from ..models.transaction import Transaction
from ..models.user import User
from ..schemas.envelopes import (
    EnvelopeClassSubtotal,
    EnvelopeSummaryItem,
    EnvelopeSummaryResponse,
)
from .clock import user_today
from .fx import convert
from .income_frequency import PAYMENTS_PER_MONTH

_CLASS_ORDER = ["needs", "wants", "savings", "investing"]

# Normalize a recurring-income cadence to a monthly figure. A row stores the
# per-payment amount, so monthly = per_payment × payments-per-month. Single
# source: api/services/income_frequency.py (CR quincenal = 2/month).
_FREQ_TO_MONTHLY = PAYMENTS_PER_MONTH


def _user_today(user: User) -> date:
    # Thin alias over the shared single source (clock.user_today).
    return user_today(user)


def _next_month_start(value: date) -> date:
    year = value.year + 1 if value.month == 12 else value.year
    month = 1 if value.month == 12 else value.month + 1
    return date(year, month, 1)


async def _monthly_income(db: AsyncSession, *, user: User) -> Decimal | None:
    """Best-effort monthly income from active recurring incomes in the user's
    currency, each normalized to a monthly figure. None if the user has none."""
    currency = user.currency or "CRC"
    rows = await db.execute(
        select(RecurringIncome.amount, RecurringIncome.frequency).where(
            RecurringIncome.user_id == user.id,
            RecurringIncome.is_active.is_(True),
            RecurringIncome.archived.is_(False),
            RecurringIncome.currency == currency,
            RecurringIncome.amount.isnot(None),
            # Aguinaldo + salario escolar are CR lump cycles paid in Dec/Jan.
            # Amortizing them into monthly income (annual ÷ 12) is phantom cash
            # that inflates surplus AND affordability — the money isn't in hand
            # month to month. They're earmarked lump events, not monthly
            # cashflow. Single source: this exclusion flows to the envelope
            # summary, the affordability engine, and compute_monthly_cashflow.
            RecurringIncome.income_type.notin_(("aguinaldo", "salario_escolar")),
        )
    )
    total = Decimal("0")
    found = False
    for amount, frequency in rows.all():
        factor = _FREQ_TO_MONTHLY.get(frequency)
        if factor is None or amount is None:
            continue
        total += Decimal(amount) * factor
        found = True
    return total.quantize(Decimal("0.01")) if found else None


# ── nesting helpers (Phase 7a) ────────────────────────────────────────────────


async def fetch_envelopes(
    db: AsyncSession, *, user_id: uuid.UUID, include_archived: bool = False
) -> list[Envelope]:
    stmt = select(Envelope).where(Envelope.user_id == user_id)
    if not include_archived:
        stmt = stmt.where(Envelope.archived.is_(False))
    stmt = stmt.order_by(Envelope.envelope_class.asc(), Envelope.name.asc())
    return list((await db.execute(stmt)).scalars().all())


async def is_valid_envelope_target(
    db: AsyncSession, *, user_id: uuid.UUID, envelope_id: uuid.UUID
) -> bool:
    """An envelope is a valid attachment/assignment target iff it exists,
    belongs to the user, and is non-archived. Shared by the bill/debt attach
    endpoints, the chat ATTACH_EXPENSE flow, and PATCH /transactions."""
    row = await db.execute(
        select(Envelope.id).where(
            Envelope.id == envelope_id,
            Envelope.user_id == user_id,
            Envelope.archived.is_(False),
        )
    )
    return row.scalar_one_or_none() is not None


async def can_assign_transaction_to_envelope(
    db: AsyncSession, *, user_id: uuid.UUID, envelope_id: uuid.UUID
) -> bool:
    """True iff the caller may tag a TRANSACTION to this envelope: it's
    non-archived AND (owned by the caller OR the caller is a member of its
    shared root). This is the ONLY place the owner-only rule is widened for
    sharing — bill/debt attachment stays owner-only (``is_valid_envelope_target``).
    The PATCH /transactions fetch is already user-scoped, so a member still only
    ever moves their OWN transaction; this just lets the destination be a shared
    envelope."""
    env = (
        await db.execute(select(Envelope).where(Envelope.id == envelope_id))
    ).scalar_one_or_none()
    if env is None or env.archived:
        return False
    if env.user_id == user_id:
        return True
    # Not the owner — walk to the root (depth ≤ 5, forest guaranteed) and check
    # membership. The target node + its root must both be non-archived.
    root = env
    guard = 0
    while root.parent_id is not None and guard < 5:
        parent = (
            await db.execute(
                select(Envelope).where(Envelope.id == root.parent_id)
            )
        ).scalar_one_or_none()
        if parent is None:
            return False
        root = parent
        guard += 1
    if root.archived:
        return False
    from .envelope_sharing import is_member

    return await is_member(db, user_id=user_id, root_id=root.id)


# ── name resolution for chat attachment (Intent.ATTACH_EXPENSE) ───────────────


@dataclass(frozen=True)
class AttachableObligation:
    """A recurring bill, debt, or credit card the user can attach to an
    envelope. For kind="card", `id` is the credit ACCOUNT id (the terms row is
    resolved from it at commit)."""

    kind: str  # "bill" | "debt" | "card"
    id: uuid.UUID
    name: str
    # amount_expected (bill, may be None) / minimum_payment (debt) /
    # live minimum due (card)
    amount: Optional[Decimal]


def _name_match(items: list, hint: str, key) -> list:
    """Case-insensitive name match: exact first, else substring either way.
    Mirrors app/queries/tools/goals.py::_match so chat resolution behaves the
    same on the read + write sides."""
    q = " ".join(hint.split()).lower()
    if not q:
        return []
    exact = [i for i in items if key(i).lower() == q]
    if exact:
        return exact
    return [i for i in items if q in key(i).lower() or key(i).lower() in q]


async def match_envelopes_by_name(
    db: AsyncSession, *, user_id: uuid.UUID, hint: str
) -> list[Envelope]:
    """Active (non-archived) envelopes whose name matches the hint."""
    envs = await fetch_envelopes(db, user_id=user_id, include_archived=False)
    return _name_match(envs, hint, lambda e: e.name)


async def match_obligations_by_name(
    db: AsyncSession, *, user_id: uuid.UUID, hint: str
) -> list[AttachableObligation]:
    """Active recurring bills + debts whose name matches the hint, as a unified
    candidate list for chat attachment."""
    bill_rows = (
        await db.execute(
            select(RecurringBill).where(
                RecurringBill.user_id == user_id,
                RecurringBill.is_active.is_(True),
            )
        )
    ).scalars().all()
    debt_rows = (
        await db.execute(
            select(Debt).where(
                Debt.user_id == user_id,
                Debt.is_active.is_(True),
                Debt.archived.is_(False),
            )
        )
    ).scalars().all()
    cands: list[AttachableObligation] = [
        AttachableObligation(
            "bill",
            b.id,
            b.name,
            Decimal(b.amount_expected) if b.amount_expected is not None else None,
        )
        for b in bill_rows
    ]
    cands += [
        AttachableObligation("debt", d.id, d.name, Decimal(d.minimum_payment))
        for d in debt_rows
    ]
    # Phase 7b B5: credit cards with terms ("meté el pago de la tarjeta en el
    # sobre Deudas"). The candidate id is the ACCOUNT id; superseded
    # credit_card Debts are excluded so a card never matches twice.
    from .credit_cards import (
        list_active_cards_with_terms,
        superseded_credit_card_debt_ids,
    )

    superseded = await superseded_credit_card_debt_ids(db, user_id=user_id)
    if superseded:
        cands = [
            c for c in cands if not (c.kind == "debt" and c.id in superseded)
        ]
    cands += [
        AttachableObligation(
            "card", c.account.id, c.account.name, c.minimum_due or None
        )
        for c in await list_active_cards_with_terms(db, user_id=user_id)
    ]
    return _name_match(cands, hint, lambda c: c.name)


async def list_active_bills(
    db: AsyncSession, *, user_id: uuid.UUID
) -> list[RecurringBill]:
    """All active recurring bills (chat mark_bill_paid options)."""
    rows = (
        await db.execute(
            select(RecurringBill).where(
                RecurringBill.user_id == user_id,
                RecurringBill.is_active.is_(True),
            )
        )
    ).scalars().all()
    return list(rows)


async def match_bills_by_name(
    db: AsyncSession, *, user_id: uuid.UUID, hint: str
) -> list[RecurringBill]:
    """Active recurring bills whose name matches the hint — the chat
    mark_bill_paid target resolver (Flexible Payment Dates). Reuses _name_match
    so it behaves like attachment/goal name resolution."""
    return _name_match(
        await list_active_bills(db, user_id=user_id), hint, lambda b: b.name
    )


async def list_active_debts(
    db: AsyncSession, *, user_id: uuid.UUID
) -> list[Debt]:
    """All active (non-archived) debts (chat record_debt_payment options)."""
    rows = (
        await db.execute(
            select(Debt).where(
                Debt.user_id == user_id,
                Debt.is_active.is_(True),
                Debt.archived.is_(False),
            )
        )
    ).scalars().all()
    return list(rows)


async def match_debts_by_name(
    db: AsyncSession, *, user_id: uuid.UUID, hint: str
) -> list[Debt]:
    """Active (non-archived) debts whose name matches the hint — the chat
    record_debt_payment target resolver (Flexible Payment Dates)."""
    return _name_match(
        await list_active_debts(db, user_id=user_id), hint, lambda d: d.name
    )


async def list_unattached_obligations(
    db: AsyncSession, *, user_id: uuid.UUID
) -> list[tuple[str, Optional[Decimal], str]]:
    """Active recurring bills + debts + credit-card minimums NOT attached to
    any envelope (envelope_id IS NULL), as ``(name, amount, source)``. Drives
    the per-item under_coverage gate (B3). ``amount`` is None for
    variable-amount bills. A card with nothing owed produces no obligation;
    a credit_card Debt superseded by an account-with-terms is excluded
    (coexistence rule — never count the same card twice)."""
    bill_rows = (
        await db.execute(
            select(RecurringBill.name, RecurringBill.amount_expected).where(
                RecurringBill.user_id == user_id,
                RecurringBill.is_active.is_(True),
                RecurringBill.envelope_id.is_(None),
            )
        )
    ).all()
    from .credit_cards import (
        list_active_cards_with_terms,
        superseded_credit_card_debt_ids,
    )

    superseded = await superseded_credit_card_debt_ids(db, user_id=user_id)
    debt_stmt = select(Debt.name, Debt.minimum_payment).where(
        Debt.user_id == user_id,
        Debt.is_active.is_(True),
        Debt.archived.is_(False),
        Debt.envelope_id.is_(None),
        # Loan cargo automático: a card-linked loan's servicing flows through the
        # card (its own gate item), so it's not an unattached obligation here.
        Debt.charge_to_account_id.is_(None),
    )
    if superseded:
        debt_stmt = debt_stmt.where(Debt.id.notin_(superseded))
    debt_rows = (await db.execute(debt_stmt)).all()
    out: list[tuple[str, Optional[Decimal], str]] = [
        (name, Decimal(str(amt)) if amt is not None else None, "bill")
        for name, amt in bill_rows
    ]
    out += [(name, Decimal(str(amt)), "debt") for name, amt in debt_rows]
    out += [
        (c.account.name, c.minimum_due, "card")
        for c in await list_active_cards_with_terms(db, user_id=user_id)
        if c.terms.envelope_id is None and c.minimum_due > 0
    ]
    return out


async def count_unassigned_month_expenses(
    db: AsyncSession, *, user: User, today: Optional[date] = None
) -> int:
    """How many current-month confirmed EXPENSES the user has with no envelope
    (envelope_id IS NULL). Drives the Phase 8 B6 "tenés N gastos sin sobre" chat
    nudge. Excludes transfer legs, goal flows, AND reconciliation ajustes — the
    same "real expense" filter onboarding `_has_expense` and the dashboard P&L
    aggregators use, so a balance-correction artifact never reads as a gasto the
    user should file into a sobre."""
    # Reconciliation ajustes are confirmed negative rows with envelope_id IS NULL
    # (anchors.apply_anchor) — they'd otherwise be miscounted as spendable gastos.
    from .anchors import AJUSTE_CATEGORY

    today = today or _user_today(user)
    start = date(today.year, today.month, 1)
    end = _next_month_start(start)
    row = await db.execute(
        select(func.count())
        .select_from(Transaction)
        .where(
            Transaction.user_id == user.id,
            Transaction.envelope_id.is_(None),
            Transaction.amount < 0,
            Transaction.archived.is_(False),
            Transaction.status == "confirmed",
            Transaction.transfer_id.is_(None),
            Transaction.goal_id.is_(None),
            Transaction.category.is_distinct_from(AJUSTE_CATEGORY),
            Transaction.transaction_date >= start,
            Transaction.transaction_date < end,
        )
    )
    return int(row.scalar_one() or 0)


async def active_children(
    db: AsyncSession, *, user_id: uuid.UUID, parent_id: uuid.UUID
) -> list[Envelope]:
    """Direct, non-archived children of one envelope."""
    rows = await db.execute(
        select(Envelope).where(
            Envelope.user_id == user_id,
            Envelope.parent_id == parent_id,
            Envelope.archived.is_(False),
        )
    )
    return list(rows.scalars().all())


def descendant_ids(
    envelopes: list[Envelope], root_id: uuid.UUID
) -> list[uuid.UUID]:
    """All descendant ids of root_id (root excluded) over the adjacency list.

    The depth cap (5) + create-only parent_id guarantee a forest (no cycles),
    so a plain stack walk terminates."""
    cmap: dict[Optional[uuid.UUID], list[Envelope]] = {}
    for e in envelopes:
        cmap.setdefault(e.parent_id, []).append(e)
    out: list[uuid.UUID] = []
    stack = list(cmap.get(root_id, []))
    while stack:
        node = stack.pop()
        out.append(node.id)
        stack.extend(cmap.get(node.id, []))
    return out


async def archive_subtree(db: AsyncSession, *, user: User, root: Envelope) -> None:
    """Soft-archive a root + its whole subtree. Detaches any fixed expenses
    pinned to the archived envelopes — soft archive doesn't fire the FK
    SET NULL, so it's explicit; the bill/debt survives. Idempotent; the caller
    commits."""
    envs = await fetch_envelopes(db, user_id=user.id, include_archived=True)
    ids = [root.id, *descendant_ids(envs, root.id)]
    await db.execute(
        update(Envelope)
        .where(Envelope.id.in_(ids))
        .values(archived=True, is_active=False)
    )
    await db.execute(
        update(RecurringBill)
        .where(RecurringBill.envelope_id.in_(ids))
        .values(envelope_id=None)
    )
    await db.execute(
        update(Debt).where(Debt.envelope_id.in_(ids)).values(envelope_id=None)
    )
    # Phase 7b B5: card terms attach to envelopes too (FK only fires on a
    # hard delete, so the soft archive detaches explicitly, same as above).
    from ..models.credit_card_terms import CreditCardTerms

    await db.execute(
        update(CreditCardTerms)
        .where(CreditCardTerms.envelope_id.in_(ids))
        .values(envelope_id=None)
    )


async def set_class_subtree(
    db: AsyncSession, *, user: User, root: Envelope, new_class: str
) -> None:
    """Set envelope_class on a root + its whole subtree (one tree = one class).
    The caller commits."""
    envs = await fetch_envelopes(db, user_id=user.id, include_archived=True)
    ids = [root.id, *descendant_ids(envs, root.id)]
    await db.execute(
        update(Envelope)
        .where(Envelope.id.in_(ids))
        .values(envelope_class=new_class)
    )


# ── reallocation: move budget between two same-level envelopes (Phase 8 B4) ──


def _money(amount: Decimal, currency: str) -> str:
    symbol = "$" if (currency or "CRC").upper() == "USD" else "₡"
    return f"{symbol}{amount:,.0f}"


def reallocation_blocker(
    *,
    from_env: Envelope,
    to_env: Envelope,
    amount: Decimal,
    from_children_total: Decimal,
) -> Optional[str]:
    """Pure validation for a reallocation. Returns a voseo error message when the
    move is invalid, else None. Shared by the service writer (raises 422) and the
    chat dispatcher (returns a Reject) so the user sees the SAME reason.

    The CRITICAL byte-lock invariant — committed_outflows = total_limit =
    Σ over ROOT envelopes of root.limit (in summary currency) — must stay
    invariant across a reallocation. That holds iff `from.parent_id ==
    to.parent_id`: root↔root keeps Σ(root limits) fixed (one root grows by
    exactly what the other shrinks); sibling↔sibling keeps the shared parent's
    allocation fixed AND leaves every root untouched. A root↔child move would
    change Σ(root limits) and silently shrink the budget — rejected here.
    """
    if amount <= 0:
        return "El monto a mover debe ser mayor que cero."
    if from_env.id == to_env.id:
        return "El sobre de origen y el de destino no pueden ser el mismo."
    if from_env.archived or to_env.archived:
        return "No se puede mover presupuesto desde o hacia un sobre archivado."
    if from_env.currency != to_env.currency:
        return "Los dos sobres deben estar en la misma moneda."
    if from_env.parent_id != to_env.parent_id:
        return (
            "Solo se puede mover presupuesto entre sobres del mismo nivel "
            "(dos sobres principales, o dos sub-sobres del mismo padre)."
        )
    # From-side floor: the source can't drop below what its own children already
    # have allocated, and never to zero-or-less.
    new_from = Decimal(from_env.limit_amount) - amount
    floor = max(from_children_total, Decimal("0"))
    if new_from < floor or new_from <= 0:
        max_movable = Decimal(from_env.limit_amount) - floor
        return (
            f"No podés mover tanto de «{from_env.name}». Lo máximo disponible es "
            f"{_money(max_movable, from_env.currency)}."
        )
    return None


async def reallocate(
    db: AsyncSession,
    *,
    user: User,
    from_id: uuid.UUID,
    to_id: uuid.UUID,
    amount: Decimal,
) -> tuple[Envelope, Envelope]:
    """Move `amount` of budget (limit_amount) from one envelope to another,
    deterministically. The LLM never computes this — the dispatcher proposes and
    this writes. Used by BOTH the REST endpoint and the chat commit; the CALLER
    commits (mirrors create_transfer_with_transactions usage).
    """
    amount = Decimal(amount)
    from_env = (
        await db.execute(
            select(Envelope).where(
                Envelope.id == from_id, Envelope.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    to_env = (
        await db.execute(
            select(Envelope).where(
                Envelope.id == to_id, Envelope.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if from_env is None or to_env is None:
        raise HTTPException(status_code=404, detail="Sobre no encontrado.")

    from_children = await active_children(
        db, user_id=user.id, parent_id=from_env.id
    )
    from_children_total = sum(
        (Decimal(c.limit_amount) for c in from_children), Decimal("0")
    )
    blocker = reallocation_blocker(
        from_env=from_env,
        to_env=to_env,
        amount=amount,
        from_children_total=from_children_total,
    )
    if blocker is not None:
        raise HTTPException(status_code=422, detail=blocker)

    from_env.limit_amount = Decimal(from_env.limit_amount) - amount
    to_env.limit_amount = Decimal(to_env.limit_amount) + amount
    await db.flush()
    return from_env, to_env


async def compute_envelope_summary(
    db: AsyncSession, *, user: User, today: Optional[date] = None
) -> EnvelopeSummaryResponse:
    # `today` override (Phase 7d): lets the snapshot job recapture a just-
    # closed month over its full window (limits/reservations are still read
    # from the CURRENT rows — snapshots exist precisely to freeze them going
    # forward). Default = live "now" in the user's timezone.
    today = today or _user_today(user)
    start = date(today.year, today.month, 1)
    end = _next_month_start(start)
    period = f"{today.year:04d}-{today.month:02d}"
    currency = user.currency or "CRC"

    envelopes = await fetch_envelopes(db, user_id=user.id, include_archived=False)

    # Live OWN (direct) spend per envelope: confirmed, non-archived EXPENSES
    # (amount < 0) dated in the current month. Grouped by currency too, because
    # a USD expense tagged to a CRC envelope must be converted before it counts
    # (see api/services/fx.py) — otherwise $30 would read as ₡30.
    #
    # Transfer legs: the query already requires envelope_id IS NOT NULL, and the
    # ONLY path that puts an envelope_id on a transfer leg is the deterministic
    # card-payment stamp in create_transfer_with_transactions (Phase 7b B5 —
    # PATCH still 409s legs). That stamped debit leg MUST count as spend so the
    # card reservation swaps to spend when paid, never both. So no transfer
    # exclusion here — an envelope-tagged expense counts, leg or not.
    # Scope spend to THIS user's envelope ids rather than `user_id == user.id`.
    # Shared envelopes: when this user OWNS a shared envelope, members' expenses
    # tagged to it must drain the same (shared-cap) bar — so we count every
    # transaction tagged to the user's own envelopes regardless of who created
    # it. A non-shared envelope can only be tagged by its owner
    # (can_assign_transaction_to_envelope), so this is IDENTICAL to the old
    # user_id filter absent sharing. The user's OWN spend on envelopes they only
    # JOINED is NOT counted here (those ids aren't in own_env_ids) — it surfaces
    # via shared_summary_items instead, so it never inflates their own totals.
    own_env_ids = [e.id for e in envelopes]
    # {envelope_id: [(tx_currency, summed_abs_amount), ...]}
    spent_raw: dict = {}
    if own_env_ids:
        spend_rows = await db.execute(
            select(
                Transaction.envelope_id,
                Transaction.currency,
                func.coalesce(func.sum(func.abs(Transaction.amount)), 0),
            )
            .where(
                Transaction.envelope_id.in_(own_env_ids),
                Transaction.amount < 0,
                Transaction.archived.is_(False),
                Transaction.status == "confirmed",
                Transaction.transaction_date >= start,
                Transaction.transaction_date < end,
            )
            .group_by(Transaction.envelope_id, Transaction.currency)
        )
        for env_id, tx_currency, total in spend_rows.all():
            spent_raw.setdefault(env_id, []).append((tx_currency, Decimal(total)))

    # Direct spend per node, in the NODE's own currency.
    direct: dict[uuid.UUID, Decimal] = {}
    for env in envelopes:
        d = Decimal("0")
        for tx_currency, bucket in spent_raw.get(env.id, []):
            d += convert(bucket, tx_currency, env.currency)
        direct[env.id] = d

    # Adjacency over the non-archived set.
    children_map: dict[Optional[uuid.UUID], list[Envelope]] = {}
    for env in envelopes:
        children_map.setdefault(env.parent_id, []).append(env)

    # Rolled-up spend = own + Σ descendants (memoized DFS). Children inherit the
    # root currency, so within-tree summation needs no FX conversion.
    rolled: dict[uuid.UUID, Decimal] = {}

    def _rollup(env_id: uuid.UUID) -> Decimal:
        cached = rolled.get(env_id)
        if cached is not None:
            return cached
        total = direct.get(env_id, Decimal("0"))
        for child in children_map.get(env_id, []):
            total += _rollup(child.id)
        rolled[env_id] = total
        return total

    for env in envelopes:
        _rollup(env.id)

    # ── reservations: attached fixed expenses, unpaid this cycle (B2) ──────────
    # An attached bill/debt reserves its expected amount inside the envelope WHILE
    # UNPAID this cycle. Once the actual payment lands (the bill's current-month
    # occurrence is PAID/PARTIALLY_PAID, or a DebtPayment exists this month) the
    # reservation releases — the actual transaction counts as spend instead, never
    # both. Variable bills (no amount_expected) reserve 0; the link still counts
    # toward per-item coverage (B3).
    env_currency = {e.id: e.currency for e in envelopes}
    direct_reserved: dict[uuid.UUID, Decimal] = {}

    bill_rows = (
        await db.execute(
            select(
                RecurringBill.id,
                RecurringBill.envelope_id,
                RecurringBill.amount_expected,
                RecurringBill.currency,
                RecurringBill.is_variable_amount,
            ).where(
                RecurringBill.user_id == user.id,
                RecurringBill.is_active.is_(True),
                RecurringBill.envelope_id.isnot(None),
            )
        )
    ).all()
    bill_ids = [r[0] for r in bill_rows]
    paid_bills: set = set()
    if bill_ids:
        occ_rows = (
            await db.execute(
                select(
                    BillOccurrence.recurring_bill_id, BillOccurrence.status
                ).where(
                    BillOccurrence.recurring_bill_id.in_(bill_ids),
                    BillOccurrence.due_date >= start,
                    BillOccurrence.due_date < end,
                )
            )
        ).all()
        paid_bills = {
            bid for bid, status in occ_rows
            if status in ("paid", "partially_paid")
        }
    for bid, env_id, amount_expected, bcur, is_variable in bill_rows:
        ec = env_currency.get(env_id)
        if ec is None or is_variable or amount_expected is None or bid in paid_bills:
            continue
        direct_reserved[env_id] = direct_reserved.get(
            env_id, Decimal("0")
        ) + convert(Decimal(str(amount_expected)), bcur, ec)

    debt_rows = (
        await db.execute(
            select(
                Debt.id, Debt.envelope_id, Debt.minimum_payment, Debt.currency
            ).where(
                Debt.user_id == user.id,
                Debt.is_active.is_(True),
                Debt.archived.is_(False),
                Debt.envelope_id.isnot(None),
                # Loan cargo automático: a card-linked loan reserves through the
                # card (released by the cargo charge), never in its own envelope.
                Debt.charge_to_account_id.is_(None),
            )
        )
    ).all()
    debt_ids = [r[0] for r in debt_rows]
    paid_debts: set = set()
    if debt_ids:
        pay_rows = (
            await db.execute(
                select(DebtPayment.debt_id).where(
                    DebtPayment.debt_id.in_(debt_ids),
                    DebtPayment.payment_date >= start,
                    DebtPayment.payment_date < end,
                )
            )
        ).all()
        paid_debts = {r[0] for r in pay_rows}
    for did, env_id, min_payment, dcur in debt_rows:
        ec = env_currency.get(env_id)
        if ec is None or did in paid_debts:
            continue
        direct_reserved[env_id] = direct_reserved.get(
            env_id, Decimal("0")
        ) + convert(Decimal(str(min_payment)), dcur, ec)

    # Phase 7b B5: an attached credit card reserves its LIVE minimum while
    # unpaid this cycle. "Paid" ⟺ Σ(credit transfer legs INTO the card this
    # month) ≥ the current minimum — and the matching debit leg was stamped
    # with the envelope (it counts as spend above), so the reservation swaps
    # to spend exactly, never both.
    from .credit_cards import list_active_cards_with_terms

    attached_cards = [
        c
        for c in await list_active_cards_with_terms(db, user_id=user.id)
        if c.terms.envelope_id is not None and c.minimum_due > 0
    ]
    if attached_cards:
        pay_rows = (
            await db.execute(
                select(
                    Transaction.account_id,
                    func.coalesce(func.sum(Transaction.amount), 0),
                )
                .where(
                    Transaction.user_id == user.id,
                    Transaction.account_id.in_(
                        [c.account.id for c in attached_cards]
                    ),
                    Transaction.transfer_id.isnot(None),
                    Transaction.amount > 0,
                    Transaction.status == "confirmed",
                    Transaction.archived.is_(False),
                    Transaction.transaction_date >= start,
                    Transaction.transaction_date < end,
                )
                .group_by(Transaction.account_id)
            )
        ).all()
        paid_into_card = {
            acc_id: Decimal(str(total)) for acc_id, total in pay_rows
        }
        for c in attached_cards:
            ec = env_currency.get(c.terms.envelope_id)
            if ec is None:
                continue
            if paid_into_card.get(c.account.id, Decimal("0")) >= c.minimum_due:
                continue
            direct_reserved[c.terms.envelope_id] = direct_reserved.get(
                c.terms.envelope_id, Decimal("0")
            ) + convert(c.minimum_due, c.account.currency, ec)

    # Roll reservations up the tree (own + descendants), like spend.
    rolled_reserved: dict[uuid.UUID, Decimal] = {}

    def _rollup_reserved(env_id: uuid.UUID) -> Decimal:
        cached = rolled_reserved.get(env_id)
        if cached is not None:
            return cached
        total = direct_reserved.get(env_id, Decimal("0"))
        for child in children_map.get(env_id, []):
            total += _rollup_reserved(child.id)
        rolled_reserved[env_id] = total
        return total

    for env in envelopes:
        _rollup_reserved(env.id)

    items: list[EnvelopeSummaryItem] = []
    # Per-class subtotals + the total are reported in the summary (user)
    # currency, so each node's figures are converted from its own currency.
    class_acc: dict[str, dict[str, float]] = {}
    for env in envelopes:
        limit_dec = Decimal(env.limit_amount)
        rolled_dec = rolled[env.id]
        direct_dec = direct.get(env.id, Decimal("0"))
        allocated_dec = sum(
            (Decimal(c.limit_amount) for c in children_map.get(env.id, [])),
            Decimal("0"),
        )
        limit_f = float(limit_dec)
        rolled_f = float(rolled_dec)
        reserved_f = float(rolled_reserved.get(env.id, Decimal("0")))
        items.append(
            EnvelopeSummaryItem(
                id=env.id,
                parent_id=env.parent_id,
                depth=env.depth,
                name=env.name,
                envelope_class=env.envelope_class,
                currency=env.currency,
                limit_amount=round(limit_f, 2),
                spent=round(rolled_f, 2),
                direct_spent=round(float(direct_dec), 2),
                reserved=round(reserved_f, 2),
                # Free = limit − reserved (attached, unpaid) − spent (actual).
                available=round(limit_f - reserved_f - rolled_f, 2),
                remaining=round(limit_f - rolled_f, 2),
                pct=round((rolled_f / limit_f) if limit_f > 0 else 0.0, 4),
                over_limit=rolled_dec > limit_dec,
                allocated=round(float(allocated_dec), 2),
                unallocated=round(float(limit_dec - allocated_dec), 2),
                over_allocated=allocated_dec > limit_dec,
            )
        )
        # Class spent = every node's OWN spend (each transaction once). Class
        # limit = ROOTS only — a parent's limit already contains its
        # descendants' sub-allocations, so counting child limits double-counts.
        acc = class_acc.setdefault(env.envelope_class, {"limit": 0.0, "spent": 0.0})
        acc["spent"] += float(convert(direct_dec, env.currency, currency))
        if env.parent_id is None:
            acc["limit"] += float(convert(limit_dec, env.currency, currency))

    by_class = [
        EnvelopeClassSubtotal(
            envelope_class=cls,
            limit_total=round(acc["limit"], 2),
            spent_total=round(acc["spent"], 2),
            over_limit=acc["spent"] > acc["limit"],
        )
        for cls, acc in sorted(
            class_acc.items(),
            key=lambda kv: _CLASS_ORDER.index(kv[0]) if kv[0] in _CLASS_ORDER else 99,
        )
    ]

    income = await _monthly_income(db, user=user)
    # In the summary currency (class subtotals are already converted) so it's
    # comparable to monthly_income; summing item.limit_amount would mix CRC+USD
    # AND double-count children.
    total_limit = sum(sub.limit_total for sub in by_class)
    # Grand totals for the home-screen headline, derived from the same figures
    # the bars use (roots only, summary currency). Spend per class already counts
    # every node's own spend once, so the class sum equals the root roll-ups.
    # Reservations roll up the tree, so summing roots counts each one once.
    total_spent = sum(sub.spent_total for sub in by_class)
    total_reserved = sum(
        float(convert(rolled_reserved.get(env.id, Decimal("0")), env.currency, currency))
        for env in envelopes
        if env.parent_id is None
    )

    # Shared envelopes the user is a MEMBER of (not owner) are appended as
    # display-only items AFTER all owner-scoped figures above are computed. They
    # are deliberately NOT folded into class_acc / by_class / total_* — the
    # byte-locked cashflow/affordability/snapshot consumers must keep seeing only
    # the user's OWN budget. See api/services/envelope_sharing.py.
    from .envelope_sharing import shared_summary_items

    items += await shared_summary_items(db, user=user, start=start, end=end)

    return EnvelopeSummaryResponse(
        period=period,
        currency=currency,
        envelopes=items,
        by_class=by_class,
        total_limit=round(total_limit, 2),
        total_spent=round(total_spent, 2),
        total_reserved=round(total_reserved, 2),
        total_available=round(total_limit - total_reserved - total_spent, 2),
        monthly_income=float(income) if income is not None else None,
    )

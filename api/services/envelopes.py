"""Envelope budgeting spend computation.

Spend is computed live from transactions — there is no stored running balance,
so the bars can never drift from the ledger. One grouped query per summary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import uuid
from typing import Optional

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
from .fx import convert

_CLASS_ORDER = ["needs", "wants", "savings", "investing"]

# Normalize a recurring-income cadence to a monthly figure for the
# "total limits vs income" sanity line. Approximate by design.
_FREQ_TO_MONTHLY = {
    "weekly": Decimal("52") / Decimal("12"),
    "biweekly": Decimal("26") / Decimal("12"),
    "monthly": Decimal("1"),
    "annual": Decimal("1") / Decimal("12"),
}


def _user_today(user: User) -> date:
    try:
        tz = ZoneInfo(user.timezone)
    except Exception:  # pragma: no cover - defensive
        tz = ZoneInfo("UTC")
    return datetime.now(tz).date()


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


# ── name resolution for chat attachment (Intent.ATTACH_EXPENSE) ───────────────


@dataclass(frozen=True)
class AttachableObligation:
    """A recurring bill or debt the user can attach to an envelope."""

    kind: str  # "bill" | "debt"
    id: uuid.UUID
    name: str
    amount: Optional[Decimal]  # amount_expected (bill, may be None) / minimum_payment (debt)


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
    return _name_match(cands, hint, lambda c: c.name)


async def list_unattached_obligations(
    db: AsyncSession, *, user_id: uuid.UUID
) -> list[tuple[str, Optional[Decimal], str]]:
    """Active recurring bills + debts NOT attached to any envelope
    (envelope_id IS NULL), as ``(name, amount, source)``. Drives the per-item
    under_coverage gate (B3). ``amount`` is None for variable-amount bills."""
    bill_rows = (
        await db.execute(
            select(RecurringBill.name, RecurringBill.amount_expected).where(
                RecurringBill.user_id == user_id,
                RecurringBill.is_active.is_(True),
                RecurringBill.envelope_id.is_(None),
            )
        )
    ).all()
    debt_rows = (
        await db.execute(
            select(Debt.name, Debt.minimum_payment).where(
                Debt.user_id == user_id,
                Debt.is_active.is_(True),
                Debt.archived.is_(False),
                Debt.envelope_id.is_(None),
            )
        )
    ).all()
    out: list[tuple[str, Optional[Decimal], str]] = [
        (name, Decimal(str(amt)) if amt is not None else None, "bill")
        for name, amt in bill_rows
    ]
    out += [(name, Decimal(str(amt)), "debt") for name, amt in debt_rows]
    return out


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


async def compute_envelope_summary(
    db: AsyncSession, *, user: User
) -> EnvelopeSummaryResponse:
    today = _user_today(user)
    start = date(today.year, today.month, 1)
    end = _next_month_start(start)
    period = f"{today.year:04d}-{today.month:02d}"
    currency = user.currency or "CRC"

    envelopes = await fetch_envelopes(db, user_id=user.id, include_archived=False)

    # Live OWN (direct) spend per envelope: confirmed, non-archived, non-transfer
    # EXPENSES (amount < 0) dated in the current month. Grouped by currency too,
    # because a USD expense tagged to a CRC envelope must be converted before it
    # counts (see api/services/fx.py) — otherwise $30 would read as ₡30.
    spend_rows = await db.execute(
        select(
            Transaction.envelope_id,
            Transaction.currency,
            func.coalesce(func.sum(func.abs(Transaction.amount)), 0),
        )
        .where(
            Transaction.user_id == user.id,
            Transaction.envelope_id.isnot(None),
            Transaction.amount < 0,
            Transaction.archived.is_(False),
            Transaction.transfer_id.is_(None),
            Transaction.status == "confirmed",
            Transaction.transaction_date >= start,
            Transaction.transaction_date < end,
        )
        .group_by(Transaction.envelope_id, Transaction.currency)
    )
    # {envelope_id: [(tx_currency, summed_abs_amount), ...]}
    spent_raw: dict = {}
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
    return EnvelopeSummaryResponse(
        period=period,
        currency=currency,
        envelopes=items,
        by_class=by_class,
        total_limit=round(total_limit, 2),
        monthly_income=float(income) if income is not None else None,
    )

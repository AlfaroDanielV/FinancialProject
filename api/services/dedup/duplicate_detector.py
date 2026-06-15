"""Likely-duplicate expense detection (deterministic).

Heuristic (operator-locked: "monto+fecha, comercio refuerza"):
    a NEW confirmed expense `txn` is a likely duplicate of an existing row `R`
    when, for the same user and currency, both are expenses (amount < 0), R is
    confirmed + non-archived + not a transfer leg / goal flow, the magnitudes
    match (within a céntimo to absorb float/Decimal noise), and the dates are
    within ±3 days. A matching merchant raises confidence (and breaks ties)
    but is NOT required — the classic dupe is a manual capture vs the Gmail
    row of the SAME purchase, whose merchant text differs.

Only the NEWER row is ever flagged (`is_duplicate=True`); history is never
rewritten. Flagging raises a `duplicate_transaction` nudge through the Phase
5d rails (dedup_key `duplicate:{txn_id}` → idempotent), which delivers to
Telegram and the in-app Alertas feed. The user decides keep vs delete; the
deterministic code applies it. The LLM only phrases the Telegram push.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.transaction import Transaction
from ...models.user import User
from ...models.user_nudge import UserNudge
from ..nudges.actions import mark_acted_on
from ..transactions import hard_delete_transaction


log = logging.getLogger("dedup.duplicate_detector")

NUDGE_TYPE_DUPLICATE = "duplicate_transaction"

# ±3 calendar days around the new expense's date.
DUP_DATE_WINDOW_DAYS = 3
# Magnitudes must match within a céntimo — effectively exact for colones
# (whole numbers) while tolerating float→Numeric round-trips on USD.
DUP_AMOUNT_EPSILON = Decimal("0.01")

_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]")


def _as_decimal(value: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _norm_merchant(raw: Optional[str]) -> str:
    return _NON_ALNUM_RE.sub("", (raw or "").lower()).strip()


def _merchant_similar(a: Optional[str], b: Optional[str]) -> bool:
    """Loose, deterministic merchant overlap — the confidence *booster*, never
    a gate. Substring or any shared token counts. Empty on either side =
    inconclusive (False), which is fine because merchant is not required."""
    na, nb = _norm_merchant(a), _norm_merchant(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    return bool(set(na.split()) & set(nb.split()))


def _is_flaggable_expense(txn: Transaction) -> bool:
    """A row eligible to BE flagged: a confirmed, non-archived expense that is
    not a transfer leg or goal flow (those are machine-managed)."""
    if txn.status != "confirmed" or txn.archived:
        return False
    if txn.transfer_id is not None or txn.goal_id is not None:
        return False
    amount = _as_decimal(txn.amount)
    return amount is not None and amount < 0


async def find_likely_duplicate(
    db: AsyncSession, *, user_id: uuid.UUID, txn: Transaction
) -> Optional[Transaction]:
    """Return the most likely pre-existing duplicate of `txn`, or None.

    Picks the closest match by date, preferring a merchant match as a
    tiebreaker. Read-only."""
    amount = _as_decimal(txn.amount)
    if amount is None or amount >= 0:
        return None
    magnitude = -amount  # positive

    lo = txn.transaction_date - timedelta(days=DUP_DATE_WINDOW_DAYS)
    hi = txn.transaction_date + timedelta(days=DUP_DATE_WINDOW_DAYS)

    stmt = select(Transaction).where(
        Transaction.user_id == user_id,
        Transaction.id != txn.id,
        Transaction.currency == txn.currency,
        Transaction.status == "confirmed",
        Transaction.archived.is_(False),
        Transaction.transfer_id.is_(None),
        Transaction.goal_id.is_(None),
        Transaction.amount < 0,
        func.abs(func.abs(Transaction.amount) - magnitude) <= DUP_AMOUNT_EPSILON,
        Transaction.transaction_date >= lo,
        Transaction.transaction_date <= hi,
    )
    rows = list((await db.execute(stmt)).scalars().all())
    if not rows:
        return None

    def _rank(row: Transaction) -> tuple[int, int]:
        date_gap = abs((row.transaction_date - txn.transaction_date).days)
        merchant_penalty = 0 if _merchant_similar(row.merchant, txn.merchant) else 1
        return (date_gap, merchant_penalty)

    return min(rows, key=_rank)


def _build_payload(txn: Transaction, matched: Optional[Transaction]) -> dict[str, Any]:
    magnitude = _as_decimal(txn.amount)
    payload: dict[str, Any] = {
        "transaction_id": str(txn.id),
        "currency": txn.currency,
        "amount": f"{abs(magnitude):.2f}" if magnitude is not None else None,
        "merchant": txn.merchant,
        "transaction_date": txn.transaction_date.isoformat(),
    }
    if matched is not None:
        payload["matched_transaction_id"] = str(matched.id)
        payload["matched_merchant"] = matched.merchant
        payload["matched_date"] = matched.transaction_date.isoformat()
        payload["merchant_match"] = _merchant_similar(matched.merchant, txn.merchant)
    return payload


async def _upsert_duplicate_nudge(
    db: AsyncSession, *, user: User, txn: Transaction, matched: Optional[Transaction]
) -> Optional[uuid.UUID]:
    """INSERT ... ON CONFLICT DO NOTHING on (user_id, dedup_key). Returns the
    new id, or the existing nudge's id when one already exists, so the inline
    chat card always has a nudge_id to act on."""
    dedup_key = f"duplicate:{txn.id}"
    stmt = (
        pg_insert(UserNudge)
        .values(
            user_id=user.id,
            nudge_type=NUDGE_TYPE_DUPLICATE,
            priority="normal",
            dedup_key=dedup_key,
            payload=_build_payload(txn, matched),
        )
        .on_conflict_do_nothing(index_elements=["user_id", "dedup_key"])
        .returning(UserNudge.id)
    )
    new_id = (await db.execute(stmt)).scalar_one_or_none()
    if new_id is not None:
        return new_id
    return (
        await db.execute(
            select(UserNudge.id).where(
                UserNudge.user_id == user.id, UserNudge.dedup_key == dedup_key
            )
        )
    ).scalar_one_or_none()


async def flag_and_notify(
    db: AsyncSession, *, user: User, txn: Transaction
) -> tuple[Optional[Transaction], Optional[uuid.UUID]]:
    """At-capture hook. If `txn` looks like a duplicate, flag it and raise the
    nudge. Returns (matched_row, nudge_id) for the inline chat warning, or
    (None, None).

    Best-effort and swallow-on-fail with loud logging (like `advice_trace`):
    a detection hiccup must NEVER break a capture. The transaction is already
    committed by the caller; this only adds the flag + nudge."""
    if not _is_flaggable_expense(txn):
        return None, None
    try:
        matched = await find_likely_duplicate(db, user_id=user.id, txn=txn)
        if matched is None:
            return None, None
        txn.is_duplicate = True
        nudge_id = await _upsert_duplicate_nudge(
            db, user=user, txn=txn, matched=matched
        )
        await db.commit()
        return matched, nudge_id
    except Exception:  # noqa: BLE001 — never break a capture path
        log.exception(
            "flag_and_notify failed for txn=%s", getattr(txn, "id", None)
        )
        await db.rollback()
        return None, None


async def clear_duplicate_nudges_for_txn(
    db: AsyncSession, *, user_id: uuid.UUID, txn_id: uuid.UUID
) -> None:
    """Terminally resolve any pending/sent duplicate nudge whose subject is
    `txn_id` (called when the transaction is hard-deleted from any path, so a
    now-stale 'posible duplicado' alert doesn't linger). Caller commits."""
    dedup_key = f"duplicate:{txn_id}"
    rows = (
        await db.execute(
            select(UserNudge).where(
                UserNudge.user_id == user_id,
                UserNudge.dedup_key == dedup_key,
                UserNudge.status.in_(("pending", "sent")),
            )
        )
    ).scalars().all()
    for nudge in rows:
        await mark_acted_on(db, user_id=user_id, nudge_id=nudge.id)


async def resolve_duplicate(
    db: AsyncSession, *, user: User, nudge: UserNudge, keep: bool
) -> bool:
    """Apply the user's keep/delete decision on a `duplicate_transaction`
    nudge, then resolve the nudge terminally as acted_on (NOT dismissed — the
    user acted on the warning either way; routing keep/delete through the
    silence machinery would mute duplicate detection after two false
    positives). Caller commits. Returns True when the transaction was deleted.

    Raises `TransactionDeleteError` (from the delete service) if the row can't
    be deleted (linked to a bill/debt); the caller surfaces the reason."""
    raw = (nudge.payload or {}).get("transaction_id")
    txn: Optional[Transaction] = None
    if raw:
        try:
            tid = uuid.UUID(str(raw))
        except (TypeError, ValueError):
            tid = None
        if tid is not None:
            txn = (
                await db.execute(
                    select(Transaction).where(
                        Transaction.id == tid, Transaction.user_id == user.id
                    )
                )
            ).scalar_one_or_none()

    deleted = False
    if txn is not None:
        if keep:
            txn.is_duplicate = False
            await db.flush()
        else:
            await hard_delete_transaction(db, user=user, txn=txn)
            deleted = True

    await mark_acted_on(db, user_id=user.id, nudge_id=nudge.id)
    return deleted

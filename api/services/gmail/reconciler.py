"""Reconciliation engine for Gmail-extracted transactions.

Decides whether an `ExtractedEmailTransaction` is:
    - already represented by an existing manual/shortcut/telegram row
      (matched_existing → updates that row's source_ref),
    - already ingested by a previous Gmail scan (duplicate_gmail),
    - new and to be inserted as `confirmed` or `shadow` depending on the
      user's activation window,
    - or too low-confidence to act on (skipped_low_confidence).

The matching window is 7 days; tolerance is ±1 (in either currency unit
because the existing transactions table doesn't normalize CRC vs USD —
a USD candidate won't false-match a CRC row because we filter by
currency too). Score is amount-exactness > date-exactness; ties broken
by first-found.

Output: tuple (outcome, transaction_or_None). The scanner uses the
transaction id to fill `gmail_messages_seen.transaction_id`.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.gmail_credential import GmailCredential
from ...models.transaction import Transaction
from ..extraction.email_extractor import (
    EXPENSE_TYPES,
    INCOME_TYPES,
    ExtractedEmailTransaction,
)


log = logging.getLogger("api.services.gmail.reconciler")


# Threshold below which we don't even attempt to insert. The scanner
# also checks 0.6 to skip the call entirely; this 0.7 here is the
# tighter "actually write a transaction" gate.
RECONCILE_MIN_CONFIDENCE = 0.7

# Match window relative to candidate date.
MATCH_WINDOW_DAYS = 1

# Match window for searching pre-existing transactions (older than the
# date window above to catch back-dated email arrivals).
LOOKBACK_DAYS = 7

# Tolerance on amount difference. Tight by design — looser would
# produce false-merges between same-day same-merchant but distinct
# transactions.
AMOUNT_TOLERANCE = Decimal("1")

# Shadow window: 7 days from gmail_credentials.activated_at. New rows
# created during this window land as `status='shadow'` so the user can
# audit before they affect balances.
SHADOW_WINDOW_DAYS = 7


class ReconcileOutcome(str, Enum):
    MATCHED_EXISTING = "matched_existing"
    CREATED_NEW = "created_new"
    CREATED_SHADOW = "created_shadow"
    DUPLICATE_GMAIL = "duplicate_gmail"
    SKIPPED_LOW_CONFIDENCE = "skipped_low_confidence"


def _signed_amount(candidate: ExtractedEmailTransaction) -> Optional[Decimal]:
    """Apply the sign convention: charge/withdraw/etc → negative,
    deposit/refund → positive. Unknown / no amount → None."""
    if candidate.amount is None:
        return None
    if candidate.transaction_type in EXPENSE_TYPES:
        return -candidate.amount
    if candidate.transaction_type in INCOME_TYPES:
        return candidate.amount
    return None  # unknown — never insert


async def _is_in_shadow_window(
    *, db: AsyncSession, user_id: uuid.UUID
) -> bool:
    cred = (
        await db.execute(
            select(GmailCredential).where(GmailCredential.user_id == user_id)
        )
    ).scalar_one_or_none()
    if cred is None or cred.activated_at is None:
        # No activation timestamp → we don't know when the user activated;
        # safest is shadow (don't pollute the balance).
        return True
    elapsed = datetime.now(timezone.utc) - cred.activated_at
    return elapsed.days < SHADOW_WINDOW_DAYS


async def _find_existing_match(
    *,
    db: AsyncSession,
    user_id: uuid.UUID,
    candidate: ExtractedEmailTransaction,
) -> Optional[Transaction]:
    """Find a pre-existing transaction (manual/shortcut/telegram) that
    plausibly represents the same event. Returns None if nothing
    matches.

    Filters:
        - same user
        - currency matches candidate.currency
        - amount within ±AMOUNT_TOLERANCE of |candidate signed|
        - transaction_date within ±MATCH_WINDOW_DAYS of candidate date
        - gmail_message_id IS NULL (we don't re-merge gmail rows)

    Sort: smallest amount diff, then smallest date diff. We do this in
    Python after the SQL prune to keep the query simple.
    """
    cand_amount = candidate.amount
    cand_date = candidate.transaction_date
    if cand_amount is None or cand_date is None or candidate.currency is None:
        return None
    cand_signed = _signed_amount(candidate)
    if cand_signed is None:
        return None

    date_low = cand_date - timedelta(days=MATCH_WINDOW_DAYS)
    date_high = cand_date + timedelta(days=MATCH_WINDOW_DAYS)

    # Match on absolute amount (existing rows have signed amounts; a
    # gmail charge candidate signed -5000 should match a manual -5000
    # OR a +5000 if the user typed the wrong sign, but let's not be
    # generous — match on signed equality with tolerance.
    rows = await db.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .where(Transaction.currency == candidate.currency)
        .where(Transaction.gmail_message_id.is_(None))
        .where(
            and_(
                Transaction.transaction_date >= date_low,
                Transaction.transaction_date <= date_high,
            )
        )
        .where(
            func.abs(Transaction.amount - cand_signed) <= AMOUNT_TOLERANCE
        )
        .order_by(Transaction.transaction_date.desc())
    )
    candidates = list(rows.scalars().all())
    if not candidates:
        return None

    # Score: lower amount diff wins; tie-break by smaller date diff.
    def score(t: Transaction) -> tuple[Decimal, int]:
        amt_diff = abs(Decimal(t.amount) - cand_signed)
        d_diff = abs((t.transaction_date - cand_date).days)
        return (amt_diff, d_diff)

    candidates.sort(key=score)
    best = candidates[0]
    log.info(
        "reconcile_match_found user=%s txn=%s candidates=%d",
        user_id,
        best.id,
        len(candidates),
    )
    return best


async def _check_duplicate_gmail(
    *,
    db: AsyncSession,
    user_id: uuid.UUID,
    gmail_message_id: str,
) -> Optional[Transaction]:
    """If a transaction already carries this gmail_message_id, return it
    (caller handles the duplicate path). Should be rare given the
    scanner's gmail_messages_seen dedup, but the partial UNIQUE in 0011
    makes us robust to a re-issue."""
    row = await db.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .where(Transaction.gmail_message_id == gmail_message_id)
        .limit(1)
    )
    return row.scalar_one_or_none()


async def reconcile(
    *,
    db: AsyncSession,
    user_id: uuid.UUID,
    candidate: ExtractedEmailTransaction,
    gmail_message_id: str,
    email_subject: Optional[str] = None,
    email_from: Optional[str] = None,
) -> tuple[ReconcileOutcome, Optional[Transaction]]:
    """Decide and act. Always commits-friendly — returns the row
    in-session; the caller commits.
    """
    # 1. Confidence gate.
    if candidate.confidence < RECONCILE_MIN_CONFIDENCE:
        log.info(
            "reconcile_skipped_low_conf user=%s conf=%.2f",
            user_id,
            candidate.confidence,
        )
        return (ReconcileOutcome.SKIPPED_LOW_CONFIDENCE, None)

    # Sanity: if amount or sign is missing, we can't write a transaction.
    signed = _signed_amount(candidate)
    if signed is None:
        log.info(
            "reconcile_skipped_no_signed_amount user=%s type=%s amount=%s",
            user_id,
            candidate.transaction_type,
            candidate.amount,
        )
        return (ReconcileOutcome.SKIPPED_LOW_CONFIDENCE, None)

    # 2. Already-ingested same Gmail message.
    dup = await _check_duplicate_gmail(
        db=db, user_id=user_id, gmail_message_id=gmail_message_id
    )
    if dup is not None:
        log.info(
            "reconcile_duplicate_gmail user=%s msg=%s txn=%s",
            user_id,
            gmail_message_id,
            dup.id,
        )
        return (ReconcileOutcome.DUPLICATE_GMAIL, dup)

    # 3. Match against pre-existing manual/telegram/shortcut rows.
    match = await _find_existing_match(
        db=db, user_id=user_id, candidate=candidate
    )
    if match is not None:
        match.gmail_message_id = gmail_message_id
        match.source = "reconciled"
        if not match.merchant and candidate.merchant:
            match.merchant = candidate.merchant
        await db.flush()
        return (ReconcileOutcome.MATCHED_EXISTING, match)

    # 4. New row. Shadow vs. confirmed depends on activation age.
    in_shadow = await _is_in_shadow_window(db=db, user_id=user_id)
    status = "shadow" if in_shadow else "confirmed"

    txn = Transaction(
        user_id=user_id,
        amount=signed,
        currency=candidate.currency or "CRC",
        merchant=candidate.merchant,
        description=candidate.description or _compose_description(
            email_subject, email_from
        ),
        transaction_date=candidate.transaction_date or date.today(),
        source="gmail",
        gmail_message_id=gmail_message_id,
        status=status,
    )
    db.add(txn)
    await db.flush()
    log.info(
        "reconcile_created user=%s msg=%s status=%s amount=%s",
        user_id,
        gmail_message_id,
        status,
        signed,
    )
    return (
        ReconcileOutcome.CREATED_SHADOW if in_shadow else ReconcileOutcome.CREATED_NEW,
        txn,
    )


def _compose_description(
    subject: Optional[str], from_addr: Optional[str]
) -> str:
    """Fallback description when the LLM didn't produce one. Uses the
    Subject and From so the transaction is at least navigable in the
    UI / logs."""
    parts = []
    if subject:
        parts.append(f"Email: {subject}")
    if from_addr:
        parts.append(f"de {from_addr}")
    return " — ".join(parts) if parts else "Notificación bancaria"

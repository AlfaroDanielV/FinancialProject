"""Reconciliation engine for Gmail-extracted transactions.

Decides whether an `ExtractedEmailTransaction` is:
    - already represented by an existing manual/shortcut/telegram row
      (matched_existing → updates that row's source_ref),
    - already ingested by a previous Gmail scan (duplicate_gmail),
    - new and always inserted as `shadow` for manual review — the user
      approves or discards each one in "revisar correos",
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
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from typing import Mapping, Optional, Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.account import Account
from ...models.transaction import Transaction
from ..extraction.email_extractor import (
    EXPENSE_TYPES,
    INCOME_TYPES,
    ExtractedEmailTransaction,
)
from . import account_guess


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

# Retired as a write gate (operator decision 2026-06-08): Gmail-parsed
# transactions now ALWAYS land as `status='shadow'` for manual review,
# regardless of parser age (see the insert in `reconcile` /
# docs/gmail-shadow-review.md). The constant is retained only because the
# Telegram notifier still uses it to decide the cadence of its daily
# shadow-review summary message — it no longer gates the write decision.
SHADOW_WINDOW_DAYS = 7


class ReconcileOutcome(str, Enum):
    MATCHED_EXISTING = "matched_existing"
    CREATED_NEW = "created_new"
    CREATED_SHADOW = "created_shadow"
    DUPLICATE_GMAIL = "duplicate_gmail"
    SKIPPED_LOW_CONFIDENCE = "skipped_low_confidence"
    # The email body carried no transaction date. We never invent one (a
    # `date.today()` fallback would land the row after any reconciliation anchor
    # and double-count — decision #7 / Gate F). Held as skipped for the user to
    # add manually.
    SKIPPED_NO_DATE = "skipped_no_date"


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


async def _find_existing_gmail_sibling(
    *,
    db: AsyncSession,
    user_id: uuid.UUID,
    candidate: ExtractedEmailTransaction,
    signed: Decimal,
) -> Optional[Transaction]:
    """A prior GMAIL-origin row that looks like the SAME payment as this
    candidate — the cross-email duplicate case (one purchase notified by two
    emails with different Message-IDs, e.g. a 'compra' + an 'autorización').

    Distinct from `_check_duplicate_gmail` (same Message-ID) and
    `_find_existing_match` (only matches non-gmail rows). Same signed amount
    within tolerance + date within the match window; only `shadow`/`confirmed`
    rows count (a previously discarded row must not re-flag). Read-only.

    We FLAG, never skip (operator decision, mirroring the at-capture detector):
    the new row is still created so the user sees both in shadow review and
    discards one — a false positive must never silently drop a real charge.
    """
    cand_date = candidate.transaction_date
    if cand_date is None or candidate.currency is None:
        return None

    date_low = cand_date - timedelta(days=MATCH_WINDOW_DAYS)
    date_high = cand_date + timedelta(days=MATCH_WINDOW_DAYS)
    row = await db.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .where(Transaction.currency == candidate.currency)
        .where(Transaction.gmail_message_id.is_not(None))
        .where(Transaction.status.in_(("shadow", "confirmed")))
        .where(Transaction.transfer_id.is_(None))
        .where(Transaction.goal_id.is_(None))
        .where(
            and_(
                Transaction.transaction_date >= date_low,
                Transaction.transaction_date <= date_high,
            )
        )
        .where(func.abs(Transaction.amount - signed) <= AMOUNT_TOLERANCE)
        .order_by(Transaction.transaction_date.desc())
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
    accounts: Sequence[Account] = (),
    last_used: Optional[Mapping[uuid.UUID, date]] = None,
    bank_name: Optional[str] = None,
) -> tuple[ReconcileOutcome, Optional[Transaction]]:
    """Decide and act. Always commits-friendly — returns the row
    in-session; the caller commits.

    `accounts` / `last_used` / `bank_name` feed the best-effort account guess
    applied to a newly-created shadow row (the user reviews + can correct it in
    the native review screen). When omitted, the row keeps `account_id=NULL`,
    preserving the prior behavior.
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

    # Body-sourced date is mandatory. An undated email must NOT be stamped with
    # the ingestion date — that would land the row after any reconciliation
    # anchor and double-count (decision #7 / Gate F). Hold it as
    # skipped(no_date); the user adds the date manually. NEVER invent a date.
    if candidate.transaction_date is None:
        log.info(
            "reconcile_skipped_no_date user=%s type=%s amount=%s",
            user_id,
            candidate.transaction_type,
            candidate.amount,
        )
        return (ReconcileOutcome.SKIPPED_NO_DATE, None)

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

    # 4. New row. Gmail-parsed transactions ALWAYS land as shadow so the
    #    user reviews each one in "revisar correos" before it affects the
    #    ledger. The old 7-day auto-trust window was removed by operator
    #    decision 2026-06-08 (permanent review gate); see
    #    docs/gmail-shadow-review.md.
    status = "shadow"

    # Best-effort account guess. Deterministic and swallow-on-fail: a guess must
    # never break ingestion, so any error just leaves account_id NULL (→ the row
    # shows "Sin cuenta" in review, same as before this feature).
    guessed_account_id: Optional[uuid.UUID] = None
    try:
        guessed_account_id = account_guess.guess_account_id(
            accounts=accounts,
            last_used=last_used,
            currency=candidate.currency,
            transaction_type=candidate.transaction_type,
            bank_name=bank_name,
        )
    except Exception:  # noqa: BLE001 — a guess must never break a scan
        log.exception(
            "account_guess_failed user=%s msg=%s", user_id, gmail_message_id
        )

    # Cross-email duplicate flag (the same payment notified by two emails). We
    # FLAG the new row, never skip it (operator decision): both land in shadow
    # review where the user discards one. Best-effort — a detection error must
    # not break ingestion.
    is_dup = False
    try:
        sibling = await _find_existing_gmail_sibling(
            db=db, user_id=user_id, candidate=candidate, signed=signed
        )
        if sibling is not None:
            is_dup = True
            log.info(
                "reconcile_gmail_sibling_dup user=%s msg=%s sibling=%s",
                user_id,
                gmail_message_id,
                sibling.id,
            )
    except Exception:  # noqa: BLE001 — dup detection must never break a scan
        log.exception(
            "gmail_sibling_check_failed user=%s msg=%s", user_id, gmail_message_id
        )

    txn = Transaction(
        user_id=user_id,
        amount=signed,
        currency=candidate.currency or "CRC",
        merchant=candidate.merchant,
        description=candidate.description or _compose_description(
            email_subject, email_from
        ),
        transaction_date=candidate.transaction_date,
        source="gmail",
        gmail_message_id=gmail_message_id,
        status=status,
        account_id=guessed_account_id,
        is_duplicate=is_dup,
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
    return (ReconcileOutcome.CREATED_SHADOW, txn)


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

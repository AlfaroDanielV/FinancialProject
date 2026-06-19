"""Best-effort account guess for Gmail-ingested transactions.

Gmail bank-notification rows land with `account_id = NULL` (see
`reconciler.py`). This module makes a deterministic, *aggressive* guess at which
of the user's accounts a movement belongs to, so the native shadow-review screen
can pre-select it. The user still reviews and can correct the guess before
confirming (`GmailReviewScreen`), so the guess never blocks anything.

The LLM never picks the account — it only extracts amount/currency/type. This
layer is pure rules: a currency filter (so a ₡ charge can never land on a $
account), the existing deterministic `match_account_hint` for the bank name, and
account-type + recency tie-breakers.

Aggressive by operator decision (2026-06-16): currency is always respected, but
beyond that we always return a candidate when one exists in the right currency,
so almost nothing is left "Sin cuenta".
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Mapping, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.account import Account
from ...models.transaction import Transaction
from ..dispatch.lazy_detection import match_account_hint


# transaction_type → preferred account_type(s). Soft preference: if no account
# of the preferred type exists in the currency, we fall back to all candidates.
# Taxonomy from `extraction/email_extractor.py::TRANSACTION_TYPES`.
_PREFERRED_TYPES: dict[str, tuple[str, ...]] = {
    "charge": ("credit",),
    "fee": ("credit",),
    "withdrawal": ("checking",),
    "payment": ("checking",),
    "transfer": ("checking",),
    "deposit": ("checking", "savings"),
    "refund": ("credit", "checking"),
}


def bank_name_for_sender(
    from_addr: Optional[str], sender_bank: Mapping[str, Optional[str]]
) -> Optional[str]:
    """Resolve the bank label for an email's raw `From` header.

    `sender_bank` maps a bare lowercased sender email → its whitelist
    `bank_name`. The Gmail `From` header is a full RFC822 value
    ("BAC <notifica@bac.cr>"), so we substring-match the (≤8) whitelist emails
    against it rather than parsing the address.
    """
    if not from_addr:
        return None
    haystack = from_addr.lower()
    for email, bank in sender_bank.items():
        if email and email in haystack:
            return bank
    return None


async def load_guess_context(
    db: AsyncSession, user_id: uuid.UUID
) -> tuple[list[Account], dict[uuid.UUID, date]]:
    """Load everything the guess needs, once per scan: the user's active
    accounts and a ``{account_id: last_used_date}`` recency map (newest
    confirmed, account-bearing transaction per account)."""
    accounts = list(
        (
            await db.execute(
                select(Account).where(
                    Account.user_id == user_id,
                    Account.is_active.is_(True),
                    Account.archived.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    rows = (
        await db.execute(
            select(
                Transaction.account_id,
                func.max(Transaction.transaction_date),
            )
            .where(
                Transaction.user_id == user_id,
                Transaction.status == "confirmed",
                Transaction.account_id.is_not(None),
            )
            .group_by(Transaction.account_id)
        )
    ).all()
    last_used = {acc_id: last for acc_id, last in rows if acc_id is not None}
    return accounts, last_used


def guess_account_id(
    *,
    accounts: Sequence[Account],
    last_used: Optional[Mapping[uuid.UUID, date]] = None,
    currency: Optional[str],
    transaction_type: Optional[str],
    bank_name: Optional[str],
) -> Optional[uuid.UUID]:
    """Best-effort, deterministic, aggressive account pick.

    Returns an account id, or ``None`` only when the user has no active account
    in the row's currency (the genuinely un-guessable case — it stays "Sin
    cuenta" and falls into the existing orphan flow).
    """
    last_used = last_used or {}
    cur = (currency or "CRC").upper()
    cands = [a for a in accounts if (a.currency or "CRC").upper() == cur]
    if not cands:
        return None

    # 1. Confident bank-name match. Reuses the deterministic lazy-detection
    #    matcher against the currency-filtered candidates (e.g. sender "BAC" →
    #    the account named "BAC Visa").
    if bank_name:
        m = match_account_hint(bank_name, cands)
        if m.status == "matched" and m.account is not None:
            return m.account.id

    # 2. Narrow by the account-type the transaction_type implies (charge → a
    #    credit card, debit/withdrawal → checking, …). Soft: keep all
    #    candidates if none match the preference.
    preferred = _PREFERRED_TYPES.get((transaction_type or "").lower(), ())
    typed = [a for a in cands if a.account_type in preferred] or list(cands)

    # 3. Aggressive tie-break: most-recently-used, then most-recently-created.
    return max(
        typed,
        key=lambda a: (last_used.get(a.id) or date.min, a.created_at),
    ).id

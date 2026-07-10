"""Deterministic auto-classification at capture (category + envelope).

Operator ask 2026-07-06: every transaction capture — chat, REST, iPhone
Shortcut, Apple Pay, Gmail — does its best to classify the row against the
user's EXISTING categories and, for expenses, auto-assign an envelope.

Two deterministic sources, in order (locked by operator decision):

1. **History** — the most recent of the user's own rows with the same
   normalized merchant whose category/envelope was USER-SET (the auto flags
   fence out prior auto-assigns, so a wrong guess can never self-reinforce).
   This is the sanctioned "user-confirmed mapping" form of the deferred
   merchant→sobre memory — a per-capture lookup of the user's own confirmed
   rows, never a stored map, never a synonym table.
2. **Hint** — where an LLM `category_hint` / free-text category exists (chat,
   shortcut), an exact normalized-name match against the user's active
   `user_categories`, kind-filtered by the transaction's sign. Envelopes are
   NEVER assigned from a hint — history only.

Hard rules preserved: the LLM never decides a category or envelope (it only
supplies the free-text hint it always supplied); no synonym/normalization
maps; envelopes are expense-only; auto-assignment must never break a capture
(`apply_classification` swallows everything and logs loudly, mirroring
`flag_and_notify`).

Rows that are machine-managed or already categorized by design are excluded:
transfer legs, goal flows, reconciliation ajustes, loan cargos, and bill/debt
payment rows (those inherit their obligation's category/envelope).
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.envelope import Envelope
from ..models.transaction import Transaction
from ..models.user_category import UserCategory
from .categories import normalize_category_name

log = logging.getLogger("services.classification")

# How many most-recent merchant-matching rows we consider. The FIRST row with
# a user-set category / envelope wins (most recent = the user's latest word).
_HISTORY_LIMIT = 10

# Fuzzy fallback: how many recent user rows to scan in Python when the exact
# normalized-merchant match came up empty. Bounded so the hot capture path
# stays cheap; ordered date-desc so the first similar hit is the most recent.
_FUZZY_HISTORY_LIMIT = 200

# A merchant string this short can't safely drive a containment match (it would
# over-match — e.g. "bac" inside unrelated names).
_MIN_FUZZY_LEN = 4

_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]")


def _norm_merchant(raw: Optional[str]) -> str:
    """Same normalization as dedup's merchant matcher: lowercase, strip
    non-alphanumerics, collapse to a comparable token string."""
    return _NON_ALNUM_RE.sub("", (raw or "").lower()).strip()


def _merchant_close(a: str, b: str) -> bool:
    """Fuzzy equivalence of two ALREADY-normalized merchant strings, for
    carrying a category/envelope across capture sources whose merchant text
    differs (a bank email's "AMAZON MKTPL*1A2B" vs a manual "amazon").

    Deliberately STRICTER than dedup's booster (`_merchant_similar`): equality
    or containment only, never any-shared-token — a false match here silently
    mis-categorizes, whereas dedup's is only a tiebreaker. A length guard stops
    a tiny token from over-matching."""
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= _MIN_FUZZY_LEN and shorter in longer


def _as_decimal(value: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ClassificationSuggestion:
    """What auto-classification found. Either side may be None."""

    category_id: Optional[uuid.UUID] = None
    category_name: Optional[str] = None
    category_source: Optional[str] = None  # "history" | "hint"
    envelope_id: Optional[uuid.UUID] = None
    envelope_name: Optional[str] = None  # for the chat chip / Telegram note


def is_classifiable(txn: Transaction) -> bool:
    """Rows auto-classification may touch: not machine-managed, not a
    reserved-category correction row. Bill/debt payment rows never reach the
    classifier (their writers inherit the obligation's category/envelope and
    don't call it), so source-level checks here cover the rest."""
    from .anchors import AJUSTE_CATEGORY  # local import — avoids a cycle

    if txn.transfer_id is not None or txn.goal_id is not None:
        return False
    if txn.source == "loan_cargo":
        return False
    if (txn.category or "").strip().lower() == AJUSTE_CATEGORY:
        return False
    return not txn.archived


async def _history_match(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    merchant: Optional[str],
    is_expense: bool,
    exclude_id: Optional[uuid.UUID],
) -> tuple[Optional[Transaction], Optional[Transaction]]:
    """Most recent same-merchant rows carrying a USER-SET category and a
    USER-SET envelope, respectively. An exact normalized-merchant equality
    pass runs first in SQL (cheap, precise); if either side is still empty, a
    bounded fuzzy pass (equality/containment on the normalized text) fills the
    gap so history carries across capture sources whose merchant strings differ
    (a bank email's noisy merchant vs a manually-typed one)."""
    norm = _norm_merchant(merchant)
    if not norm:
        return (None, None)

    from .anchors import AJUSTE_CATEGORY  # local import — avoids a cycle

    sign_filter = Transaction.amount < 0 if is_expense else Transaction.amount > 0
    # Shared filters for both the exact and the fuzzy pass. Only USER-SET
    # (non-auto) category/envelope rows are ever a source, so a prior auto
    # guess can't self-reinforce.
    base_filters = [
        Transaction.user_id == user_id,
        Transaction.status == "confirmed",
        Transaction.archived.is_(False),
        Transaction.is_duplicate.is_(False),
        Transaction.transfer_id.is_(None),
        Transaction.goal_id.is_(None),
        Transaction.source != "loan_cargo",
        Transaction.category.is_distinct_from(AJUSTE_CATEGORY),
        Transaction.merchant.isnot(None),
        sign_filter,
    ]

    def _pick(rows: list[Transaction], predicate) -> Optional[Transaction]:
        return next((r for r in rows if predicate(r)), None)

    def _has_category(r: Transaction) -> bool:
        return r.category_id is not None and not r.category_auto_assigned

    def _has_envelope(r: Transaction) -> bool:
        return r.envelope_id is not None and not r.envelope_auto_assigned

    # ── exact pass: normalized-merchant equality in SQL (cheap, precise) ──
    norm_expr = func.btrim(
        func.regexp_replace(
            func.lower(Transaction.merchant), r"[^a-z0-9 ]", "", "g"
        )
    )
    exact_stmt = (
        select(Transaction)
        .where(*base_filters, norm_expr == norm)
        .order_by(
            Transaction.transaction_date.desc(), Transaction.created_at.desc()
        )
        .limit(_HISTORY_LIMIT)
    )
    if exclude_id is not None:
        exact_stmt = exact_stmt.where(Transaction.id != exclude_id)

    rows = list((await db.execute(exact_stmt)).scalars().all())
    category_row = _pick(rows, _has_category)
    envelope_row = _pick(rows, _has_envelope)

    # ── fuzzy fallback: only for the sides the exact pass left empty ──
    # Bank-email merchant text rarely normalizes to the same token as a
    # manually-typed merchant, so exact equality misses cross-source history.
    # Scan the most recent user-set rows and carry over the closest one.
    if category_row is None or envelope_row is None:
        fuzzy_stmt = (
            select(Transaction)
            .where(*base_filters)
            .order_by(
                Transaction.transaction_date.desc(),
                Transaction.created_at.desc(),
            )
            .limit(_FUZZY_HISTORY_LIMIT)
        )
        if exclude_id is not None:
            fuzzy_stmt = fuzzy_stmt.where(Transaction.id != exclude_id)

        for r in (await db.execute(fuzzy_stmt)).scalars().all():
            if not _merchant_close(_norm_merchant(r.merchant), norm):
                continue
            if category_row is None and _has_category(r):
                category_row = r
            if envelope_row is None and _has_envelope(r):
                envelope_row = r
            if category_row is not None and envelope_row is not None:
                break

    return (category_row, envelope_row)


async def _category_from_hint(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    hint: Optional[str],
    is_expense: bool,
) -> Optional[UserCategory]:
    """Exact normalized-name match of a free-text hint against the user's
    active categories, kind-filtered by sign. No fuzzy, no synonyms — the
    hint either names an existing category or it doesn't."""
    if not hint or not hint.strip():
        return None
    kinds = ("expense", "both") if is_expense else ("income", "both")
    row = (
        await db.execute(
            select(UserCategory).where(
                UserCategory.user_id == user_id,
                UserCategory.archived.is_(False),
                UserCategory.kind.in_(kinds),
                func.lower(UserCategory.name)
                == normalize_category_name(hint),
            )
        )
    ).scalar_one_or_none()
    return row


async def _category_by_id(
    db: AsyncSession, *, user_id: uuid.UUID, category_id: uuid.UUID
) -> Optional[UserCategory]:
    return (
        await db.execute(
            select(UserCategory).where(
                UserCategory.id == category_id,
                UserCategory.user_id == user_id,
                UserCategory.archived.is_(False),
            )
        )
    ).scalar_one_or_none()


async def suggest_classification(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    merchant: Optional[str],
    amount: Any,
    category_hint: Optional[str] = None,
    exclude_id: Optional[uuid.UUID] = None,
) -> ClassificationSuggestion:
    """Read-only: what would we classify this as? History wins over hint for
    the category; the envelope comes from history only (expenses only)."""
    amt = _as_decimal(amount)
    if amt is None or amt == 0:
        return ClassificationSuggestion()
    is_expense = amt < 0

    category_row, envelope_row = await _history_match(
        db,
        user_id=user_id,
        merchant=merchant,
        is_expense=is_expense,
        exclude_id=exclude_id,
    )

    category_id: Optional[uuid.UUID] = None
    category_name: Optional[str] = None
    category_source: Optional[str] = None
    if category_row is not None:
        # Re-validate the FK still points at an active category (it may have
        # been archived since); fall through to the hint if not.
        cat = await _category_by_id(
            db, user_id=user_id, category_id=category_row.category_id
        )
        if cat is not None:
            category_id, category_name, category_source = (
                cat.id,
                cat.name,
                "history",
            )
    if category_id is None:
        cat = await _category_from_hint(
            db, user_id=user_id, hint=category_hint, is_expense=is_expense
        )
        if cat is not None:
            category_id, category_name, category_source = cat.id, cat.name, "hint"

    envelope_id: Optional[uuid.UUID] = None
    envelope_name: Optional[str] = None
    if is_expense and envelope_row is not None:
        from .envelopes import can_assign_transaction_to_envelope

        try:
            ok = await can_assign_transaction_to_envelope(
                db, user_id=user_id, envelope_id=envelope_row.envelope_id
            )
        except Exception:  # noqa: BLE001 — a suggestion must never raise
            ok = False
        if ok:
            env = (
                await db.execute(
                    select(Envelope).where(
                        Envelope.id == envelope_row.envelope_id
                    )
                )
            ).scalar_one_or_none()
            if env is not None:
                envelope_id, envelope_name = env.id, env.name

    return ClassificationSuggestion(
        category_id=category_id,
        category_name=category_name,
        category_source=category_source,
        envelope_id=envelope_id,
        envelope_name=envelope_name,
    )


async def apply_classification(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    txn: Transaction,
    category_hint: Optional[str] = None,
) -> ClassificationSuggestion:
    """Fill NULL classification fields on an un-flushed/un-committed row.

    Contract (mirrors `flag_and_notify`): best-effort, NEVER raises, mutates
    the in-session object only — the caller owns flush/commit. Values the
    client explicitly set are never overwritten:
      - `category_id` already set → category untouched, no flag.
      - free-text `category` present → treated as user-provided text; if it
        resolves to an existing category the FK is linked WITHOUT the auto
        flag (pure dual-field reconciliation, the visible name doesn't
        change); history never overrides provided text.
      - `envelope_id` already set → envelope untouched.

    `category_hint` is a MACHINE-produced label (e.g. the Gmail email
    extractor's `category_hint`) as opposed to the user-confirmed `txn.category`
    text. It is used only when the row has no user-provided category text and
    no history match; a match against an existing category is flagged auto (so
    it shows a "sugerida" tag, is one-tap correctable, and does not seed future
    history). Envelopes are never assigned from it — history only.

    Returns what was applied (for the chat hint / logging)."""
    try:
        if not is_classifiable(txn):
            return ClassificationSuggestion()

        amt = _as_decimal(txn.amount)
        if amt is None or amt == 0:
            return ClassificationSuggestion()
        is_expense = amt < 0

        provided_text = (txn.category or "").strip() or None
        want_category = txn.category_id is None
        want_envelope = is_expense and txn.envelope_id is None
        if not want_category and not want_envelope:
            return ClassificationSuggestion()

        applied_category_id: Optional[uuid.UUID] = None
        applied_category_name: Optional[str] = None
        applied_category_source: Optional[str] = None
        if want_category and provided_text is not None:
            # The caller supplied text (LLM hint / Shortcut field). If it
            # names an existing category, link the FK without the auto flag
            # (pure dual-field reconciliation — the visible name the user
            # confirmed doesn't change) and settle the category here so the
            # history lookup below can't contradict a text that matched.
            cat = await _category_from_hint(
                db, user_id=user_id, hint=provided_text, is_expense=is_expense
            )
            if cat is not None:
                txn.category_id = cat.id
                applied_category_id = cat.id
                applied_category_name = cat.name
                applied_category_source = "hint"
                want_category = False

        suggestion = ClassificationSuggestion()
        if want_category or want_envelope:
            suggestion = await suggest_classification(
                db,
                user_id=user_id,
                merchant=txn.merchant,
                amount=amt,
                category_hint=category_hint if want_category else None,
                exclude_id=txn.id,
            )

        if want_category and suggestion.category_id is not None:
            # History (or machine `category_hint`) classification. When the
            # caller's free text didn't map to any category, the user's own
            # precedent for this merchant — else the extractor's hint — is the
            # better signal. Set both name + FK and flag it auto so the UI tags
            # it, one tap corrects it, and it doesn't seed future history.
            txn.category_id = suggestion.category_id
            txn.category = suggestion.category_name
            txn.category_auto_assigned = True
            applied_category_id = suggestion.category_id
            applied_category_name = suggestion.category_name
            applied_category_source = suggestion.category_source

        applied_envelope_id: Optional[uuid.UUID] = None
        applied_envelope_name: Optional[str] = None
        if want_envelope and suggestion.envelope_id is not None:
            txn.envelope_id = suggestion.envelope_id
            txn.envelope_auto_assigned = True
            applied_envelope_id = suggestion.envelope_id
            applied_envelope_name = suggestion.envelope_name

        return ClassificationSuggestion(
            category_id=applied_category_id,
            category_name=applied_category_name,
            category_source=applied_category_source,
            envelope_id=applied_envelope_id,
            envelope_name=applied_envelope_name,
        )
    except Exception:  # noqa: BLE001 — classification must never break capture
        log.exception(
            "auto_classification_failed user=%s merchant=%s",
            user_id,
            getattr(txn, "merchant", None),
        )
        return ClassificationSuggestion()

"""Telegram notifier for Gmail scan outcomes (Block C.1).

Replaces the inline `_finish_message` from backfill.py. Centralises:
    - the "started" handshake before a scan
    - the post-scan branching (revoked / no-whitelist / no-results /
      shadow / batch / per-transaction)
    - the shadow-window accumulator (Redis set per user per day)
    - the daily shadow summary the worker sends at 8am CR

Telegram delivery is best-effort: any failure is logged and swallowed
so the scan itself never crashes because the user couldn't be reached.

Decisions: see docs/phase-6b-decisions.md (entries 2026-05-06
"Batching de notificaciones" and "Shadow summary cadence").
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Literal, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models.gmail_credential import GmailCredential
from ...models.transaction import Transaction
from ...models.user import User
from ...redis_client import get_redis
from .reconciler import SHADOW_WINDOW_DAYS
from .scanner import RunMode, ScanResult

# Late-imported strings keep this module decoupled from `bot/messages_es`
# at import time (the bot package may import services). We re-import
# inside the function to dodge any cycle drift in the future.

log = logging.getLogger("api.services.gmail.notifier")


# ── small helpers ───────────────────────────────────────────────────────────


def _today_iso(*, tz: timezone = timezone.utc) -> str:
    return datetime.now(tz).date().isoformat()


def _format_amount(amount: Decimal | float, currency: str | None) -> str:
    """User-facing money. ₡ for CRC, $ for USD, neutral otherwise."""
    sign_unsigned = abs(Decimal(amount))
    if currency == "CRC":
        return f"₡{sign_unsigned:,.0f}"
    if currency == "USD":
        return f"${sign_unsigned:,.2f}"
    return f"{sign_unsigned:,.2f} {currency or ''}".strip()


async def _resolve_chat_id(
    *, user_id: uuid.UUID, db: AsyncSession
) -> Optional[int]:
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None or user.telegram_user_id is None:
        return None
    return user.telegram_user_id


async def _is_in_shadow_window(
    *, user_id: uuid.UUID, db: AsyncSession
) -> bool:
    cred = (
        await db.execute(
            select(GmailCredential).where(GmailCredential.user_id == user_id)
        )
    ).scalar_one_or_none()
    if cred is None or cred.activated_at is None:
        return True  # default to shadow when in doubt
    elapsed = datetime.now(timezone.utc) - cred.activated_at
    return elapsed.days < SHADOW_WINDOW_DAYS


async def _send(*, chat_id: int, text: str) -> None:
    """Best-effort send. If the bot module can't be imported (tests run
    without bot init) or the bot raises, log + drop. The scanner cannot
    afford to crash because Telegram is flaky."""
    try:
        from bot.app import get_bot

        try:
            bot = get_bot()
        except RuntimeError:
            # TELEGRAM_MODE=disabled in dev/CI. Log so the test can assert
            # it ever fired, but no real network.
            log.info("notifier_send_skipped reason=bot_not_initialized")
            return
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception:
        log.exception("notifier_send_failed chat_id=%s", chat_id)


# ── shadow accumulator ─────────────────────────────────────────────────────


async def _append_shadow_ids(
    *,
    user_id: uuid.UUID,
    transaction_ids: Iterable[uuid.UUID],
    date_iso: Optional[str] = None,
) -> None:
    """Append IDs to today's shadow summary set. Called by
    notify_run_completed when a scan ran in shadow window."""
    from bot.redis_keys import (
        GMAIL_SHADOW_SUMMARY_TTL_S,
        gmail_shadow_summary_key,
    )

    redis = get_redis()
    key = gmail_shadow_summary_key(user_id, date_iso or _today_iso())
    ids = [str(i) for i in transaction_ids]
    if not ids:
        return
    # SADD is idempotent — re-running won't duplicate. Then EXPIRE to
    # set/refresh the TTL.
    await redis.sadd(key, *ids)
    await redis.expire(key, GMAIL_SHADOW_SUMMARY_TTL_S)


async def _read_shadow_ids(
    *, user_id: uuid.UUID, date_iso: str
) -> list[uuid.UUID]:
    from bot.redis_keys import gmail_shadow_summary_key

    redis = get_redis()
    raw = await redis.smembers(gmail_shadow_summary_key(user_id, date_iso))
    out = []
    for s in raw or []:
        try:
            out.append(uuid.UUID(s))
        except (TypeError, ValueError):
            continue
    return out


async def _clear_shadow_ids(
    *, user_id: uuid.UUID, date_iso: str
) -> None:
    from bot.redis_keys import gmail_shadow_summary_key

    redis = get_redis()
    await redis.delete(gmail_shadow_summary_key(user_id, date_iso))


# ── public API: notify_run_started ──────────────────────────────────────────


async def notify_run_started(
    *,
    user_id: uuid.UUID,
    days: int,
    mode: RunMode,
    db: AsyncSession,
) -> None:
    """Send the "starting" handshake. Manual mode gets a different copy
    because the user is sitting at the chat waiting; daily/backfill get
    the same text."""
    chat_id = await _resolve_chat_id(user_id=user_id, db=db)
    if chat_id is None:
        return

    if mode == "manual":
        text = (
            f"Revisando los últimos {days} días… (corrida manual). "
            f"Te aviso al final."
        )
    else:
        text = (
            f"Empecé a revisar tus correos de los últimos {days} días. "
            f"Te aviso cuando termine — puede tardar unos minutos."
        )
    await _send(chat_id=chat_id, text=text)


# ── public API: notify_run_completed ────────────────────────────────────────


async def notify_run_completed(
    *,
    user_id: uuid.UUID,
    result: ScanResult,
    db: AsyncSession,
) -> None:
    """Top-level dispatch. Picks one of:
        revoked → "Se desconectó tu Gmail..."
        no_whitelist → "No tengo bancos en tu whitelist..."
        in shadow window + (backfill | daily) → roll-up "Listo, revisé X..."
                          + accumulate IDs to today's shadow set
        in shadow window + manual → same as above (still inform)
        outside shadow + 0 created → "Listo, nada nuevo" (only manual/backfill)
        outside shadow + > threshold → batch summary
        outside shadow + ≤ threshold → per-transaction messages
    """
    from bot import messages_es

    chat_id = await _resolve_chat_id(user_id=user_id, db=db)
    if chat_id is None:
        log.info("notify_skipped user=%s reason=no_chat", user_id)
        return

    # Branch 1: revoked
    if result.revoked:
        await _send(chat_id=chat_id, text=messages_es.GMAIL_SCAN_INVALID_GRANT)
        return

    # Branch 2: no whitelist
    if result.no_whitelist:
        await _send(chat_id=chat_id, text=messages_es.GMAIL_SCAN_NO_WHITELIST)
        return

    # Branch 3: 0 messages scanned
    if result.messages_scanned == 0:
        if result.mode == "backfill":
            await _send(
                chat_id=chat_id,
                text=messages_es.GMAIL_SCAN_NO_RESULTS_FIRST_BACKFILL,
            )
            return
        if result.mode == "manual":
            await _send(
                chat_id=chat_id,
                text=messages_es.GMAIL_SCAN_NO_RESULTS_MANUAL,
            )
            return
        # daily mode + 0 messages: silent (the user wasn't expecting anything)
        return

    # Branch 4: in shadow window
    in_shadow = await _is_in_shadow_window(user_id=user_id, db=db)
    if in_shadow:
        # Accumulate IDs for tomorrow's daily summary, regardless of mode.
        await _append_shadow_ids(
            user_id=user_id, transaction_ids=result.created_transaction_ids
        )
        # Backfill / daily get the rolled-up "esta semana" reminder.
        # Manual mode also gets this text — they asked for the scan and
        # we tell them what happened.
        await _send(
            chat_id=chat_id,
            text=messages_es.GMAIL_SCAN_FINISH_SHADOW_TPL.format(
                scanned=result.messages_scanned,
                matched=result.transactions_matched,
                created=result.transactions_created,
            ),
        )
        return

    # Branch 5: outside shadow, 0 created
    if result.transactions_created == 0:
        await _send(
            chat_id=chat_id,
            text=messages_es.GMAIL_SCAN_FINISH_QUIET_TPL.format(
                scanned=result.messages_scanned,
                matched=result.transactions_matched,
                created=0,
            ),
        )
        return

    # Branch 6: outside shadow, > threshold → batch summary
    threshold = settings.gmail_batch_threshold
    if result.transactions_created > threshold:
        await _send_batch_summary(
            chat_id=chat_id,
            user_id=user_id,
            result=result,
            db=db,
        )
        return

    # Branch 7: outside shadow, ≤ threshold → per-transaction
    await _send_per_transaction(
        chat_id=chat_id,
        result=result,
        db=db,
    )


async def _send_batch_summary(
    *,
    chat_id: int,
    user_id: uuid.UUID,
    result: ScanResult,
    db: AsyncSession,
) -> None:
    from bot import messages_es

    txns = await _fetch_created_transactions(
        db=db, ids=result.created_transaction_ids
    )
    top_n = 3
    head = txns[:top_n]
    rest = len(txns) - len(head)
    lines = []
    for t in head:
        amt = _format_amount(t.amount, t.currency)
        merchant = t.merchant or t.description or "—"
        lines.append(
            messages_es.GMAIL_SHADOW_SUMMARY_ITEM_TPL.format(
                amount=amt, merchant_or_desc=merchant
            )
        )
    tail = (
        messages_es.GMAIL_SHADOW_SUMMARY_TAIL_TPL.format(n=rest)
        if rest > 0
        else ""
    )
    await _send(
        chat_id=chat_id,
        text=messages_es.GMAIL_SCAN_FINISH_BATCH_TPL.format(
            scanned=result.messages_scanned,
            created=result.transactions_created,
            lines="\n".join(lines),
            tail=tail,
        ),
    )


async def _send_per_transaction(
    *,
    chat_id: int,
    result: ScanResult,
    db: AsyncSession,
) -> None:
    from bot import messages_es

    txns = await _fetch_created_transactions(
        db=db, ids=result.created_transaction_ids
    )
    for t in txns:
        amt = _format_amount(t.amount, t.currency)
        merchant = (t.merchant or "").strip()
        is_income = Decimal(t.amount) > 0
        if is_income:
            text = (
                messages_es.GMAIL_TXN_DETECTED_INCOME_TPL.format(
                    amount=amt, origin=merchant
                )
                if merchant
                else messages_es.GMAIL_TXN_DETECTED_INCOME_NO_ORIGIN_TPL.format(
                    amount=amt
                )
            )
        else:
            text = (
                messages_es.GMAIL_TXN_DETECTED_EXPENSE_TPL.format(
                    amount=amt, merchant=merchant
                )
                if merchant
                else messages_es.GMAIL_TXN_DETECTED_EXPENSE_NO_MERCHANT_TPL.format(
                    amount=amt
                )
            )
        await _send(chat_id=chat_id, text=text)


async def _fetch_created_transactions(
    *, db: AsyncSession, ids: list[uuid.UUID]
) -> list[Transaction]:
    if not ids:
        return []
    rows = await db.execute(
        select(Transaction)
        .where(Transaction.id.in_(ids))
        .order_by(Transaction.transaction_date.desc(), Transaction.amount.asc())
    )
    return list(rows.scalars().all())


# ── shadow daily summary (called by daily worker) ──────────────────────────


async def maybe_send_shadow_summary(
    *,
    user_id: uuid.UUID,
    db: AsyncSession,
    target_date: Optional[date] = None,
) -> bool:
    """Send the previous day's shadow accumulator if the user is still
    in the shadow window. Returns True if a message was sent.

    `target_date` defaults to yesterday (UTC). Tests can pin a specific
    date. The Redis key is read for that date and CLEARED after a
    successful send so re-runs don't double-send.
    """
    from bot import messages_es

    chat_id = await _resolve_chat_id(user_id=user_id, db=db)
    if chat_id is None:
        return False
    if not await _is_in_shadow_window(user_id=user_id, db=db):
        return False

    target = target_date or (datetime.now(timezone.utc).date() - timedelta(days=1))
    date_iso = target.isoformat()

    ids = await _read_shadow_ids(user_id=user_id, date_iso=date_iso)
    if not ids:
        return False

    txns = await _fetch_created_transactions(db=db, ids=ids)
    # Filter to those still in shadow status. A user might have approved
    # mid-cycle; we don't want to surface those.
    still_shadow = [t for t in txns if t.status == "shadow"]
    if not still_shadow:
        await _clear_shadow_ids(user_id=user_id, date_iso=date_iso)
        return False

    top_n = 3
    head = still_shadow[:top_n]
    rest = len(still_shadow) - len(head)

    item_lines = [
        messages_es.GMAIL_SHADOW_SUMMARY_ITEM_TPL.format(
            amount=_format_amount(t.amount, t.currency),
            merchant_or_desc=t.merchant or t.description or "—",
        )
        for t in head
    ]
    body = (
        messages_es.GMAIL_SHADOW_SUMMARY_HEADER_TPL.format(
            count=len(still_shadow)
        )
        + "\n"
        + "\n".join(item_lines)
    )
    if rest > 0:
        body += messages_es.GMAIL_SHADOW_SUMMARY_TAIL_TPL.format(n=rest)
    body += messages_es.GMAIL_SHADOW_SUMMARY_FOOTER

    await _send(chat_id=chat_id, text=body)
    await _clear_shadow_ids(user_id=user_id, date_iso=date_iso)
    return True

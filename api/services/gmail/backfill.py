"""Async backfill runner — orchestrates a full Gmail scan for a user.

Public surface:
    run_backfill          — the worker. Single user, one scan, notifies
                            Telegram at start and end. Raises on errors.
    run_backfill_safe     — thin wrapper that catches every exception
                            and tries to notify the user instead of
                            crashing the parent task.
    enqueue_backfill      — fire-and-forget asyncio.create_task helper.
                            Called by the activate handler in
                            bot/gmail_handlers.py.

The runner opens its own AsyncSession because it runs in a background
task, decoupled from any request handler's session lifetime.

Notifier wiring (Block C.1+): start + finish messages are delegated to
`api.services.gmail.notifier`. The notifier handles all branching
(shadow window, batching threshold, per-transaction).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from ...database import AsyncSessionLocal
from . import notifier
from .scanner import RunMode, ScanResult, scan_user_inbox


log = logging.getLogger("api.services.gmail.backfill")


_DEFAULT_BACKFILL_DAYS = 30
_DEFAULT_MANUAL_DAYS = 2


# ── run_backfill ────────────────────────────────────────────────────────────


async def run_backfill(
    *,
    user_id: uuid.UUID,
    days: int = _DEFAULT_BACKFILL_DAYS,
    mode: RunMode = "backfill",
) -> ScanResult:
    """Execute one scan-and-notify pass for `user_id`. Caller-side
    sessions are NOT shared — backfills always create their own.

    Raises on unhandled exceptions in the scanner's transport / SQL.
    Use `run_backfill_safe` if you don't want to manage exceptions
    yourself (the on_activate_callback does).
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    log.info(
        "backfill_starting user=%s days=%d mode=%s",
        user_id,
        days,
        mode,
    )

    # Three sessions: start / scan / finish. Splitting them means the
    # start message lands BEFORE the scan starts long-polling Gmail
    # (helpful for 30s+ runs), and the finish message uses a fresh
    # session in case the scan rolled back.
    async with AsyncSessionLocal() as db:
        await notifier.notify_run_started(
            user_id=user_id, days=days, mode=mode, db=db
        )

    async with AsyncSessionLocal() as db:
        result = await scan_user_inbox(
            user_id=user_id,
            since=since,
            until=None,
            mode=mode,
            db=db,
        )

    log.info(
        "backfill_done user=%s scanned=%d created=%d matched=%d skipped=%d revoked=%s errors=%d",
        user_id,
        result.messages_scanned,
        result.transactions_created,
        result.transactions_matched,
        result.transactions_skipped,
        result.revoked,
        len(result.errors),
    )

    async with AsyncSessionLocal() as db:
        await notifier.notify_run_completed(
            user_id=user_id, result=result, db=db
        )

    return result


async def run_backfill_safe(
    *,
    user_id: uuid.UUID,
    days: int = _DEFAULT_BACKFILL_DAYS,
    mode: RunMode = "backfill",
) -> Optional[ScanResult]:
    """Crash-safe wrapper. Use from `asyncio.create_task` in the handler.

    Catches every exception that escapes `run_backfill`, logs it, and
    tries to notify the user with a generic apology. Returns None on
    failure, the ScanResult on success.

    Why not let the exception propagate: a `create_task` exception
    becomes "Task exception was never retrieved" in the logs and the
    user gets nothing. Per the diagnostic earlier today, this is
    EXACTLY the silent-failure mode we wrote down to avoid.
    """
    try:
        return await run_backfill(user_id=user_id, days=days, mode=mode)
    except Exception:
        log.exception("backfill_failed user=%s mode=%s", user_id, mode)
        try:
            async with AsyncSessionLocal() as db:
                # Reach into the notifier's helpers directly — we don't
                # have a ScanResult to pass to notify_run_completed.
                chat_id = await notifier._resolve_chat_id(
                    user_id=user_id, db=db
                )
                if chat_id is not None:
                    await notifier._send(
                        chat_id=chat_id,
                        text=(
                            "Algo salió mal revisando tus correos. "
                            "Vamos a reintentar mañana en la corrida "
                            "automática."
                        ),
                    )
        except Exception:
            log.exception(
                "backfill_failed_notify_also_failed user=%s", user_id
            )
        return None


def enqueue_backfill(
    *,
    user_id: uuid.UUID,
    days: int = _DEFAULT_BACKFILL_DAYS,
    mode: RunMode = "backfill",
) -> asyncio.Task:
    """Fire-and-forget. Returns the Task so callers can keep a handle
    if they want (e.g. testing) but the production path discards it.

    The Task carries a reference to itself in the asyncio loop so
    Python's GC won't collect it mid-flight. We don't track these
    centrally; the Task lives as long as the run takes (~minutes).
    """
    coro = run_backfill_safe(user_id=user_id, days=days, mode=mode)
    task = asyncio.create_task(coro, name=f"gmail-backfill-{user_id}-{mode}")
    log.info("backfill_enqueued user=%s days=%d mode=%s", user_id, days, mode)
    return task


# ── manual / daily presets ───────────────────────────────────────────────────


def enqueue_manual_scan(*, user_id: uuid.UUID) -> asyncio.Task:
    """User-triggered scan (e.g. /revisar_correos). Shorter window — we
    only care about the last couple of days to surface fresh stuff."""
    return enqueue_backfill(
        user_id=user_id, days=_DEFAULT_MANUAL_DAYS, mode="manual"
    )

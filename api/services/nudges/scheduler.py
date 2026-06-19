"""In-process nudge scheduler.

Drives the Phase 5d proactive-messaging pipeline on an interval so nudges fire
during normal operation without an external cron. Runs the same fan-out the
standalone worker exposes (`workers.nudges_daily.run_nudges_for_all_users`).

One background asyncio task, started from the API lifespan AFTER the bot
(`telegram_send_fn` needs the aiogram Bot singleton) and cancelled on shutdown.
The tick body is wrapped so an exception is logged and the loop survives —
"async tasks fail silently without explicit try/except" is a project hard rule.

Gated on `settings.nudge_scheduler_enabled`; interval + initial delay are config.
Anti-saturation (rate limit / silence / quiet hours / dedup) lives downstream in
the delivery worker, so the loop never has to reason about pacing.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from api.config import settings


log = logging.getLogger("nudges.scheduler")

_task: Optional[asyncio.Task] = None


async def run_once_safe() -> None:
    """One evaluate→deliver fan-out over all active users. Never raises — logs
    and returns. This is the unit the loop calls each tick (and what tests
    exercise without spinning the infinite loop)."""
    try:
        from workers.nudges_daily import run_nudges_for_all_users

        await run_nudges_for_all_users()
    except Exception:
        log.exception("nudge_scheduler_tick_failed")


async def _loop(interval_s: int, initial_delay_s: int) -> None:
    log.info(
        "nudge_scheduler_started interval_s=%d initial_delay_s=%d",
        interval_s,
        initial_delay_s,
    )
    try:
        await asyncio.sleep(initial_delay_s)
        while True:
            await run_once_safe()
            await asyncio.sleep(interval_s)
    except asyncio.CancelledError:
        log.info("nudge_scheduler_stopped")
        raise


def start_nudge_scheduler() -> None:
    """Start the background loop if enabled and not already running. Must be
    called from inside a running event loop (the API lifespan)."""
    global _task
    if not settings.nudge_scheduler_enabled:
        log.info("nudge_scheduler_disabled")
        return
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(
        _loop(
            settings.nudge_scheduler_interval_s,
            settings.nudge_scheduler_initial_delay_s,
        )
    )


async def stop_nudge_scheduler() -> None:
    """Cancel the loop and await its exit. Safe to call when never started."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None

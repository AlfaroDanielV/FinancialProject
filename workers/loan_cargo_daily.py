"""Standalone loan-cargo worker (runs WEEKLY; the `_daily` in the module name is
just the sibling-worker naming convention — the cadence is set by the cron).

Executed in production as an Azure Container Apps Job (cron `0 11 * * 1 UTC` =
Mon 5am Costa Rica). The cuota is a monthly event, so weekly is plenty and
`post_due_loan_cargos` is cadence-robust (most-recent-due anchor), so an
end-of-month due day is still caught the following week. Run manually for
testing with:

    uv run python -m workers.loan_cargo_daily

For every user who has at least one active loan linked to a credit card
(`debts.charge_to_account_id IS NOT NULL`), it posts this month's cuota onto the
card (a `source='loan_cargo'` charge → the card's total due rises and it counts
as a gasto) and records a `DebtPayment` (loan balance ↓). The banks usually do
NOT email a cargo automático, so the app is the system of record. The post is
idempotent (catch-up safe within the month, never backfills prior cycles).

Per-user exceptions are swallowed + logged so one bad user doesn't kill the run.
The job exits NON-ZERO when something systemic is detected (an `_is_systemic`
import/config failure re-raises immediately; a finished-but-entirely-failed run
raises) so the orchestrator retries and the run shows Failed instead of a silent
green no-op. A run with zero eligible users exits 0 (nothing to do).

Unlike the Gmail worker, this one touches only the DB — no Telegram bot, no
secret store, no Redis — so there are no network singletons to close.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import AsyncSessionLocal
from api.logging_config import setup_logging
from api.models.debt import Debt
from api.services.loan_cargo import post_due_loan_cargos


log = logging.getLogger("workers.loan_cargo_daily")


# Failure classes that are NOT user-specific: they recur identically for every
# user, so swallowing them per-user would let the job report Succeeded while
# charging nobody. (Mirrors the gmail_daily lesson.)
_SYSTEMIC_EXC = (ImportError,)


def _is_systemic(exc: BaseException) -> bool:
    """True if `exc` (or anything in its cause/context chain) is a process-wide
    failure that will hit every user."""
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, _SYSTEMIC_EXC):
            return True
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return False


def _raise_if_all_failed(*, ok: int, failed: int) -> None:
    """A finished-but-entirely-failed run is almost always systemic — raise so
    the job exits non-zero instead of silently reporting success."""
    total = ok + failed
    if total > 0 and ok == 0:
        raise RuntimeError(
            f"daily loan-cargo run failed for all {total} user(s) — treating as "
            f"systemic and failing the job"
        )


async def _eligible_user_ids(db: AsyncSession) -> list:
    rows = await db.execute(
        select(Debt.user_id)
        .where(
            Debt.is_active.is_(True),
            Debt.archived.is_(False),
            Debt.charge_to_account_id.is_not(None),
        )
        .distinct()
    )
    return [r[0] for r in rows.fetchall()]


async def _post_one_user(*, user_id) -> bool:
    """Post due cargos for a single user. Returns True on success, False on a
    swallowed per-user failure. Systemic failures (`_is_systemic`) re-raise so
    the whole job fails loudly. `today` defaults inside the service to the user's
    local date (tz-correct), not the worker's UTC."""
    try:
        async with AsyncSessionLocal() as db:
            posted = await post_due_loan_cargos(db, user_id=user_id)
            await db.commit()
        log.info("loan_cargo_done user=%s posted=%d", user_id, len(posted))
        return True
    except Exception as exc:
        if _is_systemic(exc):
            log.error(
                "loan_cargo_systemic_error user=%s — aborting run", user_id
            )
            raise
        log.exception("loan_cargo_error user=%s", user_id)
        return False


async def run_daily_for_all_users() -> None:
    async with AsyncSessionLocal() as db:
        user_ids = await _eligible_user_ids(db)

    log.info("loan_cargo_run_started users=%d", len(user_ids))

    ok = 0
    failed = 0
    for user_id in user_ids:
        if await _post_one_user(user_id=user_id):
            ok += 1
        else:
            failed += 1

    log.info(
        "loan_cargo_run_completed users=%d ok=%d failed=%d",
        len(user_ids),
        ok,
        failed,
    )
    _raise_if_all_failed(ok=ok, failed=failed)


async def main() -> None:
    setup_logging("INFO")
    await run_daily_for_all_users()


if __name__ == "__main__":
    asyncio.run(main())

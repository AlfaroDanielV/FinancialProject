from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def refresh_dashboard_materialized_views(db: AsyncSession) -> None:
    """Refresh historical dashboard aggregates for the 6e SPA.

    The nightly insights worker owns this call so we keep a single scheduled
    job instead of adding a second cron surface.
    """
    await db.execute(text("REFRESH MATERIALIZED VIEW mv_monthly_summary_by_user"))
    await db.execute(text("REFRESH MATERIALIZED VIEW mv_yearly_summary_by_user"))

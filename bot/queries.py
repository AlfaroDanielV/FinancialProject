"""Format RunQuery results into Spanish chat replies."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user import User
from api.services.transactions import recent_for_user, sum_in_window
from api.services.telegram_dispatcher import RunQuery

from . import messages_es
from .formatting import format_amount


async def run_query(
    *, user: User, query: RunQuery, db: AsyncSession
) -> str:
    if query.query_kind == "recent":
        return await _run_recent(user, query, db)
    if query.query_kind == "balance":
        return await _run_balance(user, query, db)
    return messages_es.HELP_TEXT


async def _run_recent(user: User, q: RunQuery, db: AsyncSession) -> str:
    rows = await recent_for_user(user=user, limit=q.limit, db=db)
    # Filter the window in Python — cheap for small limits and avoids a
    # separate date-bounded query path.
    rows = [r for r in rows if q.window_start <= r.transaction_date <= q.window_end]
    if not rows:
        return messages_es.QUERY_EMPTY

    lines = [messages_es.QUERY_RECENT_HEADER.format(n=len(rows))]
    for r in rows:
        label = r.merchant or r.category or "(sin detalle)"
        amt = format_amount(Decimal(str(r.amount)), r.currency)
        sign = "−" if r.amount < 0 else "+"
        lines.append(f"  {r.transaction_date.isoformat()}  {sign}{amt}  {label}")
    return "\n".join(lines)


async def _run_balance(user: User, q: RunQuery, db: AsyncSession) -> str:
    totals = await sum_in_window(
        user=user, start=q.window_start, end=q.window_end, db=db
    )
    if not totals:
        return messages_es.QUERY_EMPTY
    lines = [messages_es.QUERY_BALANCE_HEADER]
    lines.append(
        f"  del {q.window_start.isoformat()} al {q.window_end.isoformat()}"
    )
    for key, val in sorted(totals.items()):
        currency, side = key.split("_")
        label = "Gastos" if side == "expense" else "Ingresos"
        lines.append(f"  {label} ({currency}): {format_amount(val, currency)}")
    return "\n".join(lines)

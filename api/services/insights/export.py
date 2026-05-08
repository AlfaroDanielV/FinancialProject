"""Phase 6c B8 — JSON export of a user's memory.

The export is a privacy primitive (the user's right to inspect what we
remember about them) and a data-portability primitive (lets them keep
a copy before /olvidar todo).

Includes active rows AND expired-but-not-yet-purged rows by default.
The lifecycle worker hard-deletes unlocked rows 30 days past
`valid_until`, so "still in DB" is the natural cutoff; user_locked rows
are never auto-purged and always export.

Stable order: (insight_type, dedup_key, created_at) so consecutive
exports diff cleanly.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import asc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user_insight import UserInsight


# A small chunk size keeps the streaming response fluid without paying
# per-row I/O cost. 200 rows ≈ ~100KB at our average payload size.
EXPORT_CHUNK_SIZE = 200

# Pre-count threshold above which we switch to streaming. Below this, the
# whole payload fits comfortably in a single JSONResponse without paging
# the response body. Tunable without changing the wire format.
STREAMING_ROW_THRESHOLD = 2000


async def count_user_insights(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    include_expired: bool = True,
    now: datetime | None = None,
) -> int:
    stmt = (
        select(func.count())
        .select_from(UserInsight)
        .where(UserInsight.user_id == user_id)
    )
    if not include_expired:
        stmt = stmt.where(UserInsight.valid_until > (now or datetime.now(timezone.utc)))
    return int((await session.execute(stmt)).scalar_one())


async def iter_export_rows(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    include_expired: bool = True,
    now: datetime | None = None,
    chunk_size: int = EXPORT_CHUNK_SIZE,
) -> AsyncIterator[list[dict[str, Any]]]:
    """Yield batches of serialized rows in stable order.

    Pages with `LIMIT/OFFSET` to keep memory bounded. Order is `(insight_type,
    dedup_key, created_at)` — invariant across calls so a re-export of the
    same DB state produces a byte-identical body.
    """
    offset = 0
    while True:
        stmt = (
            select(UserInsight)
            .where(UserInsight.user_id == user_id)
            .order_by(
                asc(UserInsight.insight_type),
                asc(UserInsight.dedup_key),
                asc(UserInsight.created_at),
            )
            .offset(offset)
            .limit(chunk_size)
        )
        if not include_expired:
            stmt = stmt.where(
                UserInsight.valid_until > (now or datetime.now(timezone.utc))
            )
        rows = (await session.execute(stmt)).scalars().all()
        if not rows:
            return
        yield [serialize_insight_for_export(row) for row in rows]
        if len(rows) < chunk_size:
            return
        offset += chunk_size


def serialize_insight_for_export(row: UserInsight) -> dict[str, Any]:
    """Stable JSON-friendly representation of a single insight row."""
    return {
        "id": str(row.id),
        "insight_type": row.insight_type,
        "content": row.content,
        "confidence": _decimal_str(row.confidence),
        "source": row.source,
        "valid_until": _iso(row.valid_until),
        "user_locked": bool(row.user_locked),
        "dedup_key": row.dedup_key,
        "reinforcement_count": int(row.reinforcement_count),
        "last_reinforced_at": _iso(row.last_reinforced_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _decimal_str(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()

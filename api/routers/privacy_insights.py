"""Phase 6c B8 — privacy endpoints for the user-memory layer.

Both endpoints require the real `X-Shortcut-Token` (no dev shim). They
are user-driven and destructive (delete) or PII-emitting (export); the
shim is explicitly out.

    DELETE /api/v1/users/me/insights
        Hard-delete every insight for the caller. Per-row audit. Returns
        {"deleted": <count>}.

    GET /api/v1/users/me/insights/export[?include_expired=true]
        JSON dump. Streams when the user has more than
        STREAMING_ROW_THRESHOLD rows. Emits one `exported` audit row.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user_via_token
from ..models.user import User
from ..services.insights.export import (
    STREAMING_ROW_THRESHOLD,
    count_user_insights,
    iter_export_rows,
)
from ..services.insights.memory_view import delete_insights
from ..services.insights.persister import audit


router = APIRouter(prefix="/api/v1/users/me/insights", tags=["insights-privacy"])


_FORMAT_VERSION = 1


@router.delete("", status_code=200)
async def delete_my_insights(
    user: User = Depends(current_user_via_token),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Hard-delete every insight for the caller. Audit row per insight."""
    deleted = await delete_insights(
        db,
        user_id=user.id,
        deletion_reason="api_delete_my_insights",
        actor="user",
    )
    await db.commit()
    return {"deleted": deleted}


@router.get("/export")
async def export_my_insights(
    include_expired: bool = Query(
        default=True,
        description=(
            "When true (default), include rows with valid_until in the past "
            "but not yet hard-purged by the lifecycle worker."
        ),
    ),
    user: User = Depends(current_user_via_token),
    db: AsyncSession = Depends(get_db),
):
    """Return the caller's full memory state as JSON.

    Streams when the row count exceeds STREAMING_ROW_THRESHOLD; otherwise
    sends a single JSONResponse. Either way, an `exported` audit row is
    emitted with the count and a request_id.
    """
    now = datetime.now(timezone.utc)
    total = await count_user_insights(
        db, user_id=user.id, include_expired=include_expired, now=now
    )

    request_id = uuid.uuid4().hex[:16]
    await audit(
        db,
        user_id=user.id,
        insight_id=None,
        actor="user",
        payload={
            "action": "exported",
            "count": total,
            "format": "json",
            "request_id": request_id,
        },
    )
    await db.commit()

    if total > STREAMING_ROW_THRESHOLD:
        return StreamingResponse(
            _stream_export_body(
                db,
                user_id=user.id,
                include_expired=include_expired,
                now=now,
                request_id=request_id,
            ),
            media_type="application/json",
            headers={"X-Insights-Export-Request-Id": request_id},
        )

    rows: list[dict] = []
    async for batch in iter_export_rows(
        db,
        user_id=user.id,
        include_expired=include_expired,
        now=now,
    ):
        rows.extend(batch)

    return JSONResponse(
        content={
            "user_id": str(user.id),
            "exported_at": now.isoformat(),
            "format_version": _FORMAT_VERSION,
            "request_id": request_id,
            "include_expired": include_expired,
            "count": total,
            "insights": rows,
        },
        headers={"X-Insights-Export-Request-Id": request_id},
    )


async def _stream_export_body(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    include_expired: bool,
    now: datetime,
    request_id: str,
):
    """Manually-emitted JSON so we never buffer the whole array.

    Shape matches the non-streaming response exactly so consumers don't
    have to branch on size.
    """
    header = {
        "user_id": str(user_id),
        "exported_at": now.isoformat(),
        "format_version": _FORMAT_VERSION,
        "request_id": request_id,
        "include_expired": include_expired,
    }
    # Open the envelope and the insights array; rows go in batched.
    yield (
        "{"
        f'"user_id":{json.dumps(header["user_id"])},'
        f'"exported_at":{json.dumps(header["exported_at"])},'
        f'"format_version":{header["format_version"]},'
        f'"request_id":{json.dumps(header["request_id"])},'
        f'"include_expired":{"true" if include_expired else "false"},'
        '"insights":['
    )
    first = True
    async for batch in iter_export_rows(
        db,
        user_id=user_id,
        include_expired=include_expired,
        now=now,
    ):
        for row in batch:
            chunk = json.dumps(row)
            if first:
                yield chunk
                first = False
            else:
                yield "," + chunk
    yield "]}"

"""Phase 6c B8 + Phase 6e B10 — user-facing memory endpoints.

The destructive (delete) and PII-emitting (export) routes require the
real `X-Shortcut-Token` (no dev shim). The read endpoint and the per-row
edit accept the standard `current_user` chain (cookie session in 6e SPA
or shim in dev).

    GET /api/v1/users/me/insights[?include_expired=false]
        Grouped full list of the caller's active insights, ordered by
        the same `GROUP_ORDER` the bot's `/memoria` uses.

    PATCH /api/v1/users/me/insights/{id}
        Per-row edit. Body is a typed `InsightContent` payload validated
        through the discriminated union. Sets source='user_override',
        user_locked=true, confidence=1.00, emits a `locked` audit row.
        Only `USER_EDITABLE_TYPES` are accepted; the rest return 400.

    DELETE /api/v1/users/me/insights/{id}
        Hard-delete one insight. Single audit row.

    DELETE /api/v1/users/me/insights/group/{group}
        Hard-delete every insight in a group (patrones / metas /
        conozco / banderas). Per-row audit.

    DELETE /api/v1/users/me/insights
        Hard-delete every insight for the caller. Per-row audit.

    GET /api/v1/users/me/insights/export[?include_expired=true]
        JSON dump. Streams when the user has more than
        STREAMING_ROW_THRESHOLD rows. Emits one `exported` audit row.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user, current_user_via_token
from ..models.user import User
from ..models.user_insight import UserInsight
from ..schemas.insights import (
    USER_EDITABLE_TYPES,
    validate_insight_content,
    valid_until_for,
)
from ..services.insights.export import (
    STREAMING_ROW_THRESHOLD,
    count_user_insights,
    iter_export_rows,
)
from ..services.insights.memory_view import (
    GROUP_LABELS_ES,
    GROUP_ORDER,
    delete_insights,
    emit_locked_audit,
    group_for,
    load_active_insights,
    render_view,
)
from ..services.insights.persister import audit


router = APIRouter(prefix="/api/v1/users/me/insights", tags=["insights-privacy"])

GroupKey = Literal["patrones", "metas", "conozco", "banderas"]


class InsightItem(BaseModel):
    id: uuid.UUID
    insight_type: str
    group: str
    description: str
    confidence: Decimal
    source: str
    user_locked: bool
    editable: bool
    valid_until: datetime
    updated_at: datetime
    content: dict[str, Any]


class InsightGroup(BaseModel):
    group: str
    label: str
    items: list[InsightItem]


class InsightsListResponse(BaseModel):
    total: int
    groups: list[InsightGroup]


class InsightDeleteResponse(BaseModel):
    deleted: int


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


# ── Phase 6e B10: full memory list, per-row edit + delete, group delete ─────


@router.get("", response_model=InsightsListResponse)
async def list_my_insights(
    include_expired: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> InsightsListResponse:
    """Phase 6e B10 — grouped full list backing the SPA memoria view.

    Returns active insights only by default. `include_expired=true` adds
    rows past `valid_until` but not yet hard-purged by the lifecycle
    worker (rare, useful for audit walkthroughs).
    """
    now = datetime.now(timezone.utc)
    if include_expired:
        result = await db.execute(
            select(UserInsight).where(UserInsight.user_id == user.id)
        )
        rows = list(result.scalars().all())
    else:
        rows = await load_active_insights(db, user_id=user.id, now=now)

    view = render_view(rows)
    by_id_row = {row.id: row for row in rows}

    groups: list[InsightGroup] = []
    for group_key in GROUP_ORDER:
        items = view.groups.get(group_key) or []
        if not items:
            continue
        out_items: list[InsightItem] = []
        for rendered in items:
            row = by_id_row.get(rendered.insight_id)
            if row is None:
                continue
            out_items.append(
                InsightItem(
                    id=row.id,
                    insight_type=row.insight_type,
                    group=group_key,
                    description=rendered.bullet_html.lstrip("• ").lstrip(),
                    confidence=Decimal(str(row.confidence)),
                    source=row.source,
                    user_locked=bool(row.user_locked),
                    editable=row.insight_type in USER_EDITABLE_TYPES,
                    valid_until=row.valid_until,
                    updated_at=row.updated_at,
                    content=row.content,
                )
            )
        groups.append(
            InsightGroup(
                group=group_key,
                label=GROUP_LABELS_ES[group_key],
                items=out_items,
            )
        )

    total = sum(len(g.items) for g in groups)
    return InsightsListResponse(total=total, groups=groups)


@router.patch("/{insight_id}", response_model=InsightItem)
async def update_my_insight(
    insight_id: uuid.UUID = Path(...),
    content: dict[str, Any] = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_via_token),
) -> InsightItem:
    """Phase 6e B10 — per-row user override.

    Body shape: `{"content": {...}}`. Validates the payload through the
    discriminated union; rejects if the content's `type` doesn't match
    the existing row or if the row's `insight_type` isn't in
    `USER_EDITABLE_TYPES`. Sets source='user_override', user_locked=true,
    confidence=1.00, emits a `locked` audit. No LLM re-parse.
    """
    result = await db.execute(
        select(UserInsight).where(
            UserInsight.id == insight_id,
            UserInsight.user_id == user.id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Insight no encontrado.")

    if row.insight_type not in USER_EDITABLE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Este tipo de insight es computado y no se puede editar. "
                "Si está equivocado, eliminalo y se recalcula solo."
            ),
        )

    try:
        validated = validate_insight_content(content)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail=f"Contenido inválido: {type(exc).__name__}",
        ) from exc

    if validated.type != row.insight_type:
        raise HTTPException(
            status_code=400,
            detail=(
                f"content.type='{validated.type}' no coincide con el "
                f"insight existente ('{row.insight_type}')."
            ),
        )

    serialized = validated.model_dump(mode="json")
    row.content = serialized
    row.source = "user_override"
    row.user_locked = True
    row.confidence = Decimal("1.00")
    row.valid_until = valid_until_for(validated)
    row.updated_at = datetime.now(timezone.utc)
    await db.flush()

    await emit_locked_audit(
        db,
        user_id=user.id,
        insight_id=row.id,
        insight_type=row.insight_type,
        dedup_key=row.dedup_key,
        content=serialized,
    )
    await db.commit()
    await db.refresh(row)

    return InsightItem(
        id=row.id,
        insight_type=row.insight_type,
        group=group_for(row.insight_type),
        description="",  # SPA refetches the list for the natural-language description
        confidence=Decimal(str(row.confidence)),
        source=row.source,
        user_locked=bool(row.user_locked),
        editable=True,
        valid_until=row.valid_until,
        updated_at=row.updated_at,
        content=row.content,
    )


@router.delete("/group/{group}", response_model=InsightDeleteResponse)
async def delete_my_insights_by_group(
    group: GroupKey,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_via_token),
) -> InsightDeleteResponse:
    """Phase 6e B10 — hard-delete every active insight in one group.

    Declared BEFORE the per-id DELETE so the UUID converter on
    `/{insight_id}` doesn't 422 the `group` segment first.
    """
    rows = await load_active_insights(
        db, user_id=user.id, now=datetime.now(timezone.utc)
    )
    ids = [row.id for row in rows if group_for(row.insight_type) == group]
    if not ids:
        return InsightDeleteResponse(deleted=0)
    deleted = await delete_insights(
        db,
        user_id=user.id,
        insight_ids=ids,
        deletion_reason=f"spa_delete_group_{group}",
        actor="user",
    )
    await db.commit()
    return InsightDeleteResponse(deleted=deleted)


@router.delete("/{insight_id}", response_model=InsightDeleteResponse)
async def delete_my_insight(
    insight_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_via_token),
) -> InsightDeleteResponse:
    """Phase 6e B10 — hard-delete one insight."""
    deleted = await delete_insights(
        db,
        user_id=user.id,
        insight_ids=[insight_id],
        deletion_reason="spa_delete_single",
        actor="user",
    )
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Insight no encontrado.")
    await db.commit()
    return InsightDeleteResponse(deleted=deleted)

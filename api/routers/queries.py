"""Phase 6a — bloque 9: query layer endpoints.

Currently exposes a single route, `POST /api/v1/queries/test`, which
runs a user query through the SAME dispatcher + delivery pipeline the
Telegram bot uses. It exists so curl scripts and humans can drive the
query layer without paired Telegram credentials, and so block 11's
smoke can assert on chunking / token counters / tools_used directly.

Auth: `current_user` (X-Shortcut-Token preferred, X-User-Id dev shim
accepted). 401 with neither.

Status codes:
- 200: dispatcher ran (success OR Spanish-mapped error returned in
  `reply`). Inspect `error_category` to distinguish.
- 401: missing auth.
- 429: daily token budget exhausted (pre-checked here so the cap
  shows up as a real status code rather than 200 with budget text).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.queries.delivery import BudgetExceeded, handle_query_error
from app.queries.dispatcher import run_dispatch
from api.database import get_db
from api.dependencies import current_user
from api.models.user import User
from api.schemas.queries import (
    QueryTestRequest,
    QueryTestResponse,
    QueryTokens,
)
from api.services.budget import assert_within_budget

from bot.delivery_send import render_chunks


router = APIRouter(prefix="/api/v1/queries", tags=["queries"])


@router.post("/test", response_model=QueryTestResponse)
async def query_test(
    payload: QueryTestRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> QueryTestResponse:
    """Drive the query dispatcher end-to-end and return raw text + metadata.

    Bot path produces a Telegram message via `bot.delivery_send.send_chunked`;
    this endpoint produces the same chunks (via `render_chunks`) without
    sending them. Same dispatcher entry, same sanitize+split helper —
    no parallel pipeline.
    """
    tz_name = getattr(user, "timezone", None) or "America/Costa_Rica"

    # Budget pre-check. Dispatcher does its own internal check too; the
    # extra one here is what lets us surface 429 cleanly rather than 200
    # with the Spanish "límite diario" string in `reply`.
    try:
        await assert_within_budget(
            user_id=user.id, db=db, tz_name=tz_name
        )
    except BudgetExceeded as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=handle_query_error(e, user_id=user.id),
        )

    outcome = await run_dispatch(
        user_id=user.id,
        message_text=payload.message,
    )

    return QueryTestResponse(
        reply=outcome.text,
        chunks=render_chunks(outcome.text),
        dispatch_id=str(outcome.dispatch_id) if outcome.dispatch_id else None,
        iterations=outcome.total_iterations,
        tools_used=outcome.tools_used,
        tokens=QueryTokens(
            input=outcome.total_input_tokens,
            output=outcome.total_output_tokens,
            cache_read=outcome.cache_read_input_tokens,
            cache_creation=outcome.cache_creation_input_tokens,
        ),
        duration_ms=outcome.duration_ms,
        error_category=outcome.error_category,
    )

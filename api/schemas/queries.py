"""Pydantic schemas for Phase 6a query endpoints (`/api/v1/queries/*`)."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class QueryTestRequest(BaseModel):
    """Body for POST /api/v1/queries/test — debugging entry into the
    same dispatcher pipeline the Telegram bot uses."""

    user_id: int = Field(
        ...,
        description=(
            "Telegram from.id for the caller. Auth still resolves the app "
            "user server-side; this is a guard/debug identifier."
        ),
    )
    query: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="Spanish-language user query to run through the dispatcher.",
    )


class QueryTokens(BaseModel):
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0


class QueryTestResponse(BaseModel):
    """Mirror of `DispatchOutcome` plus the post-delivery chunk list.

    `reply` is the raw text the dispatcher produced (success or
    Spanish-mapped error). `chunks` is what the Telegram bot would
    actually send — sanitize_telegram_html → split_for_telegram. Useful
    for catching tag-balance regressions without firing a real bot.
    """

    reply: str
    chunks: list[str] = Field(default_factory=list)
    dispatch_id: Optional[str] = None
    iterations: int = 0
    tools_used: list[dict[str, Any]] = Field(default_factory=list)
    tokens: QueryTokens = Field(default_factory=QueryTokens)
    duration_ms: int = 0
    error_category: Optional[str] = None

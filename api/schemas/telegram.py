"""Pydantic schemas for the Phase 5b Telegram endpoints."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PairingCodeResponse(BaseModel):
    code: str = Field(..., description="6-character alphanumeric pairing code.")
    expires_in_seconds: int = Field(..., ge=1)


class SimulateRequest(BaseModel):
    """Dev-only bridge used by phase5b_smoke.sh. Mirrors the parts of a
    Telegram update the pipeline actually reads — no aiogram types involved
    so curl can drive the full flow.

    `mock_extraction` is for zero-cost deterministic smoke runs: when set,
    the pipeline skips the LLM call and uses this payload as though the
    extractor had produced it. Rejected in non-development environments
    (the whole endpoint is).
    """

    telegram_user_id: int = Field(..., description="Telegram 'from.id'")
    text: str = Field(..., min_length=1, max_length=4096)
    first_name: Optional[str] = Field(default=None, max_length=100)
    callback_data: Optional[str] = Field(
        default=None,
        description=(
            "If set, the update is treated as an inline-keyboard callback "
            "rather than a text message."
        ),
    )
    pairing_code: Optional[str] = Field(
        default=None,
        description=(
            "When set, the simulator treats this as a /start <code> update "
            "and runs the pairing flow. telegram_user_id becomes the new "
            "binding target."
        ),
    )
    mock_extraction: Optional[dict] = Field(
        default=None,
        description=(
            "If set, skip the LLM call and feed this dict into Pydantic as "
            "the ExtractionResult. Use to drive the dispatcher → commit "
            "path deterministically without API cost."
        ),
    )


class SimulateResponse(BaseModel):
    text: str
    buttons: list[dict] = Field(default_factory=list)

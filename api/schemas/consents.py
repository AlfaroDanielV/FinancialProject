"""Pydantic schemas — Phase 7e consent ledger."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

ConsentPurpose = Literal[
    "core_service",
    "behavioral_insights",
    "product_research",
    "aggregated_datasets",
]


class ConsentRecordRequest(BaseModel):
    purpose: ConsentPurpose
    granted: bool
    # The version of the consent text the user was shown (e.g. "2026-06.v1").
    version: str = Field(..., min_length=1, max_length=40)

    model_config = {"extra": "forbid"}


class ConsentStateItem(BaseModel):
    purpose: ConsentPurpose
    # "never_set" = no row recorded yet for this purpose.
    status: Literal["granted", "revoked", "never_set"]
    version: Optional[str] = None
    occurred_at: Optional[datetime] = None


class ConsentStateResponse(BaseModel):
    consents: list[ConsentStateItem]

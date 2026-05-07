"""Pydantic schemas for the Gmail OAuth + onboarding endpoints (Phase 6b)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class OAuthStartResponse(BaseModel):
    """Response from POST /api/v1/gmail/oauth/start.

    `auth_url` is the URL the bot/user opens in a browser. `expires_at`
    is when the embedded state JWT expires — after that, hitting the
    callback fails with OAuthStateError and the user has to re-issue.
    """

    auth_url: str
    expires_at: datetime
    expires_in_seconds: int = Field(..., ge=1)


class GmailStatusResponse(BaseModel):
    """Lightweight status used by /estado_gmail (read by the bot).

    Returned by the same endpoint family for parity with `users/me`.
    """

    connected: bool
    granted_at: datetime | None = None
    activated_at: datetime | None = None
    revoked_at: datetime | None = None
    last_refresh_at: datetime | None = None
    scopes: list[str] = Field(default_factory=list)

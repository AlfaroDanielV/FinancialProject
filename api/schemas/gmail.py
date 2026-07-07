"""Pydantic schemas for the Gmail OAuth + onboarding endpoints (Phase 6b),
plus the native Gmail surface (sender whitelist + shadow review)."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

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


# ── native Gmail surface (sender whitelist) ───────────────────────────────────


class SenderResponse(BaseModel):
    id: uuid.UUID
    sender_email: str
    bank_name: Optional[str] = None
    added_at: datetime

    model_config = {"from_attributes": True}


class SenderAddRequest(BaseModel):
    """Add one bank's senders. `emails` is a free-text blob — comma / space /
    newline separated — so the operator can paste a list. Validated +
    deduped + lowercased server-side."""

    bank_name: Optional[str] = Field(default=None, max_length=120)
    emails: str = Field(..., min_length=1, max_length=2000)


class SenderAddResponse(BaseModel):
    added: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)  # invalid / already active
    at_cap: bool = False  # hit the 8-sender active cap before adding all


# ── native Gmail surface (shadow review) ──────────────────────────────────────


class ShadowConfirmItem(BaseModel):
    """One shadow row to confirm, with optional per-row edits applied at
    confirm time (shadow rows can't be PATCHed directly)."""

    id: uuid.UUID
    amount: Optional[float] = None
    merchant: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    category: Optional[str] = Field(default=None, max_length=100)
    # FK to a user_categories row (the Categorías screen). Sent alongside
    # `category` (name) by the native review picker; validated in the router.
    category_id: Optional[uuid.UUID] = None
    # FK to one of the caller's active accounts. The native review screen
    # pre-fills this with the scan's best-effort guess; validated in the router
    # (caller-owned, active, currency-matched). None = keep the row's current
    # (guessed) account.
    account_id: Optional[uuid.UUID] = None
    transaction_date: Optional[date] = None
    # Envelope choice at review time. The scan pre-fills an auto-suggested
    # envelope on the shadow row; `envelope_id` overrides it and
    # `clear_envelope=true` removes it. Both mark the envelope user-set.
    # Validated in the router (assignable by the caller — owner or shared
    # member), mirroring PATCH /transactions.
    envelope_id: Optional[uuid.UUID] = None
    clear_envelope: bool = False


class ShadowConfirmRequest(BaseModel):
    items: list[ShadowConfirmItem] = Field(..., min_length=1, max_length=500)


class ShadowConfirmResponse(BaseModel):
    confirmed: int


class ShadowDiscardRequest(BaseModel):
    ids: list[uuid.UUID] = Field(..., min_length=1, max_length=500)


class ShadowDiscardResponse(BaseModel):
    discarded: int


class GmailScanResponse(BaseModel):
    queued: bool
    days: int


class GmailRunInfo(BaseModel):
    """Latest ingestion-run snapshot for the scan-progress UI."""

    started_at: datetime
    finished_at: Optional[datetime] = None
    running: bool
    mode: str
    messages_scanned: int
    transactions_created: int
    transactions_matched: int
    has_errors: bool


class GmailScanStatusResponse(BaseModel):
    """Everything the native app needs to give the user real scan feedback:
    whether Gmail is connected, how many senders are whitelisted (0 → the
    scan would no-op), and the latest run's progress/result."""

    connected: bool
    revoked: bool
    senders_count: int
    latest_run: Optional[GmailRunInfo] = None

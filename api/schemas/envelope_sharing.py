"""Pydantic schemas for shared envelopes ("Sobres compartidos").

An owner mints a short code for a ROOT envelope; up to 9 others redeem it to
become members. A member can add/remove only their own expenses to/from the
shared envelope and sees only aggregate spend + their own portion. See
api/services/envelope_sharing.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ShareCodeResponse(BaseModel):
    code: str
    expires_at: datetime


class RedeemRequest(BaseModel):
    model_config = {"extra": "forbid"}

    code: str = Field(..., min_length=1, max_length=16)


class MemberResponse(BaseModel):
    user_id: uuid.UUID
    full_name: str
    # The owner is surfaced in the member list (implicit via envelopes.user_id,
    # not an envelope_members row) so the UI can show everyone with access.
    is_owner: bool = False


class MemberRemovedResponse(BaseModel):
    removed_user_id: uuid.UUID

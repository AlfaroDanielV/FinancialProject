"""Phase 7e — versioned, append-only consent ledger.

Consent can be aggregated later but never retro-fitted: rows are written at
the moment of the act, carry the consent-text version the user saw, and are
never updated. Current state per purpose = the latest row by occurred_at.

`core_service` exists so the ledger is complete (the product can't run
without it); the product layer treats it as required, not optional. The
other purposes are genuinely optional and default to "never_set" until the
user decides (P8 onboarding wires the UX).
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.consent import UserConsent

CONSENT_PURPOSES: tuple[str, ...] = (
    "core_service",
    "behavioral_insights",
    "product_research",
    "aggregated_datasets",
)


async def record_consent(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    purpose: str,
    granted: bool,
    version: str,
    source: str = "api",
) -> UserConsent:
    """Append one grant/revoke act. Caller commits. ValueError on an unknown
    purpose (the DB CHECK would also reject it — fail fast with a clear
    message instead)."""
    if purpose not in CONSENT_PURPOSES:
        raise ValueError(f"Unknown consent purpose: {purpose!r}")
    row = UserConsent(
        user_id=user_id,
        purpose=purpose,
        status="granted" if granted else "revoked",
        version=version,
        source=source,
    )
    db.add(row)
    await db.flush()
    return row


async def current_consents(
    db: AsyncSession, *, user_id: uuid.UUID
) -> dict[str, Optional[UserConsent]]:
    """Latest row per purpose (None = never recorded)."""
    rows = (
        await db.execute(
            select(UserConsent)
            .where(UserConsent.user_id == user_id)
            .order_by(UserConsent.occurred_at.asc(), UserConsent.created_at.asc())
        )
    ).scalars()
    state: dict[str, Optional[UserConsent]] = {p: None for p in CONSENT_PURPOSES}
    for row in rows:  # ascending order → the last write per purpose wins
        state[row.purpose] = row
    return state


async def has_consent(
    db: AsyncSession, *, user_id: uuid.UUID, purpose: str
) -> bool:
    state = await current_consents(db, user_id=user_id)
    row = state.get(purpose)
    return row is not None and row.status == "granted"

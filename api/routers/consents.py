"""Phase 7e — consent ledger endpoints.

GET returns the caller's current state for every known purpose (purposes the
user never decided show `never_set`). POST appends one grant/revoke act —
the ledger is append-only; there is no PATCH/DELETE by design (the audit
trail IS the feature; account erasure cascades the rows).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.user import User
from ..schemas.consents import (
    ConsentRecordRequest,
    ConsentStateItem,
    ConsentStateResponse,
)
from ..services.consents import (
    CONSENT_PURPOSES,
    current_consents,
    record_consent,
)

router = APIRouter(prefix="/api/v1/users/me/consents", tags=["consents"])


def _state_items(state) -> list[ConsentStateItem]:
    items: list[ConsentStateItem] = []
    for purpose in CONSENT_PURPOSES:
        row = state.get(purpose)
        if row is None:
            items.append(ConsentStateItem(purpose=purpose, status="never_set"))
        else:
            items.append(
                ConsentStateItem(
                    purpose=purpose,
                    status=row.status,
                    version=row.version,
                    occurred_at=row.occurred_at,
                )
            )
    return items


@router.get("", response_model=ConsentStateResponse)
async def get_consents(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    state = await current_consents(db, user_id=user.id)
    return ConsentStateResponse(consents=_state_items(state))


@router.post("", response_model=ConsentStateResponse)
async def post_consent(
    body: ConsentRecordRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    await record_consent(
        db,
        user_id=user.id,
        purpose=body.purpose,
        granted=body.granted,
        version=body.version,
        source="api",
    )
    await db.commit()
    state = await current_consents(db, user_id=user.id)
    return ConsentStateResponse(consents=_state_items(state))

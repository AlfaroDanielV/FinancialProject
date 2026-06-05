"""Envelope budgeting ("Sobres") CRUD + summary.

Spending-cap envelopes tied to a class (needs/wants/savings/investing). Spend
is computed live (see api/services/envelopes.py); this router only manages the
envelope config + serves the home-tab summary. Soft-archive on DELETE (mirrors
goals/recurring_incomes). currency/period are immutable post-create
(EnvelopeUpdate forbids them).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.envelope import Envelope
from ..models.user import User
from ..schemas.envelopes import (
    EnvelopeCreate,
    EnvelopeResponse,
    EnvelopeSummaryResponse,
    EnvelopeUpdate,
)
from ..services.envelopes import compute_envelope_summary

router = APIRouter(prefix="/api/v1/envelopes", tags=["envelopes"])


async def _get_envelope(
    db: AsyncSession, *, user_id: uuid.UUID, envelope_id: uuid.UUID
) -> Envelope:
    row = await db.execute(
        select(Envelope).where(
            Envelope.id == envelope_id, Envelope.user_id == user_id
        )
    )
    env = row.scalar_one_or_none()
    if env is None:
        raise HTTPException(status_code=404, detail="Sobre no encontrado.")
    return env


@router.post("", response_model=EnvelopeResponse, status_code=201)
async def create_envelope(
    payload: EnvelopeCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    env = Envelope(
        user_id=user.id,
        name=payload.name,
        envelope_class=payload.envelope_class,
        limit_amount=payload.limit_amount,
        currency=payload.currency,
    )
    db.add(env)
    await db.commit()
    await db.refresh(env)
    return env


@router.get("", response_model=list[EnvelopeResponse])
async def list_envelopes(
    include_archived: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    stmt = select(Envelope).where(Envelope.user_id == user.id)
    if not include_archived:
        stmt = stmt.where(Envelope.archived.is_(False))
    stmt = stmt.order_by(Envelope.envelope_class.asc(), Envelope.name.asc())
    return list((await db.execute(stmt)).scalars().all())


@router.get("/summary", response_model=EnvelopeSummaryResponse)
async def envelopes_summary(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Home-tab feed: per-envelope limit/spent/over_limit + per-class subtotals
    + total-limit-vs-monthly-income for the current month (user timezone)."""
    return await compute_envelope_summary(db, user=user)


@router.get("/{envelope_id}", response_model=EnvelopeResponse)
async def get_envelope(
    envelope_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    return await _get_envelope(db, user_id=user.id, envelope_id=envelope_id)


@router.patch("/{envelope_id}", response_model=EnvelopeResponse)
async def update_envelope(
    envelope_id: uuid.UUID,
    payload: EnvelopeUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    env = await _get_envelope(db, user_id=user.id, envelope_id=envelope_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(env, field, value)
    await db.commit()
    await db.refresh(env)
    return env


@router.delete("/{envelope_id}", response_model=EnvelopeResponse)
async def delete_envelope(
    envelope_id: uuid.UUID,
    hard: bool = Query(
        default=False,
        description=(
            "When true, permanently delete the envelope. Tagged transactions "
            "are unlinked (envelope_id → NULL via the FK), never deleted. "
            "Default false = soft archive (hide + stop counting, keep links)."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """DELETE = soft archive by default; `?hard=true` permanently removes the
    row. The `transactions.envelope_id` FK is `ON DELETE SET NULL`, so a hard
    delete unlinks tagged transactions without deleting them."""
    env = await _get_envelope(db, user_id=user.id, envelope_id=envelope_id)
    if hard:
        # Snapshot before delete so we can still echo the removed row back.
        snapshot = EnvelopeResponse.model_validate(env)
        await db.delete(env)
        await db.commit()
        return snapshot
    env.archived = True
    env.is_active = False
    await db.commit()
    await db.refresh(env)
    return env

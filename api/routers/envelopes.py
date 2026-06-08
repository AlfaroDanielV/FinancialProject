"""Envelope budgeting ("Sobres") CRUD + summary.

Spending-cap envelopes tied to a class (needs/wants/savings/investing). Spend
is computed live (see api/services/envelopes.py); this router only manages the
envelope config + serves the home-tab summary. Soft-archive on DELETE (mirrors
goals/recurring_incomes). currency/period are immutable post-create
(EnvelopeUpdate forbids them).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

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
from ..services.envelopes import (
    active_children,
    archive_subtree,
    compute_envelope_summary,
    set_class_subtree,
)

router = APIRouter(prefix="/api/v1/envelopes", tags=["envelopes"])


def _fmt(amount: Decimal, currency: str) -> str:
    symbol = "$" if (currency or "CRC").upper() == "USD" else "₡"
    return f"{symbol}{amount:,.0f}"


async def _parent_available(
    db: AsyncSession, *, user_id: uuid.UUID, parent: Envelope, exclude_id=None
) -> Decimal:
    """How much of a parent's budget is still unallocated to its children
    (excluding one child when editing it)."""
    siblings = await active_children(db, user_id=user_id, parent_id=parent.id)
    allocated = sum(
        (s.limit_amount for s in siblings if s.id != exclude_id), Decimal("0")
    )
    return Decimal(parent.limit_amount) - allocated


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
    envelope_class = payload.envelope_class
    currency = payload.currency
    parent_id = None
    depth = 1
    if payload.parent_id is not None:
        # Nest under a parent: 5-level cap, and the child inherits the root's
        # class + currency (one tree = one class + one currency).
        parent = await _get_envelope(
            db, user_id=user.id, envelope_id=payload.parent_id
        )
        if parent.archived:
            raise HTTPException(
                status_code=400, detail="El sobre padre está archivado."
            )
        if parent.depth >= 5:
            raise HTTPException(
                status_code=422,
                detail="No se pueden anidar sobres más de 5 niveles.",
            )
        # A sub-sobre's limit can't exceed what's still unallocated in the
        # parent's budget (children must fit inside the parent total).
        available = await _parent_available(db, user_id=user.id, parent=parent)
        if Decimal(str(payload.limit_amount)) > available:
            raise HTTPException(
                status_code=422,
                detail=(
                    "El límite del sub-sobre no puede superar lo que queda "
                    f"disponible del sobre padre ({_fmt(available, parent.currency)})."
                ),
            )
        parent_id = parent.id
        depth = parent.depth + 1
        envelope_class = parent.envelope_class
        currency = parent.currency

    env = Envelope(
        user_id=user.id,
        parent_id=parent_id,
        depth=depth,
        name=payload.name,
        envelope_class=envelope_class,
        limit_amount=payload.limit_amount,
        currency=currency,
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
    data = payload.model_dump(exclude_unset=True)

    # Class is a tree-wide property: editable only on a root, and the change
    # cascades to the subtree. A sub-sobre inherits it.
    new_class = data.pop("envelope_class", None)
    if new_class is not None and new_class != env.envelope_class:
        if env.parent_id is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "La clase de un sub-sobre se hereda del sobre padre; "
                    "cambiá la del sobre raíz."
                ),
            )
        await set_class_subtree(db, user=user, root=env, new_class=new_class)
        env.envelope_class = new_class

    # Keep the budget invariant Σ(children) ≤ parent on a limit change.
    new_limit = data.get("limit_amount")
    if new_limit is not None:
        if env.parent_id is not None:
            # A sub-sobre can't grow past the parent's remaining budget.
            parent = await _get_envelope(
                db, user_id=user.id, envelope_id=env.parent_id
            )
            available = await _parent_available(
                db, user_id=user.id, parent=parent, exclude_id=env.id
            )
            if Decimal(str(new_limit)) > available:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "El límite del sub-sobre no puede superar lo que queda "
                        f"disponible del sobre padre ({_fmt(available, parent.currency)})."
                    ),
                )
        else:
            # A parent can't shrink below what's already split into its children.
            allocated = sum(
                (
                    c.limit_amount
                    for c in await active_children(
                        db, user_id=user.id, parent_id=env.id
                    )
                ),
                Decimal("0"),
            )
            if allocated > Decimal("0") and Decimal(str(new_limit)) < allocated:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "El límite no puede ser menor que lo ya asignado a sus "
                        f"sub-sobres ({_fmt(allocated, env.currency)})."
                    ),
                )

    # Archiving a parent archives its whole subtree (matches DELETE). Restoring
    # only restores this node (restore the root first to bring back a subtree).
    archived = data.pop("archived", None)
    if archived is True:
        await archive_subtree(db, user=user, root=env)
        env.archived = True
        env.is_active = False
    elif archived is False:
        env.archived = False
        env.is_active = True

    for field, value in data.items():
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
        # Snapshot before delete so we can still echo the removed row back. The
        # DB FK is ON DELETE CASCADE, so the whole subtree goes; tagged
        # transactions unlink (envelope_id → NULL), never deleted.
        snapshot = EnvelopeResponse.model_validate(env)
        await db.delete(env)
        await db.commit()
        return snapshot
    # Soft archive cascades to the subtree (a parent can't stay "open" with its
    # budget split across now-hidden children).
    await archive_subtree(db, user=user, root=env)
    env.archived = True
    env.is_active = False
    await db.commit()
    await db.refresh(env)
    return env

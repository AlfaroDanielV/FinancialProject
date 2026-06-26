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
from ..redis_client import get_redis
from ..schemas.envelope_sharing import (
    MemberRemovedResponse,
    MemberResponse,
    RedeemRequest,
    ShareCodeResponse,
)
from ..schemas.envelopes import (
    EnvelopeCreate,
    EnvelopeReallocateRequest,
    EnvelopeReallocateResponse,
    EnvelopeResponse,
    EnvelopeStarterPackRequest,
    EnvelopeSummaryResponse,
    EnvelopeUpdate,
)
from ..services.envelope_sharing import (
    fetch_shared_trees,
    is_member,
    list_members,
    mint_share_code,
    redeem_share_code,
    remove_member,
)
from ..services.envelopes import (
    active_children,
    archive_subtree,
    compute_envelope_summary,
    reallocate,
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
    own = (await db.execute(stmt)).scalars().all()
    out = [EnvelopeResponse.model_validate(e) for e in own]
    # Append shared envelopes (root + subtree) the caller is a MEMBER of, so the
    # assignment picker can target them. Marked is_shared → the client renders
    # them read-only; `user_id` on these rows is the OWNER's, not the caller's.
    for tree in await fetch_shared_trees(db, user_id=user.id):
        for env in tree.envelopes:
            out.append(
                EnvelopeResponse.model_validate(env).model_copy(
                    update={
                        "is_shared": True,
                        "role": "member",
                        "shared_by_name": tree.owner_name,
                        "member_count": tree.member_count,
                    }
                )
            )
    return out


@router.get("/summary", response_model=EnvelopeSummaryResponse)
async def envelopes_summary(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Home-tab feed: per-envelope limit/spent/over_limit + per-class subtotals
    + total-limit-vs-monthly-income for the current month (user timezone)."""
    return await compute_envelope_summary(db, user=user)


# ── reallocation + sharing ────────────────────────────────────────────────────
# Declared before the `/{envelope_id}` routes so the static `/reallocate` and
# `/redeem` segments are never captured as an id.


@router.post("/reallocate", response_model=EnvelopeReallocateResponse)
async def reallocate_envelopes(
    payload: EnvelopeReallocateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Move budget between two SAME-LEVEL envelopes (both roots, or two children
    of the same parent). The deterministic `reallocate` service validates the
    move (same parent, same currency, from-side floor) and writes both limits;
    this keeps committed_outflows = total_limit invariant. We commit + echo."""
    from_env, to_env = await reallocate(
        db,
        user=user,
        from_id=payload.from_id,
        to_id=payload.to_id,
        amount=payload.amount,
    )
    await db.commit()
    await db.refresh(from_env)
    await db.refresh(to_env)
    return EnvelopeReallocateResponse(
        from_=EnvelopeResponse.model_validate(from_env),
        to=EnvelopeResponse.model_validate(to_env),
    )


@router.post(
    "/starter-pack", response_model=list[EnvelopeResponse], status_code=201
)
async def create_starter_pack(
    payload: EnvelopeStarterPackRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Bulk-create a starter set of ROOT envelopes (≤ 8) in one transaction —
    the "armá tu presupuesto en 1 minuto" empty-state flow. Deterministic, never
    LLM-driven, and never auto-run: the caller (the empty-state UI) explicitly
    triggers it, so adding allocations here is the user's intended new budget.
    Each item creates a fresh root (parent_id=None, depth=1) in the user's
    currency. Envelopes have no name-uniqueness constraint, so a repeated name
    simply creates another sobre (no crash)."""
    currency = user.currency or "CRC"
    created: list[Envelope] = []
    for item in payload.items:
        env = Envelope(
            user_id=user.id,
            parent_id=None,
            depth=1,
            name=item.name,
            envelope_class=item.envelope_class,
            limit_amount=item.limit_amount,
            currency=currency,
        )
        db.add(env)
        created.append(env)
    await db.commit()
    for env in created:
        await db.refresh(env)
    return created


@router.post("/redeem", response_model=EnvelopeResponse)
async def redeem_envelope(
    payload: RedeemRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Join a shared envelope with a code its owner gave you. Idempotent; the
    code is multi-use until it expires or the 9-member cap is hit."""
    redis = get_redis()
    root = await redeem_share_code(db, redis, user=user, code=payload.code)
    trees = await fetch_shared_trees(db, user_id=user.id)
    tree = next((t for t in trees if t.root.id == root.id), None)
    return EnvelopeResponse.model_validate(root).model_copy(
        update={
            "is_shared": True,
            "role": "member",
            "shared_by_name": tree.owner_name if tree else None,
            "member_count": tree.member_count if tree else 0,
        }
    )


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


@router.post("/{envelope_id}/share", response_model=ShareCodeResponse)
async def share_envelope(
    envelope_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Owner mints a 24h code to share this ROOT envelope with up to 9 people.
    `_get_envelope` is owner-scoped, so a non-owner gets 404 — only the owner
    can share."""
    env = await _get_envelope(db, user_id=user.id, envelope_id=envelope_id)
    redis = get_redis()
    code, expires_at = await mint_share_code(db, redis, owner=user, envelope=env)
    return ShareCodeResponse(code=code, expires_at=expires_at)


@router.get("/{envelope_id}/members", response_model=list[MemberResponse])
async def list_envelope_members(
    envelope_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Everyone with access to a shared root: the owner first, then invited
    members (name only). Visible to the owner or any member."""
    env = (
        await db.execute(select(Envelope).where(Envelope.id == envelope_id))
    ).scalar_one_or_none()
    if env is None or env.parent_id is not None:
        raise HTTPException(status_code=404, detail="Sobre no encontrado.")
    is_owner = env.user_id == user.id
    if not is_owner and not await is_member(
        db, user_id=user.id, root_id=env.id
    ):
        raise HTTPException(status_code=404, detail="Sobre no encontrado.")
    owner = await db.get(User, env.user_id)
    out: list[MemberResponse] = []
    if owner is not None:
        out.append(
            MemberResponse(
                user_id=owner.id, full_name=owner.full_name, is_owner=True
            )
        )
    for uid, name in await list_members(db, root_id=env.id):
        out.append(MemberResponse(user_id=uid, full_name=name, is_owner=False))
    return out


@router.delete(
    "/{envelope_id}/members/{target_user_id}",
    response_model=MemberRemovedResponse,
)
async def remove_envelope_member(
    envelope_id: uuid.UUID,
    target_user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Owner removes any member; a member may remove only themselves (leave).
    The removed member's tagged transactions are unlinked, never deleted."""
    env = (
        await db.execute(select(Envelope).where(Envelope.id == envelope_id))
    ).scalar_one_or_none()
    if env is None or env.parent_id is not None:
        raise HTTPException(status_code=404, detail="Sobre no encontrado.")
    is_owner = env.user_id == user.id
    if not is_owner and user.id != target_user_id:
        raise HTTPException(
            status_code=403, detail="Solo el dueño puede quitar a otros miembros."
        )
    if target_user_id == env.user_id:
        raise HTTPException(
            status_code=400, detail="El dueño no se puede quitar del sobre."
        )
    await remove_member(db, root=env, target_user_id=target_user_id)
    return MemberRemovedResponse(removed_user_id=target_user_id)

"""Shared envelopes ("Sobres compartidos").

An envelope OWNER shares a ROOT envelope (its sub-sobres come along via the
subtree) with up to 9 other users. Sharing hands the recipients a short security
code (the Phase 6f device-code pattern), redeemed once each to create a
membership row.

Access model (surgical — widen ONLY where a member legitimately acts):

- The owner keeps FULL control: edit limit/class/structure, attach obligations,
  mint codes, remove members. The owner is implicit via `envelopes.user_id`;
  there is no membership row for them.
- A MEMBER can add/remove only their OWN expenses to/from the shared envelope
  (`can_assign_transaction_to_envelope`). They see the SHARED CAP bar (aggregate
  spend of all members) plus their own portion (`your_spent`) — never another
  member's transaction lines. They CANNOT edit limits/class/structure or attach
  bills/debts.
- A shared envelope a user is only a *member* of NEVER counts toward that
  member's own budget totals / cashflow / snapshots — `shared_summary_items`
  returns separately-flagged display-only items (`is_shared=True`), and the
  byte-locked consumers filter them out.

Share code: 6 chars, same unambiguous alphabet as `bot/pairing.py` /
`auth/device_code.py`. Stored in Redis at `envelope:share_code:{CODE}` for 24h.
Unlike device codes it is MULTI-USE — `redeem` reads (never pops) the key, so up
to 9 people can join with the same code until it expires or the cap is hit.
"""
from __future__ import annotations

import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.redis_keys import ENVELOPE_SHARE_CODE_TTL_S, share_code_key

from ..models.envelope import Envelope
from ..models.envelope_member import EnvelopeMember
from ..models.transaction import Transaction
from ..models.user import User
from ..schemas.envelopes import EnvelopeSummaryItem
from .envelopes import descendant_ids, fetch_envelopes
from .fx import convert

log = logging.getLogger("api.services.envelope_sharing")

# At most 9 invited members → ≤ 10 people total incl. the owner.
MAX_INVITED_MEMBERS = 9

# Same 31-char unambiguous alphabet as bot/pairing.py + auth/device_code.py.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LEN = 6


def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))


def normalize_code(raw: str) -> str:
    """Strip + uppercase so a pasted/lowercased code still matches the key."""
    return raw.strip().upper()


# ── share / redeem ────────────────────────────────────────────────────────────


async def mint_share_code(
    db: AsyncSession, redis: Redis, *, owner: User, envelope: Envelope
) -> tuple[str, datetime]:
    """Mint a 24h share code for a ROOT envelope the caller owns. The router
    already resolves `envelope` owner-scoped (404 otherwise), so the ownership
    check here is defensive."""
    if envelope.user_id != owner.id:
        raise HTTPException(
            status_code=403, detail="Solo el dueño del sobre puede compartirlo."
        )
    if envelope.parent_id is not None:
        raise HTTPException(
            status_code=400,
            detail="Solo se puede compartir un sobre raíz, no un sub-sobre.",
        )
    if envelope.archived:
        raise HTTPException(
            status_code=400, detail="No se puede compartir un sobre archivado."
        )
    code = _new_code()
    payload = json.dumps(
        {"envelope_id": str(envelope.id), "owner_id": str(owner.id)}
    )
    await redis.setex(share_code_key(code), ENVELOPE_SHARE_CODE_TTL_S, payload)
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=ENVELOPE_SHARE_CODE_TTL_S
    )
    return code, expires_at


async def redeem_share_code(
    db: AsyncSession, redis: Redis, *, user: User, code: str
) -> Envelope:
    """Join a shared envelope with a code. Multi-use (the key is read, not
    popped). Idempotent: re-redeeming as an existing member is a no-op. Enforces
    the ≤ 9 invited-member cap under a row lock so concurrent redeems can't
    overshoot it. Returns the joined ROOT envelope."""
    raw = await redis.get(share_code_key(normalize_code(code)))
    if not raw:
        raise HTTPException(status_code=400, detail="Código inválido o vencido.")
    try:
        data = json.loads(raw)
        envelope_id = uuid.UUID(data["envelope_id"])
    except (ValueError, TypeError, KeyError):
        log.warning("share_code_invalid_payload")
        raise HTTPException(status_code=400, detail="Código inválido o vencido.")

    # Lock the root row so concurrent redeems serialize against the cap count.
    root = (
        await db.execute(
            select(Envelope).where(Envelope.id == envelope_id).with_for_update()
        )
    ).scalar_one_or_none()
    if root is None or root.archived or root.parent_id is not None:
        raise HTTPException(
            status_code=400,
            detail="El sobre ya no está disponible para compartir.",
        )
    if root.user_id == user.id:
        raise HTTPException(
            status_code=400, detail="Ya sos el dueño de este sobre."
        )

    # Idempotent: already a member → return the root unchanged.
    already = (
        await db.execute(
            select(EnvelopeMember.id).where(
                EnvelopeMember.envelope_id == root.id,
                EnvelopeMember.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if already is not None:
        return root

    count = (
        await db.execute(
            select(func.count())
            .select_from(EnvelopeMember)
            .where(EnvelopeMember.envelope_id == root.id)
        )
    ).scalar_one()
    if count >= MAX_INVITED_MEMBERS:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Este sobre ya alcanzó el máximo de {MAX_INVITED_MEMBERS} "
                "personas invitadas."
            ),
        )

    db.add(
        EnvelopeMember(
            envelope_id=root.id, user_id=user.id, shared_by=root.user_id
        )
    )
    await db.commit()
    return root


# ── membership queries ────────────────────────────────────────────────────────


async def member_root_ids(
    db: AsyncSession, *, user_id: uuid.UUID
) -> list[uuid.UUID]:
    """Root envelope ids the user is an invited member of ("shared with me")."""
    rows = await db.execute(
        select(EnvelopeMember.envelope_id).where(
            EnvelopeMember.user_id == user_id
        )
    )
    return [rid for (rid,) in rows.all()]


async def is_member(
    db: AsyncSession, *, user_id: uuid.UUID, root_id: uuid.UUID
) -> bool:
    row = await db.execute(
        select(EnvelopeMember.id).where(
            EnvelopeMember.envelope_id == root_id,
            EnvelopeMember.user_id == user_id,
        )
    )
    return row.scalar_one_or_none() is not None


async def list_members(
    db: AsyncSession, *, root_id: uuid.UUID
) -> list[tuple[uuid.UUID, str]]:
    """Invited members of a root as ``(user_id, full_name)`` — name only, the
    minimum needed to render the list (data minimization)."""
    rows = await db.execute(
        select(User.id, User.full_name)
        .join(EnvelopeMember, EnvelopeMember.user_id == User.id)
        .where(EnvelopeMember.envelope_id == root_id)
        .order_by(User.full_name.asc())
    )
    return [(uid, name) for uid, name in rows.all()]


async def remove_member(
    db: AsyncSession, *, root: Envelope, target_user_id: uuid.UUID
) -> None:
    """Revoke a member: unlink that user's transactions tagged anywhere in the
    subtree (envelope_id → NULL, never deleting the transaction) and delete the
    membership row. Caller commits. Idempotent (no membership → just the
    unlink, which is also a no-op when nothing is tagged)."""
    owner_envs = await fetch_envelopes(
        db, user_id=root.user_id, include_archived=True
    )
    subtree_ids = [root.id, *descendant_ids(owner_envs, root.id)]
    await db.execute(
        update(Transaction)
        .where(
            Transaction.user_id == target_user_id,
            Transaction.envelope_id.in_(subtree_ids),
        )
        .values(envelope_id=None)
    )
    await db.execute(
        delete(EnvelopeMember).where(
            EnvelopeMember.envelope_id == root.id,
            EnvelopeMember.user_id == target_user_id,
        )
    )
    await db.commit()


# ── shared trees (read) ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class SharedTree:
    """One root envelope the user is a member of, plus the data needed to render
    it. ``envelopes`` is the OWNER's root + descendants (non-archived); the
    owner's other envelopes are loaded only to walk the tree and never returned
    (data minimization)."""

    root: Envelope
    owner_name: str
    member_count: int  # invited members + the owner
    envelopes: list[Envelope]  # root + descendants, owner's rows


async def fetch_shared_trees(
    db: AsyncSession, *, user_id: uuid.UUID
) -> list[SharedTree]:
    root_ids = await member_root_ids(db, user_id=user_id)
    if not root_ids:
        return []
    out: list[SharedTree] = []
    for rid in root_ids:
        root = (
            await db.execute(select(Envelope).where(Envelope.id == rid))
        ).scalar_one_or_none()
        # A root the owner archived (or that isn't a root anymore) drops out of
        # the member's view too.
        if root is None or root.archived or root.parent_id is not None:
            continue
        owner_envs = await fetch_envelopes(
            db, user_id=root.user_id, include_archived=False
        )
        sub_ids = set(descendant_ids(owner_envs, root.id))
        subtree = [e for e in owner_envs if e.id == root.id or e.id in sub_ids]
        owner = await db.get(User, root.user_id)
        owner_name = owner.full_name if owner is not None else "—"
        count = (
            await db.execute(
                select(func.count())
                .select_from(EnvelopeMember)
                .where(EnvelopeMember.envelope_id == root.id)
            )
        ).scalar_one()
        out.append(
            SharedTree(
                root=root,
                owner_name=owner_name,
                member_count=count + 1,  # + owner
                envelopes=subtree,
            )
        )
    return out


async def shared_summary_items(
    db: AsyncSession, *, user: User, start, end
) -> list[EnvelopeSummaryItem]:
    """Summary items for the shared envelopes the user is a MEMBER of, to append
    to the home-tab feed. Each item carries `is_shared=True`, the aggregate
    rolled-up `spent` (all members), and the member's own `your_spent`. Per-node
    figures are in the node's own currency (like the own items); these are
    display-only and never folded into the owner-scoped grand totals."""
    trees = await fetch_shared_trees(db, user_id=user.id)
    if not trees:
        return []

    items: list[EnvelopeSummaryItem] = []
    for tree in trees:
        subtree = tree.envelopes
        ids = [e.id for e in subtree]
        if not ids:
            continue

        # Aggregate spend (ALL members) + this member's own portion, per
        # node+currency. Dropping the user_id filter is what makes the cap shared;
        # the CASE isolates the caller's own spend for "Vos: ₡X de ₡Y".
        rows = await db.execute(
            select(
                Transaction.envelope_id,
                Transaction.currency,
                func.coalesce(func.sum(func.abs(Transaction.amount)), 0),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                Transaction.user_id == user.id,
                                func.abs(Transaction.amount),
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
            )
            .where(
                Transaction.envelope_id.in_(ids),
                Transaction.amount < 0,
                Transaction.archived.is_(False),
                Transaction.status == "confirmed",
                Transaction.transaction_date >= start,
                Transaction.transaction_date < end,
            )
            .group_by(Transaction.envelope_id, Transaction.currency)
        )
        agg_raw: dict = {}
        your_raw: dict = {}
        for env_id, tx_currency, agg_total, your_total in rows.all():
            agg_raw.setdefault(env_id, []).append(
                (tx_currency, Decimal(agg_total))
            )
            your_raw.setdefault(env_id, []).append(
                (tx_currency, Decimal(your_total))
            )

        # Direct spend per node in the node's own currency (children inherit the
        # root currency, so the within-tree roll-up needs no further FX).
        direct_agg: dict[uuid.UUID, Decimal] = {}
        direct_your: dict[uuid.UUID, Decimal] = {}
        for e in subtree:
            da = Decimal("0")
            dy = Decimal("0")
            for txc, amt in agg_raw.get(e.id, []):
                da += convert(amt, txc, e.currency)
            for txc, amt in your_raw.get(e.id, []):
                dy += convert(amt, txc, e.currency)
            direct_agg[e.id] = da
            direct_your[e.id] = dy

        cmap: dict = {}
        for e in subtree:
            cmap.setdefault(e.parent_id, []).append(e)

        rolled_agg: dict[uuid.UUID, Decimal] = {}
        rolled_your: dict[uuid.UUID, Decimal] = {}

        def _roll(direct: dict, store: dict, eid: uuid.UUID) -> Decimal:
            cached = store.get(eid)
            if cached is not None:
                return cached
            total = direct.get(eid, Decimal("0"))
            for child in cmap.get(eid, []):
                total += _roll(direct, store, child.id)
            store[eid] = total
            return total

        for e in subtree:
            _roll(direct_agg, rolled_agg, e.id)
            _roll(direct_your, rolled_your, e.id)

        for e in subtree:
            limit_dec = Decimal(e.limit_amount)
            limit_f = float(limit_dec)
            spent_dec = rolled_agg[e.id]
            spent_f = float(spent_dec)
            items.append(
                EnvelopeSummaryItem(
                    id=e.id,
                    parent_id=e.parent_id,
                    depth=e.depth,
                    name=e.name,
                    envelope_class=e.envelope_class,
                    currency=e.currency,
                    limit_amount=round(limit_f, 2),
                    spent=round(spent_f, 2),
                    # Member's OWN direct spend on this node (non-leaky).
                    direct_spent=round(float(direct_your.get(e.id, Decimal("0"))), 2),
                    reserved=0.0,
                    available=round(limit_f - spent_f, 2),
                    remaining=round(limit_f - spent_f, 2),
                    pct=round((spent_f / limit_f) if limit_f > 0 else 0.0, 4),
                    over_limit=spent_dec > limit_dec,
                    allocated=0.0,
                    unallocated=0.0,
                    over_allocated=False,
                    is_shared=True,
                    role="member",
                    shared_by_name=tree.owner_name,
                    member_count=tree.member_count,
                    your_spent=round(float(rolled_your[e.id]), 2),
                )
            )
    return items

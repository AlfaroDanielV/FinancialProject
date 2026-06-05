import base64
import csv
import io
import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user, current_user_via_token
from ..models.account import Account
from ..models.envelope import Envelope
from ..models.transaction import Transaction
from ..models.user import User
from ..models.user_category import UserCategory
from ..schemas.transaction import (
    ShortcutTransactionCreate,
    TransactionBulkArchive,
    TransactionBulkCategorize,
    TransactionBulkResponse,
    TransactionCreate,
    TransactionListResponse,
    TransactionResponse,
    TransactionUpdate,
)

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])

TransactionKind = Literal["all", "income", "expense", "transfer"]
SortBy = Literal["date", "amount", "category"]
SortDir = Literal["asc", "desc"]

CSV_EXPORT_MAX_ROWS = 50_000


def _encode_cursor(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(token: str) -> dict:
    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + pad)
        return json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=400, detail="Cursor inválido."
        ) from exc


# ── iPhone Shortcut endpoint ────────────────────────────────────────────────

@router.post("/shortcut", response_model=TransactionResponse, status_code=201)
async def shortcut_transaction(
    payload: ShortcutTransactionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_via_token),
):
    """
    Webhook for the iPhone Shortcut.

    Headers:
        X-Shortcut-Token: <user.shortcut_token returned at registration>

    Body:
        {
          "amount": 5000,
          "merchant": "Supermercado Buen Precio",
          "category": "supermercado",
          "is_expense": true,
          "description": "compras semana",
          "transaction_date": "2026-04-12"
        }

    user_id is attached server-side from the resolved token; the Shortcut
    must NOT send it in the body.
    """
    signed_amount = -abs(payload.amount) if payload.is_expense else abs(payload.amount)

    txn = Transaction(
        user_id=user.id,
        amount=signed_amount,
        currency="CRC",
        merchant=payload.merchant,
        description=payload.description,
        category=payload.category,
        transaction_date=payload.transaction_date or date.today(),
        source="shortcut",
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)
    return txn


# ── Standard CRUD ────────────────────────────────────────────────────────────

@router.post("", response_model=TransactionResponse, status_code=201)
async def create_transaction(
    payload: TransactionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    if payload.account_id is not None:
        account_result = await db.execute(
            select(Account.id).where(
                Account.id == payload.account_id,
                Account.user_id == user.id,
                Account.archived.is_(False),
            )
        )
        if account_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=400, detail="Cuenta inválida.")
    if payload.category_id is not None:
        category_result = await db.execute(
            select(UserCategory.id).where(
                UserCategory.id == payload.category_id,
                UserCategory.user_id == user.id,
                UserCategory.archived.is_(False),
            )
        )
        if category_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=400, detail="Categoría inválida.")

    txn = Transaction(
        user_id=user.id,
        account_id=payload.account_id,
        category_id=payload.category_id,
        amount=payload.amount,
        currency=payload.currency,
        merchant=payload.merchant,
        description=payload.description,
        category=payload.category,
        subcategory=payload.subcategory,
        transaction_date=payload.transaction_date,
        source=payload.source,
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)
    return txn


def _build_filters(
    *,
    user_id: uuid.UUID,
    account_id: Optional[uuid.UUID],
    account_ids: Optional[list[uuid.UUID]],
    category_id: Optional[uuid.UUID],
    category_ids: Optional[list[uuid.UUID]],
    from_date: Optional[date],
    to_date: Optional[date],
    kind: TransactionKind,
    currency: Optional[str],
    min_amount: Optional[Decimal],
    max_amount: Optional[Decimal],
    q: Optional[str],
    include_archived: bool,
) -> list:
    filters: list = [Transaction.user_id == user_id]
    accounts_filter = []
    if account_id is not None:
        accounts_filter.append(account_id)
    if account_ids:
        accounts_filter.extend(account_ids)
    if accounts_filter:
        filters.append(Transaction.account_id.in_(set(accounts_filter)))

    categories_filter = []
    if category_id is not None:
        categories_filter.append(category_id)
    if category_ids:
        categories_filter.extend(category_ids)
    if categories_filter:
        filters.append(Transaction.category_id.in_(set(categories_filter)))

    if from_date is not None:
        filters.append(Transaction.transaction_date >= from_date)
    if to_date is not None:
        filters.append(Transaction.transaction_date <= to_date)
    if kind == "income":
        filters.append(Transaction.transfer_id.is_(None))
        filters.append(Transaction.amount > 0)
    elif kind == "expense":
        filters.append(Transaction.transfer_id.is_(None))
        filters.append(Transaction.amount < 0)
    elif kind == "transfer":
        filters.append(Transaction.transfer_id.is_not(None))
    if currency:
        filters.append(Transaction.currency == currency.upper())
    if min_amount is not None:
        filters.append(Transaction.amount >= min_amount)
    if max_amount is not None:
        filters.append(Transaction.amount <= max_amount)
    if q:
        like = f"%{q.strip()}%"
        filters.append(
            or_(
                Transaction.description.ilike(like),
                Transaction.merchant.ilike(like),
            )
        )
    if not include_archived:
        filters.append(Transaction.archived.is_(False))
    return filters


def _order_by(sort_by: SortBy, sort_dir: SortDir):
    direction = (lambda col: col.desc()) if sort_dir == "desc" else (
        lambda col: col.asc()
    )
    if sort_by == "amount":
        return [direction(Transaction.amount), direction(Transaction.id)]
    if sort_by == "category":
        return [
            direction(Transaction.category),
            direction(Transaction.id),
        ]
    return [
        direction(Transaction.transaction_date),
        direction(Transaction.created_at),
        direction(Transaction.id),
    ]


def _apply_cursor(
    stmt,
    *,
    cursor: dict,
    sort_by: SortBy,
    sort_dir: SortDir,
):
    """Date-sorted cursor only. Other sorts ignore the cursor and fall
    back to offset pagination (rarely used; documented in B5 status).
    """
    if sort_by != "date":
        return stmt
    cursor_date = cursor.get("d")
    cursor_id = cursor.get("i")
    if not cursor_date or not cursor_id:
        return stmt
    try:
        last_date = date.fromisoformat(cursor_date)
        last_id = uuid.UUID(cursor_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="Cursor inválido."
        ) from exc

    if sort_dir == "desc":
        return stmt.where(
            or_(
                Transaction.transaction_date < last_date,
                and_(
                    Transaction.transaction_date == last_date,
                    Transaction.id < last_id,
                ),
            )
        )
    return stmt.where(
        or_(
            Transaction.transaction_date > last_date,
            and_(
                Transaction.transaction_date == last_date,
                Transaction.id > last_id,
            ),
        )
    )


@router.get("", response_model=TransactionListResponse)
async def list_transactions(
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    cursor: Optional[str] = Query(default=None),
    account_id: Optional[uuid.UUID] = Query(default=None),
    account_ids: Optional[list[uuid.UUID]] = Query(default=None),
    category_id: Optional[uuid.UUID] = Query(default=None),
    category_ids: Optional[list[uuid.UUID]] = Query(default=None),
    from_date: Optional[date] = Query(default=None),
    to_date: Optional[date] = Query(default=None),
    kind: TransactionKind = Query(default="all"),
    currency: Optional[str] = Query(default=None, min_length=3, max_length=3),
    min_amount: Optional[Decimal] = Query(default=None),
    max_amount: Optional[Decimal] = Query(default=None),
    q: Optional[str] = Query(default=None, max_length=200),
    sort_by: SortBy = Query(default="date"),
    sort_dir: SortDir = Query(default="desc"),
    include_archived: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    filters = _build_filters(
        user_id=user.id,
        account_id=account_id,
        account_ids=account_ids,
        category_id=category_id,
        category_ids=category_ids,
        from_date=from_date,
        to_date=to_date,
        kind=kind,
        currency=currency,
        min_amount=min_amount,
        max_amount=max_amount,
        q=q,
        include_archived=include_archived,
    )

    count_result = await db.execute(select(func.count()).where(*filters))
    total = count_result.scalar_one()

    stmt = select(Transaction).where(*filters).order_by(*_order_by(sort_by, sort_dir))

    if cursor and sort_by == "date":
        decoded = _decode_cursor(cursor)
        stmt = _apply_cursor(
            stmt, cursor=decoded, sort_by=sort_by, sort_dir=sort_dir
        )
    else:
        stmt = stmt.offset(offset)

    stmt = stmt.limit(limit + 1)

    result = await db.execute(stmt)
    items = list(result.scalars().all())
    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = None
    if has_more and sort_by == "date" and items:
        last = items[-1]
        next_cursor = _encode_cursor(
            {"d": last.transaction_date.isoformat(), "i": str(last.id)}
        )

    return TransactionListResponse(total=total, items=items, next_cursor=next_cursor)


@router.get("/export")
async def export_transactions_csv(
    account_id: Optional[uuid.UUID] = Query(default=None),
    account_ids: Optional[list[uuid.UUID]] = Query(default=None),
    category_id: Optional[uuid.UUID] = Query(default=None),
    category_ids: Optional[list[uuid.UUID]] = Query(default=None),
    from_date: Optional[date] = Query(default=None),
    to_date: Optional[date] = Query(default=None),
    kind: TransactionKind = Query(default="all"),
    currency: Optional[str] = Query(default=None, min_length=3, max_length=3),
    min_amount: Optional[Decimal] = Query(default=None),
    max_amount: Optional[Decimal] = Query(default=None),
    q: Optional[str] = Query(default=None, max_length=200),
    include_archived: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    filters = _build_filters(
        user_id=user.id,
        account_id=account_id,
        account_ids=account_ids,
        category_id=category_id,
        category_ids=category_ids,
        from_date=from_date,
        to_date=to_date,
        kind=kind,
        currency=currency,
        min_amount=min_amount,
        max_amount=max_amount,
        q=q,
        include_archived=include_archived,
    )

    count_result = await db.execute(select(func.count()).where(*filters))
    total = int(count_result.scalar_one())
    if total > CSV_EXPORT_MAX_ROWS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Refiná los filtros (encontramos {total:,} filas, "
                f"máximo {CSV_EXPORT_MAX_ROWS:,})."
            ),
        )

    result = await db.execute(
        select(Transaction)
        .where(*filters)
        .order_by(
            Transaction.transaction_date.desc(),
            Transaction.created_at.desc(),
        )
    )
    rows = list(result.scalars().all())

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "transaction_date",
            "amount",
            "currency",
            "merchant",
            "description",
            "category",
            "account_id",
            "status",
            "source",
            "transfer_id",
            "id",
        ]
    )
    for txn in rows:
        writer.writerow(
            [
                txn.transaction_date.isoformat(),
                f"{Decimal(str(txn.amount)):.2f}",
                txn.currency,
                txn.merchant or "",
                (txn.description or "").replace("\n", " "),
                txn.category or "",
                str(txn.account_id) if txn.account_id else "",
                txn.status,
                txn.source,
                str(txn.transfer_id) if txn.transfer_id else "",
                str(txn.id),
            ]
        )

    buffer.seek(0)
    filename = f"transacciones-{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


async def _load_owned_transactions(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    transaction_ids: list[uuid.UUID],
) -> list[Transaction]:
    if not transaction_ids:
        return []
    result = await db.execute(
        select(Transaction).where(
            Transaction.id.in_(transaction_ids),
            Transaction.user_id == user_id,
        )
    )
    return list(result.scalars().all())


def _enforce_bulk_eligible(
    txns: list[Transaction],
    requested_ids: list[uuid.UUID],
) -> None:
    """All-or-nothing guard. Raises 409 if any row violates the rules.

    Rules:
    - Every requested ID must exist and belong to the caller.
    - No shadow / pending_review rows.
    - No transfer legs.
    """
    found = {txn.id for txn in txns}
    missing = [tid for tid in requested_ids if tid not in found]
    if missing:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "missing_transactions",
                "ids": [str(x) for x in missing],
            },
        )
    bad = [
        str(t.id)
        for t in txns
        if t.status != "confirmed" or t.transfer_id is not None
    ]
    if bad:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ineligible_transactions",
                "ids": bad,
                "message": (
                    "No se pueden modificar en bulk: filas pendientes de "
                    "aprobar o partes de una transferencia."
                ),
            },
        )


@router.post("/bulk/archive", response_model=TransactionBulkResponse)
async def bulk_archive_transactions(
    payload: TransactionBulkArchive,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    txns = await _load_owned_transactions(
        db, user_id=user.id, transaction_ids=payload.transaction_ids
    )
    _enforce_bulk_eligible(txns, payload.transaction_ids)
    for txn in txns:
        txn.archived = True
    await db.commit()
    return TransactionBulkResponse(updated=len(txns))


@router.post("/bulk/restore", response_model=TransactionBulkResponse)
async def bulk_restore_transactions(
    payload: TransactionBulkArchive,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    txns = await _load_owned_transactions(
        db, user_id=user.id, transaction_ids=payload.transaction_ids
    )
    _enforce_bulk_eligible(txns, payload.transaction_ids)
    for txn in txns:
        txn.archived = False
    await db.commit()
    return TransactionBulkResponse(updated=len(txns))


@router.post("/bulk/categorize", response_model=TransactionBulkResponse)
async def bulk_categorize_transactions(
    payload: TransactionBulkCategorize,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    if payload.category_id is not None:
        category_result = await db.execute(
            select(UserCategory.id).where(
                UserCategory.id == payload.category_id,
                UserCategory.user_id == user.id,
                UserCategory.archived.is_(False),
            )
        )
        if category_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=400, detail="Categoría inválida.")

    txns = await _load_owned_transactions(
        db, user_id=user.id, transaction_ids=payload.transaction_ids
    )
    _enforce_bulk_eligible(txns, payload.transaction_ids)
    for txn in txns:
        txn.category_id = payload.category_id
    await db.commit()
    return TransactionBulkResponse(updated=len(txns))


@router.get("/{transaction_id}", response_model=TransactionResponse)
async def get_transaction(
    transaction_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.user_id == user.id,
        )
    )
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(status_code=404, detail="Transacción no encontrada.")
    return txn


@router.patch("/{transaction_id}/flag", response_model=TransactionResponse)
async def flag_transaction(
    transaction_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.user_id == user.id,
        )
    )
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(status_code=404, detail="Transacción no encontrada.")

    txn.parse_status = "flagged"
    await db.commit()
    await db.refresh(txn)
    return txn


@router.patch("/{transaction_id}", response_model=TransactionResponse)
async def update_transaction(
    transaction_id: uuid.UUID,
    payload: TransactionUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Phase 6e B4 — edit amount, category, merchant, description, date.

    Shadow rows (status='shadow') and transfer legs are immutable here:
    shadow approval flows through /aprobar_shadow in the bot, and transfer
    legs can only change via the parent transfer.
    """
    result = await db.execute(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.user_id == user.id,
        )
    )
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(status_code=404, detail="Transacción no encontrada.")

    if txn.status != "confirmed":
        raise HTTPException(
            status_code=409,
            detail=(
                "Esta transacción está pendiente de aprobar. "
                "Usá /aprobar_shadow o /rechazar_shadow en el bot."
            ),
        )
    if txn.transfer_id is not None:
        raise HTTPException(
            status_code=409,
            detail="Editá la transferencia, no sus movimientos individuales.",
        )
    if txn.archived:
        raise HTTPException(
            status_code=409,
            detail="Restaurá la transacción antes de editarla.",
        )

    update_data = payload.model_dump(exclude_unset=True)

    if "category_id" in update_data and update_data["category_id"] is not None:
        category_result = await db.execute(
            select(UserCategory.id).where(
                UserCategory.id == update_data["category_id"],
                UserCategory.user_id == user.id,
                UserCategory.archived.is_(False),
            )
        )
        if category_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=400, detail="Categoría inválida.")

    if "envelope_id" in update_data and update_data["envelope_id"] is not None:
        envelope_result = await db.execute(
            select(Envelope.id).where(
                Envelope.id == update_data["envelope_id"],
                Envelope.user_id == user.id,
                Envelope.archived.is_(False),
            )
        )
        if envelope_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=400, detail="Sobre inválido.")

    for field, value in update_data.items():
        setattr(txn, field, value)

    await db.commit()
    await db.refresh(txn)
    return txn

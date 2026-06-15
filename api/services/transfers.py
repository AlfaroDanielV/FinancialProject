from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.account import Account
from ..models.credit_card_terms import CreditCardTerms
from ..models.transaction import Transaction
from ..models.transfer import Transfer
from ..schemas.transfers import TransferCreate
from .accounts import compute_account_balances


@dataclass
class TransferCreationResult:
    transfer: Transfer
    debit_transaction_id: uuid.UUID
    credit_transaction_id: uuid.UUID


def _fmt(amount: Decimal, currency: str) -> str:
    """Compact money label for error copy — ₡/$ prefix, 2 decimals."""
    prefix = {"CRC": "₡", "USD": "$"}.get(currency, f"{currency} ")
    return f"{prefix}{amount:,.2f}"


async def create_transfer_with_transactions(
    db: AsyncSession, *, user_id: uuid.UUID, payload: TransferCreate
) -> TransferCreationResult:
    if payload.from_account_id == payload.to_account_id:
        raise HTTPException(
            status_code=400,
            detail="La cuenta origen y destino tienen que ser distintas.",
        )

    result = await db.execute(
        select(Account).where(
            Account.user_id == user_id,
            Account.id.in_([payload.from_account_id, payload.to_account_id]),
        )
    )
    accounts = {account.id: account for account in result.scalars().all()}
    from_account = accounts.get(payload.from_account_id)
    to_account = accounts.get(payload.to_account_id)
    if from_account is None or to_account is None:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")
    if from_account.archived or to_account.archived:
        raise HTTPException(
            status_code=400,
            detail="No se puede transferir con cuentas archivadas.",
        )

    from_ccy = from_account.currency
    to_ccy = to_account.currency
    # `payload.currency` is the currency the typed `amount` is expressed in
    # (the input currency / mode selector). It must be one of the two accounts.
    input_ccy = payload.currency.upper()
    if input_ccy not in (from_ccy, to_ccy):
        raise HTTPException(
            status_code=400,
            detail=(
                "La moneda del monto tiene que ser la de la cuenta origen "
                "o la de destino."
            ),
        )

    amount_in = Decimal(payload.amount)

    # Canonical fx_rate direction: units of the FROM-account (funding) currency
    # per 1 unit of the TO-account (credit/destination) currency — e.g. CRC per
    # 1 USD ≈ 520. `debited` leaves the funding account (from_ccy); `applied`
    # lands on the destination (to_ccy). All Decimal, quantized to cents.
    if from_ccy == to_ccy:
        # Same currency: no exchange. Force rate 1, store no fx_rate.
        debited = amount_in.quantize(Decimal("0.01"))
        applied = debited
        stored_fx = None
    else:
        if payload.fx_rate is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Necesitás indicar el tipo de cambio para transferencias "
                    "entre monedas distintas."
                ),
            )
        rate = Decimal(payload.fx_rate)
        if rate <= 0:
            raise HTTPException(
                status_code=400,
                detail="El tipo de cambio tiene que ser mayor que cero.",
            )
        if input_ccy == to_ccy:
            # Mode A — amount typed in the destination (credit) currency.
            applied = amount_in.quantize(Decimal("0.01"))
            debited = (amount_in * rate).quantize(Decimal("0.01"))
        else:
            # Mode B — amount typed in the funding (source) currency.
            debited = amount_in.quantize(Decimal("0.01"))
            applied = (amount_in / rate).quantize(Decimal("0.01"))
        stored_fx = rate

    # Funds guard (always in funding currency, single source of truth): the
    # amount leaving the funding account must not exceed its current balance.
    balances = await compute_account_balances(
        db, user_id=user_id, account_ids=[from_account.id]
    )
    current = balances.get(from_account.id)
    current_balance = current.current if current else Decimal("0")
    if debited > current_balance:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Fondos insuficientes en «{from_account.name}». "
                f"Necesitás {_fmt(debited, from_ccy)} y tenés "
                f"{_fmt(current_balance, from_ccy)}."
            ),
        )

    occurred_at = payload.occurred_at or datetime.now(timezone.utc)
    transfer = Transfer(
        user_id=user_id,
        from_account_id=from_account.id,
        to_account_id=to_account.id,
        amount=debited,
        currency=from_ccy,
        fx_rate=stored_fx,
        occurred_at=occurred_at,
        notes=payload.notes,
    )
    db.add(transfer)
    await db.flush()

    description = payload.notes or (
        f"Transferencia {from_account.name} -> {to_account.name}"
    )

    # Phase 7b B5: when the destination card is attached to an envelope, stamp
    # the DEBIT leg with that envelope so the card's reservation swaps to
    # spend when paid (never both). This deterministic path is the ONLY one
    # that puts an envelope_id on a transfer leg — PATCH still 409s legs.
    debit_envelope_id = None
    if to_account.account_type == "credit":
        terms_envelope = (
            await db.execute(
                select(CreditCardTerms.envelope_id).where(
                    CreditCardTerms.account_id == to_account.id,
                    CreditCardTerms.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        debit_envelope_id = terms_envelope

    debit = Transaction(
        user_id=user_id,
        account_id=from_account.id,
        transfer_id=transfer.id,
        amount=-debited,
        currency=from_account.currency,
        merchant=to_account.name,
        description=description,
        category="transferencia",
        transaction_date=occurred_at.date(),
        source="manual",
        envelope_id=debit_envelope_id,
    )
    credit = Transaction(
        user_id=user_id,
        account_id=to_account.id,
        transfer_id=transfer.id,
        amount=applied,
        currency=to_account.currency,
        merchant=from_account.name,
        description=description,
        category="transferencia",
        transaction_date=occurred_at.date(),
        source="manual",
    )
    db.add_all([debit, credit])
    await db.flush()

    return TransferCreationResult(
        transfer=transfer,
        debit_transaction_id=debit.id,
        credit_transaction_id=credit.id,
    )


async def delete_transfer_with_transactions(
    db: AsyncSession, *, user_id: uuid.UUID, transfer_id: uuid.UUID
) -> bool:
    """Hard-delete a transfer and BOTH its legs — Phase 7b chat undo.

    A mistaken transfer must remove both rows, otherwise one account keeps a
    phantom movement. Returns False when the transfer doesn't exist or belongs
    to another user (caller replies 'no encontré la última acción')."""
    transfer = (
        await db.execute(
            select(Transfer).where(
                Transfer.id == transfer_id, Transfer.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if transfer is None:
        return False
    await db.execute(
        delete(Transaction).where(Transaction.transfer_id == transfer.id)
    )
    await db.delete(transfer)
    await db.commit()
    return True

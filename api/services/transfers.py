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


@dataclass
class TransferCreationResult:
    transfer: Transfer
    debit_transaction_id: uuid.UUID
    credit_transaction_id: uuid.UUID


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

    currency = payload.currency.upper()
    if currency != from_account.currency:
        raise HTTPException(
            status_code=400,
            detail=(
                "La moneda de la transferencia tiene que coincidir con "
                "la cuenta origen."
            ),
        )

    amount = Decimal(payload.amount)
    if from_account.currency != to_account.currency and payload.fx_rate is None:
        raise HTTPException(
            status_code=400,
            detail="Necesitás indicar el tipo de cambio para transferencias mixtas.",
        )

    destination_amount = amount
    if from_account.currency != to_account.currency:
        destination_amount = (amount * Decimal(payload.fx_rate)).quantize(
            Decimal("0.01")
        )

    occurred_at = payload.occurred_at or datetime.now(timezone.utc)
    transfer = Transfer(
        user_id=user_id,
        from_account_id=from_account.id,
        to_account_id=to_account.id,
        amount=amount,
        currency=currency,
        fx_rate=payload.fx_rate,
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
        amount=-amount,
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
        amount=destination_amount,
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

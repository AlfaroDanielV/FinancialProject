"""Credit-account payment fix — cross-currency conversion direction + the
funds guard, both deterministic in the shared transfers service.

Canonical fx_rate = units of the FROM-account (funding) currency per 1 unit of
the TO-account (credit/destination) currency, e.g. CRC per USD ≈ 520.
- Mode B (amount typed in the funding currency): applied = amount ÷ rate.
- Mode A (amount typed in the destination currency): debited = amount × rate.
The amount leaving the funding account must never exceed its balance.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from api.models.account import Account
from api.models.transaction import Transaction
from api.models.transfer import Transfer
from api.schemas.transfers import TransferCreate
from api.services.transfers import create_transfer_with_transactions


async def _add_account(
    session,
    user_id,
    name: str,
    *,
    account_type: str = "checking",
    currency: str = "CRC",
    initial_balance: Decimal = Decimal("0"),
) -> Account:
    account = Account(
        user_id=user_id,
        name=name,
        account_type=account_type,
        currency=currency,
        initial_balance=initial_balance,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _leg_amounts(session, transfer_id) -> list[Decimal]:
    rows = await session.execute(
        select(Transaction).where(Transaction.transfer_id == transfer_id)
    )
    return sorted(Decimal(t.amount) for t in rows.scalars().all())


@pytest.mark.asyncio
async def test_mode_b_funding_currency_divides(db_with_user):
    """Done-when #1: pay ₡520.000 at rate 520 from CRC checking to a USD card →
    applied $1.000, debited ₡520.000 (was inflating to ~270M with the old ×)."""
    session, user_id = db_with_user
    crc = await _add_account(
        session, user_id, "Corriente", initial_balance=Decimal("600000")
    )
    usd = await _add_account(
        session, user_id, "Visa USD", account_type="credit", currency="USD"
    )

    result = await create_transfer_with_transactions(
        session,
        user_id=user_id,
        payload=TransferCreate(
            from_account_id=crc.id,
            to_account_id=usd.id,
            amount=Decimal("520000"),
            currency="CRC",  # input in the funding currency → Mode B
            fx_rate=Decimal("520"),
        ),
    )
    await session.commit()

    assert result.transfer.amount == Decimal("520000.00")
    assert result.transfer.currency == "CRC"
    assert result.transfer.fx_rate == Decimal("520")
    assert await _leg_amounts(session, result.transfer.id) == [
        Decimal("-520000.00"),
        Decimal("1000.00"),
    ]


@pytest.mark.asyncio
async def test_mode_a_destination_currency_multiplies(db_with_user):
    """Done-when #2: same payment entered as $1.000 (Mode A) at rate 520 →
    debited ₡520.000, applied $1.000."""
    session, user_id = db_with_user
    crc = await _add_account(
        session, user_id, "Corriente", initial_balance=Decimal("600000")
    )
    usd = await _add_account(
        session, user_id, "Visa USD", account_type="credit", currency="USD"
    )

    result = await create_transfer_with_transactions(
        session,
        user_id=user_id,
        payload=TransferCreate(
            from_account_id=crc.id,
            to_account_id=usd.id,
            amount=Decimal("1000"),
            currency="USD",  # input in the destination currency → Mode A
            fx_rate=Decimal("520"),
        ),
    )
    await session.commit()

    assert result.transfer.amount == Decimal("520000.00")
    assert await _leg_amounts(session, result.transfer.id) == [
        Decimal("-520000.00"),
        Decimal("1000.00"),
    ]


@pytest.mark.asyncio
async def test_funds_guard_rejects_and_writes_no_rows(db_with_user):
    """Done-when #3: a USD 18.000.000 payment from a ₡600.000 checking account
    is rejected; no transfer/ledger rows are written."""
    session, user_id = db_with_user
    crc = await _add_account(
        session, user_id, "Corriente", initial_balance=Decimal("600000")
    )
    usd = await _add_account(
        session, user_id, "Visa USD", account_type="credit", currency="USD"
    )

    with pytest.raises(HTTPException) as exc:
        await create_transfer_with_transactions(
            session,
            user_id=user_id,
            payload=TransferCreate(
                from_account_id=crc.id,
                to_account_id=usd.id,
                amount=Decimal("18000000"),
                currency="USD",  # Mode A → debited = 18M × 520 ≫ ₡600.000
                fx_rate=Decimal("520"),
            ),
        )
    assert exc.value.status_code == 400
    assert "Fondos insuficientes" in exc.value.detail

    transfers = (
        await session.execute(select(func.count()).select_from(Transfer))
    ).scalar_one()
    legs = (
        await session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.transfer_id.isnot(None))
        )
    ).scalar_one()
    assert transfers == 0
    assert legs == 0


@pytest.mark.asyncio
async def test_same_currency_forces_rate_one_and_still_guards(db_with_user):
    """Done-when #4: same-currency transfer ignores fx, stores no rate, and the
    funds guard is still enforced."""
    session, user_id = db_with_user
    src = await _add_account(
        session, user_id, "Corriente", initial_balance=Decimal("10000")
    )
    dst = await _add_account(session, user_id, "Ahorros")

    # Sufficient funds: ₡5.000 of ₡10.000.
    result = await create_transfer_with_transactions(
        session,
        user_id=user_id,
        payload=TransferCreate(
            from_account_id=src.id,
            to_account_id=dst.id,
            amount=Decimal("5000"),
            currency="CRC",
        ),
    )
    await session.commit()
    assert result.transfer.fx_rate is None
    assert await _leg_amounts(session, result.transfer.id) == [
        Decimal("-5000.00"),
        Decimal("5000.00"),
    ]

    # Overdraw: ₡20.000 from the remaining ₡5.000 → rejected.
    with pytest.raises(HTTPException) as exc:
        await create_transfer_with_transactions(
            session,
            user_id=user_id,
            payload=TransferCreate(
                from_account_id=src.id,
                to_account_id=dst.id,
                amount=Decimal("20000"),
                currency="CRC",
            ),
        )
    assert exc.value.status_code == 400
    assert "Fondos insuficientes" in exc.value.detail

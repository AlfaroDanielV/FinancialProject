from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select

from api.models.account import Account
from api.models.transaction import Transaction

from app.queries.session import AsyncSessionLocal

from ._common import (
    as_decimal,
    fuzzy_any,
    signed_decimal_to_string,
    user_currency,
)
from .base import is_tool_registered, query_tool


GET_ACCOUNT_BALANCE_DESCRIPTION = (
    "Devuelve el saldo actual de una o todas las cuentas del usuario. "
    "Usá esto cuando el usuario pregunte cuánto tiene, cuánto le queda, "
    "o el saldo de una cuenta específica. El balance se calcula sumando "
    "todas las transacciones (gastos restan, ingresos suman). Cuentas de "
    "tipo crédito pueden tener balance negativo (deuda pendiente)."
)

LIST_ACCOUNTS_DESCRIPTION = (
    "Lista todas las cuentas del usuario con sus metadatos básicos (nombre "
    "y tipo). No incluye balances — para saldos usá get_account_balance. "
    "Usá esto cuando el usuario pregunte qué cuentas tiene, o necesite "
    "decidir cuál usar."
)


async def get_account_balance(
    *,
    account_name: Optional[str] = None,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        currency = await user_currency(db, user_id)

        filters: list[Any] = [Account.user_id == user_id]
        if account_name and account_name.strip():
            filters.append(fuzzy_any(Account.name, [account_name]))

        balance_subq = (
            select(
                Transaction.account_id.label("acct_id"),
                func.coalesce(func.sum(Transaction.amount), Decimal("0")).label(
                    "balance"
                ),
                func.max(Transaction.transaction_date).label("last_date"),
            )
            .where(Transaction.user_id == user_id)
            .group_by(Transaction.account_id)
            .subquery()
        )

        stmt = (
            select(
                Account.id,
                Account.name,
                Account.account_type,
                Account.is_active,
                func.coalesce(balance_subq.c.balance, Decimal("0")).label("balance"),
                balance_subq.c.last_date,
            )
            .select_from(Account)
            .outerjoin(balance_subq, Account.id == balance_subq.c.acct_id)
            .where(*filters)
            .order_by(Account.name.asc())
        )
        rows = list((await db.execute(stmt)).all())

    accounts: list[dict[str, Any]] = []
    total = Decimal("0")
    for row in rows:
        balance = as_decimal(row.balance)
        total += balance
        accounts.append(
            {
                "account_name": row.name,
                "account_type": row.account_type,
                "current_balance": signed_decimal_to_string(balance),
                "currency": currency,
                "last_transaction_date": (
                    row.last_date.isoformat() if row.last_date else None
                ),
                "is_active": bool(row.is_active),
            }
        )

    return {
        "accounts": accounts,
        "total_balance": signed_decimal_to_string(total),
        "currency": currency,
        "matched_count": len(accounts),
    }


async def list_accounts(
    *,
    include_inactive: bool = False,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        stmt = (
            select(Account.name, Account.account_type, Account.is_active)
            .where(Account.user_id == user_id)
            .order_by(Account.name.asc())
        )
        if not include_inactive:
            stmt = stmt.where(Account.is_active.is_(True))
        rows = list((await db.execute(stmt)).all())

        active_count_stmt = select(func.count()).select_from(Account).where(
            Account.user_id == user_id, Account.is_active.is_(True)
        )
        total_count_stmt = select(func.count()).select_from(Account).where(
            Account.user_id == user_id
        )
        active_count = int((await db.execute(active_count_stmt)).scalar_one() or 0)
        total_count = int((await db.execute(total_count_stmt)).scalar_one() or 0)

    accounts = [
        {
            "account_name": row.name,
            "account_type": row.account_type,
            "is_active": bool(row.is_active),
        }
        for row in rows
    ]

    return {
        "accounts": accounts,
        "total_count": total_count,
        "active_count": active_count,
    }


def register_account_tools() -> None:
    if not is_tool_registered("get_account_balance"):
        query_tool(
            name="get_account_balance",
            description=GET_ACCOUNT_BALANCE_DESCRIPTION,
        )(get_account_balance)
    if not is_tool_registered("list_accounts"):
        query_tool(
            name="list_accounts",
            description=LIST_ACCOUNTS_DESCRIPTION,
        )(list_accounts)


register_account_tools()

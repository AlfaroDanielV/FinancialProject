from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select

from api.models.account import Account
from api.models.transaction import Transaction
from api.services.accounts import compute_account_balances

from app.queries.session import AsyncSessionLocal

from ._common import (
    fuzzy_any,
    signed_decimal_to_string,
    user_currency,
)
from .base import is_tool_registered, query_tool


GET_ACCOUNT_BALANCE_DESCRIPTION = (
    "Devuelve el saldo actual de una o todas las cuentas del usuario. "
    "Usá esto cuando el usuario pregunte cuánto tiene, cuánto le queda, "
    "o el saldo de una cuenta específica. El balance es el saldo inicial de "
    "la cuenta más todas las transacciones confirmadas (gastos restan, "
    "ingresos suman). Cuentas de tipo crédito pueden tener balance negativo "
    "(deuda pendiente)."
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

        accts = list(
            (
                await db.execute(
                    select(
                        Account.id,
                        Account.name,
                        Account.account_type,
                        Account.is_active,
                        Account.currency,
                    )
                    .where(*filters)
                    .order_by(Account.name.asc())
                )
            ).all()
        )
        ids = [a.id for a in accts]
        # Single balance invariant: includes the account's initial_balance and
        # excludes shadow/archived rows. The previous bespoke Σ-amount query did
        # NEITHER, so the chat balance disagreed with the home screen (H2).
        balances = (
            await compute_account_balances(db, user_id=user_id, account_ids=ids)
            if ids
            else {}
        )
        last_dates: dict[uuid.UUID, Any] = {}
        if ids:
            last_rows = await db.execute(
                select(
                    Transaction.account_id,
                    func.max(Transaction.transaction_date),
                )
                .where(
                    Transaction.user_id == user_id,
                    Transaction.account_id.in_(ids),
                    Transaction.status == "confirmed",
                    Transaction.archived.is_(False),
                )
                .group_by(Transaction.account_id)
            )
            last_dates = {aid: d for aid, d in last_rows.all()}

    accounts: list[dict[str, Any]] = []
    totals_by_currency: dict[str, Decimal] = {}
    for a in accts:
        balance = balances[a.id].current if a.id in balances else Decimal("0")
        totals_by_currency[a.currency] = (
            totals_by_currency.get(a.currency, Decimal("0")) + balance
        )
        last = last_dates.get(a.id)
        accounts.append(
            {
                "account_name": a.name,
                "account_type": a.account_type,
                "current_balance": signed_decimal_to_string(balance),
                "currency": a.currency,
                "last_transaction_date": last.isoformat() if last else None,
                "is_active": bool(a.is_active),
            }
        )

    # D3: never add ₡+$ on a placeholder rate. `total_balance` is the user's
    # display-currency subtotal; `totals_by_currency` exposes the rest so the
    # LLM can narrate "tenés ₡X y $Y" without a fabricated conversion.
    primary_total = totals_by_currency.get(currency, Decimal("0"))
    return {
        "accounts": accounts,
        "total_balance": signed_decimal_to_string(primary_total),
        "currency": currency,
        "matched_count": len(accounts),
        "totals_by_currency": {
            c: signed_decimal_to_string(v)
            for c, v in totals_by_currency.items()
        },
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

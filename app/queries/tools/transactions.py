from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import Field
from sqlalchemy import Date, cast, func, literal, or_, select

from api.models.account import Account
from api.models.transaction import Transaction
from api.models.user import User

from app.queries.session import AsyncSessionLocal

from .base import is_tool_registered, query_tool

TransactionType = Literal["expense", "income", "all"]
TransactionSort = Literal["date_desc", "date_asc", "amount_desc", "amount_asc"]
AggregateGroupBy = Literal["category", "account", "merchant", "day", "week", "month"]
AggregateSort = Literal["amount_desc", "amount_asc", "label_asc"]

TransactionLimit = Annotated[int, Field(ge=1, le=200)]
AggregateTopN = Annotated[int, Field(ge=1, le=50)]

LIST_TRANSACTIONS_DESCRIPTION = (
    "Lista transacciones individuales con todos sus detalles. Usala cuando el "
    "usuario pide ver sus gastos/ingresos itemizados, buscar una transaccion "
    "especifica, o ver detalle de un periodo. Las transacciones tienen "
    "granularidad diaria: transaction_date es una fecha sin hora."
)

AGGREGATE_TRANSACTIONS_DESCRIPTION = (
    "Devuelve subtotales agrupados. Usala cuando el usuario pide un desglose, "
    "un resumen por categoria/cuenta/dia/etc., o una comparacion dentro de un "
    "mismo periodo. Las transacciones tienen granularidad diaria."
)


async def list_transactions(
    *,
    start_date: date,
    end_date: date,
    account_ids: list[uuid.UUID] | None = None,
    categories: list[str] | None = None,
    merchants: list[str] | None = None,
    transaction_type: TransactionType = "all",
    min_amount: Decimal | None = None,
    max_amount: Decimal | None = None,
    limit: TransactionLimit = 50,
    sort: TransactionSort = "date_desc",
    user_id: uuid.UUID,
) -> dict[str, Any]:
    filters = _transaction_filters(
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        account_ids=account_ids,
        categories=categories,
        merchants=merchants,
        transaction_type=transaction_type,
        min_amount=min_amount,
        max_amount=max_amount,
    )
    limit_applied = min(limit, 200)

    async with AsyncSessionLocal() as db:
        currency = await _user_currency(db, user_id)
        amount_expr = _abs_amount_expr()

        total_matched_result = await db.execute(
            select(func.count()).select_from(Transaction).where(*filters)
        )
        total_matched = int(total_matched_result.scalar_one() or 0)

        total_amount_result = await db.execute(
            select(func.coalesce(func.sum(amount_expr), Decimal("0")))
            .select_from(Transaction)
            .where(*filters)
        )
        total_amount = _as_decimal(total_amount_result.scalar_one())

        rows_result = await db.execute(
            select(Transaction, Account.name.label("account_name"))
            .outerjoin(Account, Transaction.account_id == Account.id)
            .where(*filters)
            .order_by(*_transaction_order_by(sort))
            .limit(limit_applied)
        )

        transactions = [
            {
                "transaction_date": txn.transaction_date.isoformat(),
                "amount": _decimal_to_string(_as_decimal(txn.amount).copy_abs()),
                "currency": currency,
                "merchant": txn.merchant,
                "category": txn.category,
                "account_name": account_name,
                "notes": txn.description,
                "transaction_type": _transaction_type_for_amount(txn.amount),
            }
            for txn, account_name in rows_result.all()
        ]

    return {
        "transactions": transactions,
        "total_matched": total_matched,
        "total_amount": _decimal_to_string(total_amount),
        "currency": currency,
        "limit_applied": limit_applied,
        "truncated": total_matched > limit_applied,
    }


async def aggregate_transactions(
    *,
    start_date: date,
    end_date: date,
    group_by: AggregateGroupBy,
    account_ids: list[uuid.UUID] | None = None,
    categories: list[str] | None = None,
    merchants: list[str] | None = None,
    transaction_type: TransactionType = "all",
    top_n: AggregateTopN = 10,
    sort: AggregateSort = "amount_desc",
    user_id: uuid.UUID,
) -> dict[str, Any]:
    filters = _transaction_filters(
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        account_ids=account_ids,
        categories=categories,
        merchants=merchants,
        transaction_type=transaction_type,
        min_amount=None,
        max_amount=None,
    )
    top_n_applied = min(top_n, 50)

    async with AsyncSessionLocal() as db:
        currency = await _user_currency(db, user_id)
        agg = await _compute_period_aggregate(
            db,
            filters=filters,
            group_by=group_by,
            top_n=top_n_applied,
            sort=sort,
        )

    groups = [
        {
            "label": _label_to_string(label),
            "amount": _decimal_to_string(amount),
            "count": count,
            "percentage_of_total": _percentage(amount, agg.grand_total),
        }
        for label, amount, count in agg.visible_groups
    ]

    return {
        "groups": groups,
        "grand_total": _decimal_to_string(agg.grand_total),
        "currency": currency,
        "total_groups": agg.total_groups,
        "other_amount": _decimal_to_string(agg.other_amount),
        "other_count": agg.other_count,
        "group_by": group_by,
        "transaction_type_filter": transaction_type,
    }


@dataclass
class _PeriodAggregate:
    """Raw aggregate of a period: numbers in Decimal/int, no serialization.

    Reused by `aggregate_transactions` (which serializes one period) and by
    `compare_periods` (which serializes two and computes deltas).
    """

    grand_total: Decimal
    transaction_count: int
    total_groups: int
    visible_groups: list[tuple[Any, Decimal, int]] = field(default_factory=list)
    other_amount: Decimal = Decimal("0")
    other_count: int = 0


async def _compute_period_aggregate(
    db: Any,
    *,
    filters: list[Any],
    group_by: AggregateGroupBy | None,
    top_n: int,
    sort: AggregateSort = "amount_desc",
) -> _PeriodAggregate:
    """Run one or two queries against `filters` and return a dataclass.

    Always computes `grand_total` and `transaction_count`. When `group_by`
    is set, also computes the breakdown + top-N split. The Account outer
    join is included unconditionally because some `group_by` values
    reference Account.name — including it for the totals query is harmless
    (it's just a join, not a filter), and keeps the SQL plan symmetric
    between the two queries.
    """
    totals_stmt = (
        select(
            func.coalesce(func.sum(_abs_amount_expr()), Decimal("0")),
            func.count(Transaction.id),
        )
        .select_from(Transaction)
        .outerjoin(Account, Transaction.account_id == Account.id)
        .where(*filters)
    )
    totals_row = (await db.execute(totals_stmt)).one()
    grand_total = _as_decimal(totals_row[0])
    txn_count = int(totals_row[1] or 0)

    if group_by is None:
        return _PeriodAggregate(
            grand_total=grand_total,
            transaction_count=txn_count,
            total_groups=0,
        )

    label_expr = _group_label_expr(group_by)
    amount_sql = func.sum(_abs_amount_expr()).label("amount")
    count_sql = func.count(Transaction.id).label("count")
    stmt = (
        select(label_expr.label("label"), amount_sql, count_sql)
        .select_from(Transaction)
        .outerjoin(Account, Transaction.account_id == Account.id)
        .where(*filters)
        .group_by(label_expr)
        .order_by(*_aggregate_order_by(sort, label_expr, amount_sql))
    )
    rows = list((await db.execute(stmt)).all())

    visible = [
        (row.label, _as_decimal(row.amount), int(row.count or 0))
        for row in rows[:top_n]
    ]
    other_rows = rows[top_n:]
    other_amount = sum(
        (_as_decimal(row.amount) for row in other_rows), Decimal("0")
    )
    other_count = sum((int(row.count or 0) for row in other_rows), 0)

    return _PeriodAggregate(
        grand_total=grand_total,
        transaction_count=txn_count,
        total_groups=len(rows),
        visible_groups=visible,
        other_amount=other_amount,
        other_count=other_count,
    )


def register_transaction_tools() -> None:
    if not is_tool_registered("list_transactions"):
        query_tool(
            name="list_transactions",
            description=LIST_TRANSACTIONS_DESCRIPTION,
        )(list_transactions)
    if not is_tool_registered("aggregate_transactions"):
        query_tool(
            name="aggregate_transactions",
            description=AGGREGATE_TRANSACTIONS_DESCRIPTION,
        )(aggregate_transactions)


def _transaction_filters(
    *,
    user_id: uuid.UUID,
    start_date: date,
    end_date: date,
    account_ids: list[uuid.UUID] | None,
    categories: list[str] | None,
    merchants: list[str] | None,
    transaction_type: TransactionType,
    min_amount: Decimal | None,
    max_amount: Decimal | None,
) -> list[Any]:
    filters: list[Any] = [
        Transaction.user_id == user_id,
        Transaction.transaction_date >= start_date,
        Transaction.transaction_date <= end_date,
    ]
    if account_ids:
        filters.append(Transaction.account_id.in_(account_ids))
    if categories:
        filters.append(_fuzzy_any(Transaction.category, categories))
    if merchants:
        filters.append(_fuzzy_any(Transaction.merchant, merchants))
    if transaction_type == "expense":
        filters.append(Transaction.amount < 0)
    elif transaction_type == "income":
        filters.append(Transaction.amount > 0)
    if min_amount is not None:
        filters.append(_abs_amount_expr() >= min_amount)
    if max_amount is not None:
        filters.append(_abs_amount_expr() <= max_amount)
    return filters


def _fuzzy_any(column: Any, values: list[str]) -> Any:
    clauses = []
    for raw in values:
        value = raw.strip().lower()
        if not value:
            continue
        compact = "".join(value.split())
        lowered = func.lower(func.coalesce(column, ""))
        compacted_column = func.lower(
            func.regexp_replace(func.coalesce(column, ""), r"\s+", "", "g")
        )
        clauses.append(
            or_(
                lowered.ilike(f"%{value}%"),
                compacted_column.ilike(f"%{compact}%"),
            )
        )
    return or_(*clauses) if clauses else literal(True)


def _abs_amount_expr() -> Any:
    return func.abs(Transaction.amount)


def _transaction_order_by(sort: TransactionSort) -> list[Any]:
    amount_expr = _abs_amount_expr()
    if sort == "date_asc":
        return [Transaction.transaction_date.asc(), Transaction.created_at.asc()]
    if sort == "amount_desc":
        return [
            amount_expr.desc(),
            Transaction.transaction_date.desc(),
            Transaction.created_at.desc(),
        ]
    if sort == "amount_asc":
        return [
            amount_expr.asc(),
            Transaction.transaction_date.desc(),
            Transaction.created_at.desc(),
        ]
    return [Transaction.transaction_date.desc(), Transaction.created_at.desc()]


def _group_label_expr(group_by: AggregateGroupBy) -> Any:
    if group_by == "category":
        return func.coalesce(Transaction.category, "Sin categoria")
    if group_by == "account":
        return func.coalesce(Account.name, "Sin cuenta")
    if group_by == "merchant":
        return func.coalesce(Transaction.merchant, "Sin comercio")
    if group_by == "day":
        return Transaction.transaction_date
    if group_by == "week":
        return cast(func.date_trunc("week", Transaction.transaction_date), Date)
    if group_by == "month":
        return cast(func.date_trunc("month", Transaction.transaction_date), Date)
    raise ValueError(f"unsupported group_by: {group_by}")


def _aggregate_order_by(sort: AggregateSort, label_expr: Any, amount_expr: Any) -> list[Any]:
    if sort == "amount_asc":
        return [amount_expr.asc(), label_expr.asc()]
    if sort == "label_asc":
        return [label_expr.asc()]
    return [amount_expr.desc(), label_expr.asc()]


async def _user_currency(db: Any, user_id: uuid.UUID) -> str:
    result = await db.execute(select(User.currency).where(User.id == user_id))
    return result.scalar_one_or_none() or "CRC"


def _transaction_type_for_amount(amount: Any) -> str:
    return "expense" if _as_decimal(amount) < 0 else "income"


def _as_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _decimal_to_string(value: Decimal) -> str:
    value = value.copy_abs()
    if value == value.to_integral_value():
        return str(value.quantize(Decimal("1")))
    return format(value.normalize(), "f")


def _percentage(amount: Decimal, total: Decimal) -> float:
    if total == 0:
        return 0.0
    return float(((amount / total) * Decimal("100")).quantize(Decimal("0.1")))


def _label_to_string(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


register_transaction_tools()

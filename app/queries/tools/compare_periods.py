from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal, Optional

from pydantic import Field

from app.queries.session import AsyncSessionLocal

from .base import is_tool_registered, query_tool
from .transactions import (
    AggregateTopN,
    TransactionType,
    _compute_period_aggregate,
    _decimal_to_string,
    _label_to_string,
    _transaction_filters,
    _user_currency,
)


CompareGroupBy = Literal["category", "account", "merchant"]
CompareTopN = Annotated[int, Field(ge=1, le=50)]


COMPARE_PERIODS_DESCRIPTION = (
    "Compará dos períodos de tiempo en términos de gastos o ingresos del "
    "usuario. Útil cuando el usuario pregunta 'compará X con Y', 'cómo voy "
    "este mes vs el anterior', 'gasté más o menos que antes'. "
    "Convención del delta: delta = period_b - period_a, así que poné el "
    "período más antiguo o de referencia en period_a y el más reciente o "
    "actual en period_b. Por ejemplo, 'este mes vs el anterior' → "
    "period_a=mes anterior, period_b=mes actual. delta_amount positivo "
    "significa que period_b fue mayor que period_a. "
    "Si especificás group_by, devuelve un breakdown por grupo en cada "
    "período independiente (no calcula delta por grupo — el modelo razona "
    "sobre las dos listas). transaction_type=expense por default."
)


class InvalidPeriod(Exception):
    """Raised when a period's start_date is after its end_date."""


async def compare_periods(
    *,
    period_a_start: date,
    period_a_end: date,
    period_b_start: date,
    period_b_end: date,
    transaction_type: TransactionType = "expense",
    group_by: Optional[CompareGroupBy] = None,
    account_ids: list[uuid.UUID] | None = None,
    categories: list[str] | None = None,
    merchants: list[str] | None = None,
    top_n: CompareTopN = 10,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    if period_a_start > period_a_end:
        raise InvalidPeriod(
            f"period_a_start ({period_a_start.isoformat()}) es posterior a "
            f"period_a_end ({period_a_end.isoformat()}). Revisá las fechas."
        )
    if period_b_start > period_b_end:
        raise InvalidPeriod(
            f"period_b_start ({period_b_start.isoformat()}) es posterior a "
            f"period_b_end ({period_b_end.isoformat()}). Revisá las fechas."
        )

    top_n_applied = min(top_n, 50)

    def _filters_for(start: date, end: date) -> list[Any]:
        return _transaction_filters(
            user_id=user_id,
            start_date=start,
            end_date=end,
            account_ids=account_ids,
            categories=categories,
            merchants=merchants,
            transaction_type=transaction_type,
            min_amount=None,
            max_amount=None,
        )

    async with AsyncSessionLocal() as db:
        currency = await _user_currency(db, user_id)
        agg_a = await _compute_period_aggregate(
            db,
            filters=_filters_for(period_a_start, period_a_end),
            group_by=group_by,
            top_n=top_n_applied,
        )
        agg_b = await _compute_period_aggregate(
            db,
            filters=_filters_for(period_b_start, period_b_end),
            group_by=group_by,
            top_n=top_n_applied,
        )

    delta_amount = agg_b.grand_total - agg_a.grand_total
    delta_percentage = _delta_percentage(agg_a.grand_total, delta_amount)

    period_a_payload = _period_payload(
        period_a_start, period_a_end, agg_a, group_by
    )
    period_b_payload = _period_payload(
        period_b_start, period_b_end, agg_b, group_by
    )

    payload: dict[str, Any] = {
        "period_a": period_a_payload,
        "period_b": period_b_payload,
        "delta_amount": _signed_decimal(delta_amount),
        "delta_percentage": delta_percentage,
        "transaction_type_filter": transaction_type,
        "currency": currency,
    }
    if group_by is not None:
        payload["group_by"] = group_by
    return payload


def _period_payload(
    start: date,
    end: date,
    agg: Any,
    group_by: Optional[CompareGroupBy],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total_amount": _decimal_to_string(agg.grand_total),
        "transaction_count": agg.transaction_count,
    }
    if group_by is not None:
        body["groups"] = [
            {
                "label": _label_to_string(label),
                "amount": _decimal_to_string(amount),
                "count": count,
            }
            for label, amount, count in agg.visible_groups
        ]
        body["other_amount"] = _decimal_to_string(agg.other_amount)
        body["other_count"] = agg.other_count
    return body


def _delta_percentage(period_a_total: Decimal, delta: Decimal) -> float | None:
    if period_a_total == 0:
        return None
    pct = (delta / period_a_total) * Decimal("100")
    return float(pct.quantize(Decimal("0.1")))


def _signed_decimal(value: Decimal) -> str:
    """Like _decimal_to_string but preserves sign (delta can be negative)."""
    if value == value.to_integral_value():
        return str(value.quantize(Decimal("1")))
    return format(value.normalize(), "f")


def register_compare_periods_tool() -> None:
    if not is_tool_registered("compare_periods"):
        query_tool(
            name="compare_periods",
            description=COMPARE_PERIODS_DESCRIPTION,
        )(compare_periods)


register_compare_periods_tool()

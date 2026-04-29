from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, literal, or_, select

from api.models.user import User


def as_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def decimal_to_string(value: Decimal | int | float | str | None) -> str:
    """Format a non-negative decimal. Used for amounts that are always abs."""
    d = as_decimal(value).copy_abs()
    if d == d.to_integral_value():
        return str(d.quantize(Decimal("1")))
    return format(d.normalize(), "f")


def signed_decimal_to_string(value: Decimal | int | float | str | None) -> str:
    """Format with sign preserved. Used for balances that can be negative."""
    d = as_decimal(value)
    if d == d.to_integral_value():
        return str(d.quantize(Decimal("1")))
    return format(d.normalize(), "f")


def fuzzy_any(column: Any, values: list[str]) -> Any:
    """ILIKE-substring + whitespace-stripped variant. Same helper as bloque 4."""
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


async def user_currency(db: Any, user_id: uuid.UUID) -> str:
    result = await db.execute(select(User.currency).where(User.id == user_id))
    return result.scalar_one_or_none() or "CRC"

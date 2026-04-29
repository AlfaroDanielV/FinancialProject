from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from api.database import AsyncSessionLocal
from api.models.pending_confirmation import PendingConfirmation

from .base import is_tool_registered, query_tool


GET_PENDING_CONFIRMATIONS_DESCRIPTION = (
    "Lista las propuestas de acción del bot que el usuario aún no respondió "
    "(sí/no). Útil cuando el usuario pregunta '¿qué tenía pendiente?' o "
    "necesita recordar contexto previo. Solo devuelve propuestas no resueltas."
)


async def get_pending_confirmations(
    *,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        stmt = (
            select(PendingConfirmation)
            .where(
                PendingConfirmation.user_id == user_id,
                PendingConfirmation.resolved_at.is_(None),
            )
            .order_by(PendingConfirmation.created_at.desc())
        )
        rows = list((await db.execute(stmt)).scalars())

    now = datetime.now(timezone.utc)
    cr_tz = ZoneInfo("America/Costa_Rica")

    pending = []
    for row in rows:
        proposed = row.proposed_action or {}
        summary = (
            proposed.get("summary_es")
            if isinstance(proposed, dict)
            else None
        )
        if not summary:
            summary = _format_fallback(row.action_type, proposed)
        created_at = _ensure_aware(row.created_at)
        delta = now - created_at
        age_hours = round(delta.total_seconds() / 3600, 1)
        pending.append(
            {
                "proposed_action": summary,
                "created_at": created_at.astimezone(cr_tz).isoformat(),
                "age_hours": age_hours,
            }
        )

    return {
        "pending": pending,
        "total_count": len(pending),
    }


def _format_fallback(action_type: str, proposed: Any) -> str:
    if not isinstance(proposed, dict):
        return f"Acción {action_type} pendiente"
    payload = proposed.get("payload") if isinstance(proposed.get("payload"), dict) else {}
    amount = payload.get("amount")
    merchant = payload.get("merchant")
    category = payload.get("category")
    pieces = []
    if action_type == "log_expense":
        pieces.append("Registrar gasto")
    elif action_type == "log_income":
        pieces.append("Registrar ingreso")
    else:
        pieces.append(f"Acción {action_type}")
    if amount:
        pieces.append(f"de {amount}")
    if merchant:
        pieces.append(f"en {merchant}")
    elif category:
        pieces.append(f"({category})")
    return " ".join(pieces)


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def register_pending_tools() -> None:
    if not is_tool_registered("get_pending_confirmations"):
        query_tool(
            name="get_pending_confirmations",
            description=GET_PENDING_CONFIRMATIONS_DESCRIPTION,
        )(get_pending_confirmations)


register_pending_tools()

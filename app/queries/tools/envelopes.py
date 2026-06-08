"""Read-only query tool — envelope ("sobre") spending.

Answers "¿cuánto gasté en <sobre>?" / "¿cuánto me queda en mis sobres?" by
reusing the same live computation the home tab uses
(`api/services/envelopes.py::compute_envelope_summary`) — so the assistant's
answer can never drift from the bars. The LLM phrases the result; this tool
only returns the numbers.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from api.models.user import User
from api.services.envelopes import compute_envelope_summary

from app.queries.session import AsyncSessionLocal

from .base import is_tool_registered, query_tool


GET_ENVELOPE_SPENDING_DESCRIPTION = (
    "Devuelve cuánto lleva gastado el usuario este mes en sus sobres "
    "(envelopes / presupuestos por rubro) y cuánto le queda del tope. Usá esto "
    "cuando el usuario pregunte por el gasto o el saldo de un sobre por nombre "
    "(ej.: «¿cuánto gasté en Restaurantes?», «¿cómo voy con el sobre del "
    "súper?», «¿cuánto me queda en mis sobres?»). Pasá envelope_name para un "
    "sobre específico (coincide por nombre, sin distinguir mayúsculas), o "
    "dejalo vacío para todos. El gasto se calcula en vivo desde las "
    "transacciones del mes en curso; lo gastado en otra moneda se convierte a "
    "la moneda del sobre. Los sobres pueden tener sub-sobres: «spent» es el "
    "gasto ACUMULADO (este sobre más sus sub-sobres) y «children» trae el "
    "desglose por sub-sobre; «direct_spent» es solo lo cargado directamente al "
    "sobre. Si no hay coincidencia, mirá available_envelope_names para sugerir "
    "los sobres que sí existen."
)


def _match(items, name: str):
    """Case-insensitive name match: exact first, else substring either way."""
    q = name.strip().lower()
    exact = [it for it in items if it.name.lower() == q]
    if exact:
        return exact
    return [it for it in items if q in it.name.lower() or it.name.lower() in q]


async def get_envelope_spending(
    *,
    envelope_name: Optional[str] = None,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            return {"envelopes": [], "matched_count": 0, "available_envelope_names": []}
        summary = await compute_envelope_summary(db, user=user)

    items = list(summary.envelopes)
    by_parent: dict[Any, list] = {}
    for it in items:
        by_parent.setdefault(it.parent_id, []).append(it)

    def _serialize(it) -> dict[str, Any]:
        node = {
            "name": it.name,
            "class": it.envelope_class,
            "currency": it.currency,
            "limit": f"{it.limit_amount:.2f}",
            "spent": f"{it.spent:.2f}",  # rolled-up (this sobre + sub-sobres)
            "direct_spent": f"{it.direct_spent:.2f}",
            "remaining": f"{it.remaining:.2f}",
            "over_limit": it.over_limit,
        }
        kids = by_parent.get(it.id, [])
        if kids:
            node["children"] = [_serialize(k) for k in kids]
        return node

    if envelope_name and envelope_name.strip():
        matched = _match(items, envelope_name)
    else:
        # No name → list the roots; sub-sobres nest under them via "children".
        matched = [it for it in items if it.parent_id is None]

    return {
        "period": summary.period,
        "currency": summary.currency,
        "query_name": envelope_name,
        "envelopes": [_serialize(it) for it in matched],
        "matched_count": len(matched),
        # So the LLM can say "no tenés un sobre llamado X; tenés: …" (incl. sub-sobres).
        "available_envelope_names": [it.name for it in items],
    }


def register_envelope_tools() -> None:
    if not is_tool_registered("get_envelope_spending"):
        query_tool(
            name="get_envelope_spending",
            description=GET_ENVELOPE_SPENDING_DESCRIPTION,
        )(get_envelope_spending)


register_envelope_tools()

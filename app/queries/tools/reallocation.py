"""Read-only query tool — reallocation candidates ("¿de dónde muevo plata?").

Answers the reactive-advisor ask ("me estoy pasando de Gustos, ¿de dónde
saco?") by ranking the user's OWN envelopes by unused budget (`available`
DESC) — the most slack is the best place to pull budget FROM. Reuses the same
live computation the home tab uses
(`api/services/envelopes.py::compute_envelope_summary`), so the suggestion can
never drift from the bars. The LLM proposes the specific move; this tool only
returns the ranked numbers. The actual write (a same-level reallocation the
user confirms) goes through the deterministic write path, never here.
"""
from __future__ import annotations

import uuid
from typing import Any

from api.models.user import User
from api.services.envelopes import compute_envelope_summary

from app.queries.session import AsyncSessionLocal

from .base import is_tool_registered, query_tool


SUGGEST_REALLOCATION_DESCRIPTION = (
    "Sugiere de cuáles sobres conviene mover (reasignar) presupuesto. Usá esto "
    "cuando el usuario pregunte «¿de dónde muevo plata?», «me estoy pasando del "
    "sobre X, ¿de dónde saco?» o pida ideas para cubrir un sobre que se quedó "
    "corto. Devuelve los sobres propios del usuario ordenados por presupuesto "
    "SIN USAR (available, de mayor a menor): los primeros son los mejores "
    "candidatos para sacar plata sin afectar lo ya gastado. Cada sobre trae "
    "name, available (lo libre = tope − reservado − gastado), limit (tope), "
    "spent (gastado) y currency, todo como texto. Con esto proponé un "
    "movimiento concreto (cuánto y de cuál sobre a cuál) para que el usuario "
    "lo confirme. OJO: el movimiento solo es válido entre sobres del mismo "
    "nivel (dos sobres principales, o dos sub-sobres del mismo padre) y en la "
    "misma moneda."
)


async def suggest_reallocation_candidates(
    *,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            return {"candidates": [], "count": 0}
        summary = await compute_envelope_summary(db, user=user)

    # Own (non-shared) envelopes only — shared items the user merely JOINED are
    # display-only and can't be reallocated. Archived rows are already excluded
    # by compute_envelope_summary. Rank by unused budget DESC (most slack first).
    own = [e for e in summary.envelopes if not e.is_shared]
    own.sort(key=lambda e: e.available, reverse=True)
    return {
        "currency": summary.currency,
        "candidates": [
            {
                "name": e.name,
                "available": f"{e.available:.2f}",
                "limit": f"{e.limit_amount:.2f}",
                "spent": f"{e.spent:.2f}",
                "currency": e.currency,
            }
            for e in own
        ],
        "count": len(own),
    }


def register_reallocation_tools() -> None:
    if not is_tool_registered("suggest_reallocation_candidates"):
        query_tool(
            name="suggest_reallocation_candidates",
            description=SUGGEST_REALLOCATION_DESCRIPTION,
        )(suggest_reallocation_candidates)


register_reallocation_tools()

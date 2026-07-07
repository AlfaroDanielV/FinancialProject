"""P10 B9 (+B7) — `get_framing_principles`: matched narration frames.

The dynamic half of the principle-framing contract (§4.4): the STATIC rule
lives in the system prompt (`_PRINCIPLE_FRAMING`); this tool returns the
per-user MATCHED principles — framing intent, source attribution, CR
localization color. It returns **narration shape only — NEVER a number** (the
CI no-number scorer sweeps everything this tool can emit).

Determinism spine (D8/D9):
- `financial_state` is derived SERVER-SIDE by the single owner
  (`classify_for_user`) — the LLM cannot supply or override it.
- The P6c archetype / risk-posture insights are OPTIONAL ranking modifiers
  (B7, resolution O1): read when present, mapped conservatively, and absent →
  the same principles fire with centrality-only ranking. They never select.
- The LLM may pass only the conversational `intent` topic (e.g.
  "plan_retirement") — context it legitimately owns.

Advisory-only tool; advice-traced as kind="advisory_assessment" (D11) with
the state + matched ids, swallow-on-fail.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select

from api.data.principle_library_cr import match_principles
from api.models.user import User
from api.services.advice_trace import record_advice_event
from api.services.finance.financial_state import classify_for_user

from ..session import AsyncSessionLocal
from .base import is_tool_registered, query_tool

log = logging.getLogger("app.queries.tools.framing")

GET_FRAMING_PRINCIPLES_DESCRIPTION = (
    "Devuelve hasta dos principios de finanzas conductuales (curados, con "
    "fuente) que calzan con el estado financiero REAL del usuario — el estado "
    "lo calcula el servidor, no vos. Usá framing_template como intención de "
    "tono y encuadre para narrar los hallazgos deterministas; citá la fuente "
    "con naturalidad si aporta. Los principios NUNCA traen ni justifican "
    "cifras — los números salen solo de las herramientas de datos. Podés "
    "pasar intent (p. ej. plan_retirement) si el tema de la conversación lo "
    "amerita."
)

# Klontz money-script labels the library understands. ArchetypeContent.primary
# is free text — anything else degrades to None (O1: modifier, never a gate).
_KNOWN_ARCHETYPES = frozenset(
    {"money_avoidance", "money_worship", "money_status", "money_vigilance"}
)

_RISK_MAP = {"conservative": "low_tolerance", "aggressive": "high_tolerance"}

# The deterministic money_personality label (Workstream E) → the Klontz
# money-script the library already ranks against. "investor" has no clean Klontz
# target → not mapped (falls back to the observed LLM archetype, if any). This
# stays a ranking MODIFIER, never a selector (O1 / P10 hard rule 2).
_SASI_TO_KLONTZ = {
    "spender": "money_status",
    "avoider": "money_avoidance",
    "saver": "money_vigilance",
}


async def _optional_signals(db, user_id: uuid.UUID) -> tuple[Optional[str], Optional[str]]:
    """Latest valid archetype + risk-posture insights, mapped conservatively.

    The computed money_personality label is preferred over the LLM archetype:
    both are ordered by confidence desc, and the computed row caps at 1.00 while
    the LLM archetype caps at 0.85, so a mapped money_personality is evaluated
    first. Missing rows / unmappable content → (None, None)-ish: the match falls
    back to centrality + state strength (O1). Never raises — a broken insight row
    must not break the framing path."""
    archetype: Optional[str] = None
    risk_signal: Optional[str] = None
    try:
        from api.models.user_insight import UserInsight

        rows = (
            await db.execute(
                select(UserInsight.insight_type, UserInsight.content)
                .where(
                    UserInsight.user_id == user_id,
                    UserInsight.insight_type.in_(
                        ("archetype", "risk_posture", "money_personality")
                    ),
                    UserInsight.valid_until > datetime.now(timezone.utc),
                )
                .order_by(UserInsight.confidence.desc(), UserInsight.updated_at.desc())
            )
        ).all()
        for insight_type, content in rows:
            if insight_type == "money_personality" and archetype is None:
                mapped = _SASI_TO_KLONTZ.get(
                    str((content or {}).get("personality", "")).strip().lower()
                )
                if mapped is not None:
                    archetype = mapped
            elif insight_type == "archetype" and archetype is None:
                primary = str((content or {}).get("primary", "")).strip().lower()
                primary = primary.replace(" ", "_").replace("-", "_")
                if primary in _KNOWN_ARCHETYPES:
                    archetype = primary
            elif insight_type == "risk_posture" and risk_signal is None:
                risk_signal = _RISK_MAP.get(str((content or {}).get("posture", "")))
    except Exception:  # noqa: BLE001 — optional signal, never blocks framing
        log.exception("framing_optional_signals_failed user_id=%s", user_id)
    return archetype, risk_signal


def _source_line(p: dict) -> str:
    src = p["source"]
    locator = f" ({src['locator']})" if src.get("locator") else ""
    return f"{src['title']} — {src['author']}{locator}"


async def get_framing_principles(
    *,
    intent: Optional[str] = None,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            return {"error": "user_not_found"}
        reading = await classify_for_user(db, user=user)
        archetype, risk_signal = await _optional_signals(db, user_id)

    matched = match_principles(
        reading.state.value,
        gate_reason=reading.signals.get("gate_reason"),
        archetype=archetype,
        risk_signal=risk_signal,
        intent=intent,
    )
    payload: dict[str, Any] = {
        "financial_state": reading.state.value,
        "principles": [
            {
                "principle_id": p["principle_id"],
                "framing_template": p["framing_template"],
                "core_idea": p["core_idea"],
                "source": _source_line(p),
                "cultural_flags": p["cultural_flags"],
            }
            for p in matched
        ],
        "matched_count": len(matched),
        "note": (
            "Marcos de tono y encuadre solamente — los números salen "
            "únicamente de las herramientas de datos. Si un encuadre pide "
            "contexto que el usuario nunca dio, omitilo."
        ),
    }
    await record_advice_event(
        user_id=user_id,
        kind="advisory_assessment",
        verdict=f"state:{reading.state.value}"[:30],
        inputs={
            "financial_state": reading.state.value,
            "signals": reading.signals,
            "intent": intent,
            "archetype": archetype,
            "risk_signal": risk_signal,
        },
        result={"matched_principle_ids": [p["principle_id"] for p in matched]},
        surface="query_tool",
    )
    return payload


def register_framing_tools() -> None:
    if not is_tool_registered("get_framing_principles"):
        query_tool(
            name="get_framing_principles",
            description=GET_FRAMING_PRINCIPLES_DESCRIPTION,
        )(get_framing_principles)


register_framing_tools()

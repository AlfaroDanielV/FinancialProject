"""Prompt and tool schema for Phase 6c user-memory extraction.

The extractor is intentionally narrower than the Phase 5b intent
extractor. It only emits conversation-derived memory types. Computed
financial facts belong to `api.services.insights.computed`, not here.
"""
from __future__ import annotations

EXTRACTABLE_TYPES = (
    "stated_preference",
    "stated_goal",
    "archetype",
    "risk_posture",
    "decision_style",
    "financial_literacy",
)


SYSTEM_PROMPT = """\
Sos un extractor de memoria financiera para un bot de Telegram en español
de Costa Rica. Tu trabajo es leer una ventana corta de conversación y emitir
SOLO insights estructurados que el usuario declaró o que se pueden inferir
del estilo de la conversación.

Reglas duras:
- Siempre llamá la herramienta `emit_user_insights`.
- El output es un objeto con `insights`, un array JSON estricto.
- Podés devolver `insights: []` si no hay señal clara.
- NO emitás datos calculados desde transacciones. Prohibidos:
  spending_pattern, recurring_drift, cash_flow_stability, debt_load,
  emergency_fund, cr_seasonal_pattern, behavioral_flag, stated_observed_gap.
- Tipos permitidos:
  stated_preference, stated_goal, archetype, risk_posture,
  decision_style, financial_literacy.
- No inventés montos, fechas, metas ni preferencias. Si falta evidencia,
  no emitas insight.
- `confidence` es por insight: 0.50 a 0.85. Nunca uses más de 0.85.
- `raw_quote` debe ser una cita breve del usuario, máximo 280 caracteres.
- No uses tono psicológico. Reportá preferencias, metas y estilo observable.
- Español costarricense con voseo en campos narrativos cuando aplique.

Shapes exactos:
- stated_preference.content:
  {topic: debt_payoff|savings|risk_tolerance|lifestyle,
   stance, raw_quote, extracted_at}
- stated_goal.content:
  {goal_text, target_amount, target_date, status: mentioned|committed|abandoned}
- archetype.content:
  {primary, secondary, confidence, evidence_summary}
- risk_posture.content:
  {posture: conservative|moderate|aggressive,
   evidence_basis: stated|observed|mixed}
- decision_style.content:
  {style: analytical|intuitive|avoidant, evidence}
- financial_literacy.content:
  {level: beginner|intermediate|advanced, evidence}

Few-shots:

Usuario: "Quiero matar primero las tarjetas, aunque me quede apretado."
Output:
{"insights":[{"type":"stated_preference","confidence":0.82,"content":{"topic":"debt_payoff","stance":"prioriza pagar tarjetas antes que otros objetivos","raw_quote":"Quiero matar primero las tarjetas","extracted_at":"2026-05-07T00:00:00Z"}}]}

Usuario: "Quiero ahorrar 500 mil para diciembre para el marchamo."
Output:
{"insights":[{"type":"stated_goal","confidence":0.84,"content":{"goal_text":"Ahorrar para el marchamo","target_amount":500000,"target_date":"2026-12-01","status":"committed"}}]}

Usuario: "No me gusta invertir en cosas volátiles. Prefiero algo seguro."
Output:
{"insights":[{"type":"risk_posture","confidence":0.80,"content":{"posture":"conservative","evidence_basis":"stated"}}]}

Usuario: "Mandame números, comparaciones y supuestos; quiero entender el cálculo."
Output:
{"insights":[{"type":"decision_style","confidence":0.78,"content":{"style":"analytical","evidence":"pidió números, comparaciones y supuestos para decidir"}}]}

Usuario: "No entiendo bien qué es tasa efectiva, explicámelo simple."
Output:
{"insights":[{"type":"financial_literacy","confidence":0.70,"content":{"level":"beginner","evidence":"pidió explicación simple de tasa efectiva"}}]}

Usuario: "Si el aguinaldo no alcanza, pago una tarjeta con otra."
Output:
{"insights":[{"type":"archetype","confidence":0.76,"content":{"primary":"debt_juggler","secondary":"aguinaldo_dependent","confidence":0.76,"evidence_summary":"menciona depender del aguinaldo y mover saldos entre tarjetas"}}]}

Usuario: "¿Cuánto gasté ayer en gasolina?"
Output:
{"insights":[]}
"""


TOOL_DEFINITION = {
    "name": "emit_user_insights",
    "description": (
        "Emit strict structured memory insights extracted from a short "
        "Spanish conversation window. Return an empty array when there is "
        "no durable user-memory signal."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["insights"],
        "properties": {
            "insights": {
                "type": "array",
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["type", "confidence", "content"],
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": list(EXTRACTABLE_TYPES),
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 0.85,
                        },
                        "content": {
                            "type": "object",
                            "description": (
                                "Payload matching the schema for `type`. "
                                "Do not include computed insight fields."
                            ),
                        },
                    },
                },
            }
        },
    },
}


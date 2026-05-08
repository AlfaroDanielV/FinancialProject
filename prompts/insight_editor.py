"""Prompt and tool schema for the /editar_memoria parser (Phase 6c B7).

This is NOT the post-conversation extractor (B4). It runs only when the
user has tapped a specific insight to edit and replied with a free-text
correction. Single-insight output, strict to the type the user is
editing — the runner rejects anything else.

Hard rules baked into the prompt:
- Always call `emit_user_edit`.
- Output exactly ONE insight, matching the requested `target_type`.
- Never invent amounts, dates, or topics that the user didn't say.
- Spanish CR voseo in narrative fields.
- Reply confidence must be 1.0 (this is a user override).
"""
from __future__ import annotations


EDITABLE_TYPES = (
    "stated_preference",
    "stated_goal",
    "archetype",
    "risk_posture",
    "decision_style",
    "financial_literacy",
)


SYSTEM_PROMPT = """\
Sos un parser estricto que convierte una corrección del usuario en UN insight
estructurado para la memoria del bot. El usuario ya eligió qué insight quiere
corregir; tu trabajo es leer el `target_type` solicitado y devolver UN solo
insight de ese mismo tipo, con los campos derivados de la respuesta del usuario.

Reglas duras:
- Siempre llamá la herramienta `emit_user_edit`.
- El campo `type` del output DEBE coincidir con `target_type`. Si el mensaje
  del usuario no permite producir ese tipo, igual emití el tipo solicitado
  con los mejores campos posibles a partir del texto. NUNCA cambies el tipo.
- No inventés montos, fechas, ni tópicos. Si el usuario no los dijo,
  dejalos en null o usá el default razonable del schema.
- Español costarricense con voseo en campos narrativos cuando aplique.
- Para `stated_preference`: `raw_quote` debe ser una cita textual breve del
  usuario (≤280 chars). `extracted_at` usá la fecha actual ISO 8601.
- Para `stated_goal`: si el usuario no da fecha, dejá `target_date=null`.
  `status` por defecto es "committed" cuando el usuario dice "quiero"/"voy a";
  "mentioned" cuando solo lo menciona.
- Para `archetype`: `confidence` interno fijalo en 0.85 (no es el confidence
  global; es el campo del modelo).

Pocos shots:

target_type=risk_posture
Usuario: "soy bastante conservador con mi plata"
Output:
{"type":"risk_posture","content":{"posture":"conservative","evidence_basis":"stated"}}

target_type=stated_goal
Usuario: "mi meta es ahorrar 5 millones para diciembre"
Output:
{"type":"stated_goal","content":{"goal_text":"Ahorrar 5 millones","target_amount":5000000,"target_date":"2026-12-01","status":"committed"}}

target_type=stated_preference
Usuario: "siempre prefiero pagar deudas antes que ahorrar"
Output:
{"type":"stated_preference","content":{"topic":"debt_payoff","stance":"prioriza pagar deudas antes que ahorrar","raw_quote":"siempre prefiero pagar deudas antes que ahorrar","extracted_at":"2026-05-08T00:00:00Z"}}

target_type=decision_style
Usuario: "decido por instinto, sin tanto número"
Output:
{"type":"decision_style","content":{"style":"intuitive","evidence":"decide por instinto sin pedir números"}}

target_type=financial_literacy
Usuario: "ya manejo bien interés compuesto y amortización"
Output:
{"type":"financial_literacy","content":{"level":"advanced","evidence":"maneja interés compuesto y amortización"}}

target_type=archetype
Usuario: "soy ahorrador prudente, planeo todo antes"
Output:
{"type":"archetype","content":{"primary":"saver_planner","secondary":null,"confidence":0.85,"evidence_summary":"se describe como ahorrador prudente que planea todo antes"}}
"""


TOOL_DEFINITION = {
    "name": "emit_user_edit",
    "description": (
        "Emit exactly ONE structured user-memory insight derived from a "
        "free-text correction. The `type` must match the requested "
        "target_type; content fields must follow the schema for that type."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "content"],
        "properties": {
            "type": {
                "type": "string",
                "enum": list(EDITABLE_TYPES),
            },
            "content": {
                "type": "object",
                "description": (
                    "Payload matching the schema for `type`. Computed-only "
                    "fields are forbidden."
                ),
            },
        },
    },
}

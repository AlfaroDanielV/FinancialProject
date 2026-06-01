"""Recorded (hand-crafted) LLM responses for extractor fixture tests.

TODO: Re-record against the real Anthropic API once the bot is wired and
overwrite these values with the actual tool_input payloads. The shapes here
are what Haiku 4.5 should plausibly produce for each Spanish input — used
right now to pin the ExtractionResult schema before the dispatcher starts
consuming it.

Each entry is the exact dict that would arrive as `tool_use.input` from the
model. Pydantic validation + our field validators transform this into an
ExtractionResult. If a validator silently drops a field (e.g. unsupported
currency), the test should assert the drop, not the raw value.
"""
from __future__ import annotations

from api.services.llm_extractor import RecordedLLMResponse


# ── 1. Basic CRC expense ──────────────────────────────────────────────────────
# Input: "gasté 5000 colones en el super"
BASIC_EXPENSE_CRC = RecordedLLMResponse(
    tool_input={
        "intent": "log_expense",
        "dispatcher": "write",
        "amount": 5000,
        "currency": "CRC",
        "merchant": "supermercado",
        "category_hint": "supermercado",
        "account_hint": None,
        "occurred_at_hint": None,
        "query_window": None,
        "confidence": 0.92,
        "raw_notes": None,
    },
    input_tokens=430,
    output_tokens=45,
    cache_read_input_tokens=380,
)


# ── 2. Slang amount ("5k"), no currency ───────────────────────────────────────
# Input: "5k en gasolina"
SLANG_AMOUNT_NO_CURRENCY = RecordedLLMResponse(
    tool_input={
        "intent": "log_expense",
        "dispatcher": "write",
        "amount": 5000,
        "currency": None,  # user didn't say; dispatcher will default to user.currency
        "merchant": None,
        "category_hint": "combustible",
        "account_hint": None,
        "occurred_at_hint": None,
        "query_window": None,
        "confidence": 0.78,
        "raw_notes": None,
    },
    input_tokens=425,
    output_tokens=40,
    cache_read_input_tokens=380,
)


# ── 3. USD expense ────────────────────────────────────────────────────────────
# Input: "pagué 30 dólares en Amazon"
USD_EXPENSE = RecordedLLMResponse(
    tool_input={
        "intent": "log_expense",
        "dispatcher": "write",
        "amount": 30,
        "currency": "USD",
        "merchant": "Amazon",
        "category_hint": "compras en línea",
        "account_hint": None,
        "occurred_at_hint": None,
        "query_window": None,
        "confidence": 0.95,
        "raw_notes": None,
    },
    input_tokens=428,
    output_tokens=42,
    cache_read_input_tokens=380,
)


# ── 4. Expense with relative date ─────────────────────────────────────────────
# Input: "ayer compré pan por 2 mil"
EXPENSE_YESTERDAY = RecordedLLMResponse(
    tool_input={
        "intent": "log_expense",
        "dispatcher": "write",
        "amount": 2000,
        "currency": "CRC",
        "merchant": "panadería",
        "category_hint": "comida",
        "account_hint": None,
        "occurred_at_hint": "ayer",
        "query_window": None,
        "confidence": 0.85,
        "raw_notes": None,
    },
    input_tokens=430,
    output_tokens=48,
    cache_read_input_tokens=380,
)


# ── 5. Weekly balance query ───────────────────────────────────────────────────
# Input: "¿cuánto gasté esta semana?"
WEEKLY_BALANCE_QUERY = RecordedLLMResponse(
    tool_input={
        "intent": "query",
        "dispatcher": "query",
        "amount": None,
        "currency": None,
        "merchant": None,
        "category_hint": None,
        "account_hint": None,
        "occurred_at_hint": None,
        "query_window": "this_week",
        "confidence": 0.97,
        "raw_notes": None,
    },
    input_tokens=420,
    output_tokens=35,
    cache_read_input_tokens=380,
)


# ── 7. Conversational goal creation — named + amount (Phase 6f) ───────────────
# Input: "creá una meta de fondo de emergencia de 500 mil"
CREATE_GOAL_NAMED = RecordedLLMResponse(
    tool_input={
        "intent": "create_goal",
        "dispatcher": "write",
        "amount": None,
        "currency": None,
        "merchant": None,
        "category_hint": None,
        "account_hint": None,
        "occurred_at_hint": None,
        "query_window": None,
        "goal_name": "fondo de emergencia",
        "goal_target_amount": 500000,
        "goal_target_date": None,
        "confidence": 0.93,
        "raw_notes": None,
    },
    input_tokens=440,
    output_tokens=50,
    cache_read_input_tokens=380,
)


# ── 8. Goal creation, amount + date but no name (drives clarification) ─────────
# Input: "quiero ahorrar 2 millones para diciembre"
CREATE_GOAL_NO_NAME = RecordedLLMResponse(
    tool_input={
        "intent": "create_goal",
        "dispatcher": "write",
        "amount": None,
        "currency": None,
        "merchant": None,
        "category_hint": None,
        "account_hint": None,
        "occurred_at_hint": None,
        "query_window": None,
        "goal_name": None,
        "goal_target_amount": 2000000,
        "goal_target_date": "diciembre",
        "confidence": 0.9,
        "raw_notes": None,
    },
    input_tokens=438,
    output_tokens=48,
    cache_read_input_tokens=380,
)


# ── 9. Conversational recurring-income creation — salary (Phase 6f) ───────────
# Input: "me pagan 800 mil de salario cada quincena, el próximo el 15"
CREATE_INCOME_SALARY = RecordedLLMResponse(
    tool_input={
        "intent": "create_income",
        "dispatcher": "write",
        "amount": 800000,
        "currency": None,
        "merchant": None,
        "category_hint": None,
        "account_hint": None,
        "occurred_at_hint": None,
        "query_window": None,
        "income_type": "salary",
        "income_frequency": "biweekly",
        "income_next_date": "el 15",
        "confidence": 0.92,
        "raw_notes": None,
    },
    input_tokens=445,
    output_tokens=55,
    cache_read_input_tokens=380,
)


# ── 10. Conversational recurring-bill creation (Phase 6f) ─────────────────────
# Input: "el recibo de luz me llega como 18 mil cada mes, el 5"
CREATE_BILL_MONTHLY = RecordedLLMResponse(
    tool_input={
        "intent": "create_bill",
        "dispatcher": "write",
        "amount": 18000,
        "currency": None,
        "merchant": None,
        "category_hint": "servicios",
        "account_hint": None,
        "occurred_at_hint": None,
        "query_window": None,
        "bill_name": "Luz",
        "bill_frequency": "monthly",
        "bill_day_of_month": 5,
        "confidence": 0.9,
        "raw_notes": None,
    },
    input_tokens=448,
    output_tokens=58,
    cache_read_input_tokens=380,
)


# ── 11. Conversational debt creation (Phase 6f) — chat → form handoff ─────────
# Input: "tengo un préstamo de 5 millones a 5 años con el BAC" (rate unknown)
CREATE_DEBT_BASIC = RecordedLLMResponse(
    tool_input={
        "intent": "create_debt",
        "dispatcher": "write",
        "debt_name": None,
        "debt_principal": 5000000,
        "debt_interest_rate": None,
        "debt_term_months": 60,
        "debt_lender": "BAC",
        "confidence": 0.9,
    },
    input_tokens=452,
    output_tokens=44,
    cache_read_input_tokens=380,
)


# ── 6. Low-confidence ambiguous input (schema-sharpening case) ────────────────
# Input: "algo de 1000 por ahí"
LOW_CONFIDENCE_AMBIGUOUS = RecordedLLMResponse(
    tool_input={
        "intent": "unknown",
        "dispatcher": "control",
        "amount": 1000,
        "currency": None,
        "merchant": None,
        "category_hint": None,
        "account_hint": None,
        "occurred_at_hint": None,
        "query_window": None,
        "confidence": 0.35,
        "raw_notes": "menciona cantidad pero no indica si es gasto, ingreso, o consulta",
    },
    input_tokens=422,
    output_tokens=52,
    cache_read_input_tokens=380,
)

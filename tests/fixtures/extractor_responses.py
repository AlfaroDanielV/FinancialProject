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
        "intent": "query_balance",
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


# ── 6. Low-confidence ambiguous input (schema-sharpening case) ────────────────
# Input: "algo de 1000 por ahí"
LOW_CONFIDENCE_AMBIGUOUS = RecordedLLMResponse(
    tool_input={
        "intent": "unknown",
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

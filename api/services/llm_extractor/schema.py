"""Pydantic schema for what the LLM is allowed to return.

The dispatcher consumes ExtractionResult — not the raw model output. If the
model produces something outside this shape, Pydantic rejects it and the
runner replies in Spanish asking the user to rephrase. This is deliberate:
we'd rather lose one turn than let a malformed extraction cross the
LLM-rules boundary.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Intent(str, Enum):
    LOG_EXPENSE = "log_expense"
    LOG_INCOME = "log_income"
    QUERY_RECENT = "query_recent"
    QUERY_BALANCE = "query_balance"
    CONFIRM_YES = "confirm_yes"
    CONFIRM_NO = "confirm_no"
    UNDO = "undo"
    HELP = "help"
    UNKNOWN = "unknown"


# Accepted values for `query_window`. "last_n_days:<int>" is checked by
# prefix — the tail is validated numerically in the field validator.
VALID_QUERY_WINDOWS = frozenset(
    {"today", "yesterday", "this_week", "this_month"}
)
EXPECTED_QUERY_WINDOW_PREFIX = "last_n_days:"


class ExtractionResult(BaseModel):
    """Canonical structured output of the LLM extractor.

    Every field is optional EXCEPT `intent` and `confidence` so the model is
    free to admit "I don't know" by leaving a value null rather than
    hallucinating one. The dispatcher's clarification logic assumes the
    model respects that — the system prompt reinforces it.
    """

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    amount: Optional[Decimal] = Field(default=None)
    currency: Optional[str] = Field(default=None)
    merchant: Optional[str] = Field(default=None, max_length=255)
    category_hint: Optional[str] = Field(default=None, max_length=100)
    account_hint: Optional[str] = Field(default=None, max_length=100)
    occurred_at_hint: Optional[str] = Field(default=None, max_length=100)
    query_window: Optional[str] = Field(default=None, max_length=32)
    confidence: float = Field(..., ge=0.0, le=1.0)
    raw_notes: Optional[str] = Field(default=None, max_length=500)

    @field_validator("currency")
    @classmethod
    def _currency_upper(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().upper()
        if v not in {"CRC", "USD"}:
            # Phase 5b is single-currency per txn and the project is
            # CRC/USD only. Reject silently → dispatcher treats as missing.
            return None
        return v

    @field_validator("query_window")
    @classmethod
    def _validate_window(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().lower()
        if v in VALID_QUERY_WINDOWS:
            return v
        if v.startswith(EXPECTED_QUERY_WINDOW_PREFIX):
            tail = v[len(EXPECTED_QUERY_WINDOW_PREFIX):]
            try:
                n = int(tail)
            except ValueError:
                return None
            if n < 1 or n > 365:
                return None
            return f"{EXPECTED_QUERY_WINDOW_PREFIX}{n}"
        return None

    @field_validator("category_hint", "account_hint", "merchant")
    @classmethod
    def _normalize_strings(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = " ".join(v.split())  # collapse inner whitespace
        return v or None

    @field_validator("amount")
    @classmethod
    def _positive_amount(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        # The sign in the DB is decided by the dispatcher from `intent`; the
        # model always returns a magnitude. Anything non-positive is
        # dropped so the dispatcher asks for clarification instead of
        # committing a zero-amount transaction.
        if v is None:
            return None
        if v <= 0:
            return None
        return v

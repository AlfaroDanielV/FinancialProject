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
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# SMART goals: sentinel for `goal_target_date` meaning "the user explicitly
# declined a deadline". Distinct from None (which means "ask") — the dispatcher
# checks it before re-asking so "Sin fecha" proceeds instead of looping. Lives
# here (not in bot/) so both the bot clarification merge and the api dispatcher
# can import it without the bot→api layering being inverted.
GOAL_NO_DATE_SENTINEL = "__no_date__"


class Intent(str, Enum):
    LOG_EXPENSE = "log_expense"
    LOG_INCOME = "log_income"
    LOG_TRANSFER = "log_transfer"
    CREATE_GOAL = "create_goal"
    CREATE_INCOME = "create_income"
    CREATE_BILL = "create_bill"
    CREATE_DEBT = "create_debt"
    CREATE_CARD = "create_card"
    ATTACH_EXPENSE = "attach_expense"
    SET_BALANCE = "set_balance"
    REALLOCATE_ENVELOPE = "reallocate_envelope"
    MARK_BILL_PAID = "mark_bill_paid"
    RECORD_DEBT_PAYMENT = "record_debt_payment"
    QUERY = "query"
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

# Intents that route through the control dispatcher. Everything else that
# isn't QUERY is a write intent — the model_validator below derives the
# `dispatcher` tag from this partition (P10 B0.5 R4).
_CONTROL_INTENTS = frozenset(
    {Intent.CONFIRM_YES, Intent.CONFIRM_NO, Intent.UNDO, Intent.HELP, Intent.UNKNOWN}
)


class ExtractionResult(BaseModel):
    """Canonical structured output of the LLM extractor.

    Every field is optional EXCEPT `intent` and `confidence` so the model is
    free to admit "I don't know" by leaving a value null rather than
    hallucinating one. The dispatcher's clarification logic assumes the
    model respects that — the system prompt reinforces it.
    """

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    dispatcher: Literal["write", "query", "control"]
    amount: Optional[Decimal] = Field(default=None)
    currency: Optional[str] = Field(default=None)
    merchant: Optional[str] = Field(default=None, max_length=255)
    category_hint: Optional[str] = Field(default=None, max_length=100)
    account_hint: Optional[str] = Field(default=None, max_length=100)
    # Phase 7b — transfers between the user's own accounts (intent=log_transfer).
    # A credit-card payment IS a transfer (source account → credit account);
    # amount/currency/occurred_at_hint are reused. Either hint may be null —
    # the dispatcher clarifies the missing side deterministically.
    transfer_from_hint: Optional[str] = Field(default=None, max_length=100)
    transfer_to_hint: Optional[str] = Field(default=None, max_length=100)
    # SINPE Móvil / bank-transfer RECEIPT parties (a 3rd-party comprobante, e.g.
    # an uploaded photo). The LLM extracts the RAW parties; a deterministic rule
    # (api/services/dispatch/transfer_direction.py) derives income/expense/
    # internal by comparing them to the user's identity — the LLM must NOT decide
    # the direction. is_transfer_receipt=True ONLY for such a receipt naming a
    # sender and/or recipient (NEVER for first-person "me pagaron"/"pagué").
    is_transfer_receipt: bool = Field(default=False)
    sender_name: Optional[str] = Field(default=None, max_length=160)
    sender_phone: Optional[str] = Field(default=None, max_length=32)
    recipient_name: Optional[str] = Field(default=None, max_length=160)
    recipient_phone: Optional[str] = Field(default=None, max_length=32)
    occurred_at_hint: Optional[str] = Field(default=None, max_length=100)
    query_window: Optional[str] = Field(default=None, max_length=32)
    # Phase 6f — conversational goal creation (intent=create_goal).
    goal_name: Optional[str] = Field(default=None, max_length=255)
    goal_target_amount: Optional[Decimal] = Field(default=None)
    goal_target_date: Optional[str] = Field(default=None, max_length=64)
    # SMART goals: set by the deterministic infeasible decision-point (the user
    # tapped "Crear así"), NOT by the LLM — it tells the dispatcher to skip the
    # feasibility re-gate and propose the goal as-is. The extractor never emits
    # this; the system prompt does not mention it.
    goal_force_create: bool = Field(default=False)
    # Phase 6f — conversational recurring-income creation (intent=create_income).
    # amount + currency are reused for the income amount. income_type defaults
    # to salary server-side; derived CR cycles are NOT creatable here.
    income_type: Optional[str] = Field(default=None, max_length=32)
    income_frequency: Optional[str] = Field(default=None, max_length=16)
    income_next_date: Optional[str] = Field(default=None, max_length=64)
    # Phase 6f — conversational recurring-bill creation (intent=create_bill).
    # amount (amount_expected), currency, category_hint (category) are reused.
    bill_name: Optional[str] = Field(default=None, max_length=255)
    bill_frequency: Optional[str] = Field(default=None, max_length=16)
    bill_day_of_month: Optional[int] = Field(default=None)
    # Phase 6f — conversational debt creation (intent=create_debt). Extraction
    # is LIGHT: just enough to pre-fill the native form, which gathers the rest
    # (the chat never collects all six DebtCreate fields). currency is reused.
    # debt_interest_rate is a PERCENT (18 → "18%"); the form converts to the
    # 0–1 fraction DebtCreate stores.
    debt_name: Optional[str] = Field(default=None, max_length=255)
    debt_principal: Optional[Decimal] = Field(default=None)
    debt_interest_rate: Optional[Decimal] = Field(default=None)
    debt_term_months: Optional[int] = Field(default=None)
    debt_lender: Optional[str] = Field(default=None, max_length=100)
    # Phase 7b — credit-card account creation (intent=create_card). LIGHT
    # extraction like debt: chat hands off to the native card form
    # (open_screen card_create); the form + PDF upload gather the terms.
    card_name: Optional[str] = Field(default=None, max_length=255)
    card_issuer: Optional[str] = Field(default=None, max_length=100)
    card_limit: Optional[Decimal] = Field(default=None)
    # Fixed-expense attachment (intent=attach_expense): attach an EXISTING
    # recurring bill / debt to an envelope. expense_hint = the obligation name
    # ("ICE", "préstamo del carro"); envelope_hint = the sobre name ("Servicios").
    expense_hint: Optional[str] = Field(default=None, max_length=255)
    envelope_hint: Optional[str] = Field(default=None, max_length=120)
    # Phase 8 B4 — reallocation between envelopes (intent=reallocate_envelope).
    # A COMMAND to move budget from one sobre to another ("movéme 15 mil de
    # Ahorro a Gustos"); amount is reused. A QUESTION asking where to pull from
    # is a query (the read-only suggest_reallocation_candidates answers).
    reallocate_from_hint: Optional[str] = Field(default=None, max_length=120)
    reallocate_to_hint: Optional[str] = Field(default=None, max_length=120)
    # Flexible payment dates — record a payment to an EXISTING recurring bill
    # (intent=mark_bill_paid) or debt (intent=record_debt_payment). The target is
    # a free-form name hint; occurred_at_hint carries the payment date (the
    # dispatcher defaults it to today). amount = monto pagado — optional for a
    # bill (falls back to the bill's expected amount), required for a debt.
    bill_target_hint: Optional[str] = Field(default=None, max_length=255)
    debt_target_hint: Optional[str] = Field(default=None, max_length=255)
    confidence: float = Field(..., ge=0.0, le=1.0)
    raw_notes: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _dispatcher_follows_intent(self) -> "ExtractionResult":
        """P10 B0.5 (R4) — enforce intent↔dispatcher consistency.

        The 2026-07-02 compound-message bug: a mixed write+analytical message
        ("Ganó 2000000 al mes … dime si me alcanza") could come back as
        `intent=query, dispatcher="write"`, reach the write dispatcher, and
        blow up on its Intent.QUERY guard. The intent is the semantic signal;
        the dispatcher tag is routing — so the routing is DERIVED from the
        intent, never trusted independently. Coerce (don't reject): losing a
        turn to a ValidationError would be worse than fixing the tag.
        """
        if self.intent is Intent.QUERY:
            expected = "query"
        elif self.intent in _CONTROL_INTENTS:
            expected = "control"
        else:
            expected = "write"
        if self.dispatcher != expected:
            self.dispatcher = expected
        return self

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

    @field_validator(
        "category_hint", "account_hint", "merchant", "goal_name", "bill_name",
        "debt_name", "debt_lender", "expense_hint", "envelope_hint",
        "transfer_from_hint", "transfer_to_hint", "card_name", "card_issuer",
        "sender_name", "recipient_name",
        "reallocate_from_hint", "reallocate_to_hint",
        "bill_target_hint", "debt_target_hint",
    )
    @classmethod
    def _normalize_strings(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = " ".join(v.split())  # collapse inner whitespace
        return v or None

    @field_validator("income_type")
    @classmethod
    def _valid_income_type(cls, v: Optional[str]) -> Optional[str]:
        # Only directly-creatable types; aguinaldo/salario_escolar derive from a
        # salary (the Incomes screen's one-tap action), never created here.
        if v is None:
            return None
        v = v.strip().lower()
        return v if v in {"salary", "freelance", "other"} else None

    @field_validator("income_frequency")
    @classmethod
    def _valid_income_frequency(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().lower()
        return v if v in {"weekly", "biweekly", "monthly", "annual"} else None

    @field_validator("bill_frequency")
    @classmethod
    def _valid_bill_frequency(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().lower()
        # All BillFrequency members except 'custom' (RRULE is not conversational).
        return v if v in {
            "weekly", "biweekly", "monthly", "bimonthly",
            "quarterly", "semiannual", "annual",
        } else None

    @field_validator("bill_day_of_month")
    @classmethod
    def _valid_day_of_month(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return None
        return v if 1 <= v <= 31 else None

    @field_validator("amount", "goal_target_amount", "debt_principal", "card_limit")
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

    @field_validator("debt_interest_rate")
    @classmethod
    def _valid_interest_pct(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        # Extracted as a PERCENT (the form converts to a 0–1 fraction). A
        # plausible CR loan rate is single/low-double digits; anything outside
        # (0, 100] is dropped so the form asks rather than pre-filling garbage.
        if v is None:
            return None
        if v <= 0 or v > 100:
            return None
        return v

    @field_validator("debt_term_months")
    @classmethod
    def _valid_term_months(cls, v: Optional[int]) -> Optional[int]:
        # 1 month .. 50 years. Out-of-range → None (form gathers it).
        if v is None:
            return None
        return v if 1 <= v <= 600 else None

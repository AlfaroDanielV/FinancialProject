"""Bank-statement reconciliation schemas (PDF → balance anchor).

A user uploads a bank statement PDF; `extract_statement` (LLM `document` block,
Haiku→Sonnet 0.65) reads the bank, the fecha de corte, and every product on the
statement with its `SALDO AL CORTE` / saldo adeudado. The deterministic
reconcile path then APPENDS a `source="statement"` anchor per deposit/credit
account (or sets a loan's `current_balance`) at the corte date — reusing the
balance-anchor engine shipped 2026-06-19.

"LLM extracts; rules decide": this module is the LLM's PROPOSED structure; the
deterministic `reconcile_products` is the only write path. The `suggested_*`
fields are filled by the server's fuzzy matcher AFTER extraction — the LLM never
sets them.
"""
import uuid
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

# deposit  → cuenta a la vista / ahorro / corriente → anchor at corte
# credit   → tarjeta de crédito                      → anchor (owed = negative)
# loan     → préstamo / crédito                       → Debt.current_balance
StatementKind = Literal["deposit", "credit", "loan"]


class StatementProduct(BaseModel):
    """One product line read off a statement. `closing_balance` is the printed
    SALDO AL CORTE / saldo adeudado as a POSITIVE magnitude (the reconcile path
    applies the per-kind sign: a credit card's owed balance anchors negative).

    `suggested_account_id` / `suggested_debt_id` are filled by the server after
    a fuzzy match against the user's records — the LLM leaves them null.
    """

    label: Optional[str] = Field(None, max_length=120)
    account_last4: Optional[str] = Field(None, max_length=8)
    iban: Optional[str] = Field(None, max_length=40)
    currency: str = Field("CRC", max_length=3)
    kind: StatementKind
    closing_balance: float
    suggested_account_id: Optional[uuid.UUID] = None
    suggested_debt_id: Optional[uuid.UUID] = None


class StatementExtraction(BaseModel):
    """Returned by `POST /accounts/parse-statement` to drive the native form /
    the chat proposal. Does NOT write anything."""

    bank: Optional[str] = Field(None, max_length=80)
    corte_date: Optional[date] = None
    products: list[StatementProduct] = Field(default_factory=list)
    confidence: float = Field(..., ge=0, le=1)


class StatementReconcileItem(BaseModel):
    model_config = {"extra": "forbid"}

    kind: StatementKind
    # account_id for deposit/credit; debt_id for loan.
    target_id: uuid.UUID
    # The printed balance as a POSITIVE magnitude; the service applies the sign.
    closing_balance: Decimal = Field(..., max_digits=14, decimal_places=2)


class StatementReconcileRequest(BaseModel):
    model_config = {"extra": "forbid"}

    corte_date: date
    items: list[StatementReconcileItem] = Field(..., min_length=1)


class StatementReconcileResultItem(BaseModel):
    target_id: uuid.UUID
    kind: StatementKind
    name: str
    delta: Optional[Decimal] = None  # account anchors only
    anchor_id: Optional[uuid.UUID] = None  # account anchors only
    new_balance: Decimal  # account: anchored balance; loan: current_balance


class StatementReconcileResponse(BaseModel):
    corte_date: date
    results: list[StatementReconcileResultItem]

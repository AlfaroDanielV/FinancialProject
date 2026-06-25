"""Bank-statement reconciliation schemas (PDF → balance anchor).

A user uploads a bank statement PDF; `extract_statement` (LLM `document` block,
Haiku→Sonnet 0.65) reads it into the **semantic primitives** below — the SAME
shape for every bank and account type: account identity + instruments + currency
legs + sign-tagged flows + role-tagged closing balances. The LLM tags
`account_type` / `direction` / `role` / `contingent` and copies amounts verbatim;
it computes nothing.

`build_reconcile_plan` (deterministic, `statement_normalize.py`) then turns that
rich extraction into a `ReconcilePlan` — one `LegPlan` per (account × currency
leg) — applying the per-type policy table, the universal conservation check, and
ledger resolution. The plan COLLAPSES to the legacy per-target
`StatementReconcileItem` list before the write, so `reconcile_products`
(`statements.py`) — the only writer — and `apply_anchor` stay unchanged.

"LLM extracts; rules decide": these models are the LLM's PROPOSED structure;
conservation is the deterministic validator of the LLM's role tag.
"""
import uuid
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

# ── Legacy collapse kind (the write path branches on this) ────────────────────
# deposit  → cuenta a la vista / ahorro / corriente / inversión → anchor at corte
# credit   → tarjeta de crédito                                  → anchor (owed = negative)
# loan     → préstamo / línea de crédito                          → Debt.current_balance
StatementKind = Literal["deposit", "credit", "loan"]

# ── Semantic primitives (V2 — type-agnostic, identical for every bank) ────────
AccountTypeSem = Literal[
    "checking", "savings", "credit_card", "loan", "line_of_credit", "investment"
]
# Asset orientation: inflow = +, outflow = −. The system applies the STORAGE
# sign per the policy table (a credit card's owed balance anchors negative).
FlowDirection = Literal["inflow", "outflow"]
BalanceRole = Literal[
    "closing",
    "available",
    "financed",
    "payoff",
    "minimum",
    "principal_outstanding",
    "market_value",
    "previous",
]


class StmtIdentifier(BaseModel):
    kind: Literal["iban", "account_number", "masked_pan", "last4", "product_code"]
    value: str = Field(..., max_length=64)


class StmtInstrument(BaseModel):
    """A physical card / PAN under an account. Attribution ONLY — never carries a
    balance (the model has no amount field on purpose: a supplementary card's
    spend already rolls into the account's leg balance). Collapsing instruments
    into the owner account is the Bug-2 fix."""

    masked_pan: Optional[str] = Field(None, max_length=32)
    holder_name: Optional[str] = Field(None, max_length=120)
    is_supplementary: bool = False


class StmtFlow(BaseModel):
    amount: float  # verbatim POSITIVE magnitude; `direction` carries the sign
    direction: FlowDirection
    # True ONLY for amounts not yet in the pay-in-full balance — typically the
    # current-period interest charged only if you don't pay de contado. Excluded
    # from the conservation sum.
    contingent: bool = False
    label_raw: Optional[str] = Field(None, max_length=120)


class StmtBalanceCandidate(BaseModel):
    amount: float  # verbatim
    role: BalanceRole


class StmtCurrencyLeg(BaseModel):
    currency: str = Field(..., max_length=3)
    opening_balance: Optional[float] = None  # verbatim "saldo anterior"
    flows: list[StmtFlow] = Field(default_factory=list)
    closing_candidates: list[StmtBalanceCandidate] = Field(default_factory=list)


class StmtAccount(BaseModel):
    account_type: AccountTypeSem
    issuer: Optional[str] = Field(None, max_length=80)
    product_name: Optional[str] = Field(None, max_length=120)
    identifiers: list[StmtIdentifier] = Field(default_factory=list)
    instruments: list[StmtInstrument] = Field(default_factory=list)
    currency_legs: list[StmtCurrencyLeg] = Field(default_factory=list)


class StatementExtractionV2(BaseModel):
    """The rich semantic extraction returned by `extract_statement`. Internal —
    `build_reconcile_plan` consumes it; the parse endpoint projects it to the
    back-compat `StatementExtraction` for the form/chat."""

    bank: Optional[str] = Field(None, max_length=80)
    period_start: Optional[date] = None
    period_end: Optional[date] = None  # == fecha de corte (the anchor date)
    due_date: Optional[date] = None
    accounts: list[StmtAccount] = Field(default_factory=list)
    confidence: float = Field(..., ge=0, le=1)


# ── Reconcile plan (deterministic build_reconcile_plan output) ────────────────


class ResolvedTarget(BaseModel):
    kind: StatementKind
    target_id: Optional[uuid.UUID] = None  # None → the user picks
    target_name: Optional[str] = None
    confidence: float = 0.0
    candidate_ids: list[uuid.UUID] = Field(default_factory=list)


class LegPlan(BaseModel):
    account_type: AccountTypeSem
    currency: str
    label: Optional[str] = None
    last4: Optional[str] = None
    iban: Optional[str] = None
    account_number: Optional[str] = None
    target_role: Optional[BalanceRole] = None
    sign: Literal["asset", "liability"]
    # Positive magnitude written as `closing_balance`; the writer applies the
    # storage sign. None when no role candidate matched → needs_review.
    reconcile_value: Optional[Decimal] = None
    opening: Optional[Decimal] = None
    conservation_expected: Optional[Decimal] = None  # |opening + Σ non-contingent|
    # True = verified, False = mismatch (→ needs_review), None = unverifiable.
    conservation_ok: Optional[bool] = None
    conservation_delta: Optional[Decimal] = None
    needs_review: bool = False
    # conservation_mismatch | no_target_role | unknown_account_type
    # | unresolved_target | ambiguous_target | None
    review_reason: Optional[str] = None
    attributed_instruments: list[str] = Field(default_factory=list)
    resolution: ResolvedTarget


class ReconcilePlan(BaseModel):
    bank: Optional[str] = None
    corte_date: Optional[date] = None
    legs: list[LegPlan] = Field(default_factory=list)


# ── Back-compat projection (the parse-endpoint response the form/chat read) ───


class StatementProduct(BaseModel):
    """One collapsed (account × currency leg), projected from a `LegPlan`. The
    enrichment fields (`conservation_ok`/`needs_review`/…) are additive so the
    native form can render an honest proposal; old clients ignore them.

    `closing_balance` is the role-tagged target magnitude (POSITIVE); the
    reconcile path applies the per-kind sign.
    """

    label: Optional[str] = Field(None, max_length=120)
    account_last4: Optional[str] = Field(None, max_length=8)
    iban: Optional[str] = Field(None, max_length=40)
    account_number: Optional[str] = Field(None, max_length=64)
    currency: str = Field("CRC", max_length=3)
    kind: StatementKind
    # None when no role candidate resolved (a review row that must NOT anchor a
    # fabricated 0 if toggled on).
    closing_balance: Optional[float] = None
    suggested_account_id: Optional[uuid.UUID] = None
    suggested_debt_id: Optional[uuid.UUID] = None
    # Enrichment (additive):
    conservation_ok: Optional[bool] = None
    needs_review: bool = False
    review_reason: Optional[str] = None
    target_role: Optional[BalanceRole] = None
    attributed_instruments: list[str] = Field(default_factory=list)
    candidate_ids: list[uuid.UUID] = Field(default_factory=list)
    # 1.0 = a confident identity match; gates native self-stamping.
    match_confidence: float = 0.0


class StatementExtraction(BaseModel):
    """Returned by `POST /accounts/parse-statement` to drive the native form /
    the chat proposal. Projected from the `ReconcilePlan` (one product per leg).
    Does NOT write anything."""

    bank: Optional[str] = Field(None, max_length=80)
    corte_date: Optional[date] = None
    products: list[StatementProduct] = Field(default_factory=list)
    confidence: float = Field(..., ge=0, le=1)


# ── Write payload (the deterministic reconcile path consumes this) ────────────


class StatementReconcileItem(BaseModel):
    model_config = {"extra": "forbid"}

    kind: StatementKind
    # account_id for deposit/credit; debt_id for loan.
    target_id: uuid.UUID
    # The role-tagged target balance as a POSITIVE magnitude; the service applies
    # the sign.
    closing_balance: Decimal = Field(..., max_digits=14, decimal_places=2)
    # Optional currency guard — when present the writer rejects a leg whose
    # currency ≠ the target's (fixes the silent USD-onto-CRC bug). The new flows
    # always set it; the legacy chat payload may omit it.
    currency: Optional[str] = Field(None, max_length=3)
    # Optional identity hints — the writer self-stamps these onto the resolved
    # account/debt's NULL identity columns so the NEXT statement matches
    # deterministically (fill-if-null, never clobber).
    iban: Optional[str] = Field(None, max_length=40)
    account_number: Optional[str] = Field(None, max_length=64)
    last4: Optional[str] = Field(None, max_length=8)
    # Provenance echoed back in the result (defaults keep old callers valid).
    conservation_ok: Optional[bool] = None
    needs_review: bool = False


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
    conservation_ok: Optional[bool] = None
    needs_review: bool = False


class StatementReconcileResponse(BaseModel):
    corte_date: date
    results: list[StatementReconcileResultItem]
